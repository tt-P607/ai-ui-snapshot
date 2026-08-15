"""截图业务服务：DeepSeek 提问 / 直接截图 / 直接取分享链接。

对外暴露三个解耦入口，均用共享的任务级浏览器会话（复用 bot 登录态）驱动
真实 DeepSeek 网页，返回结构化结果 :class:`AskResult`：
- :func:`ask_deepseek`：真实提问，按 output_format 返回回复文本（auto）或
  当前对话界面截图（snapshot），供快捷命令使用。
- :func:`capture_snapshot`：直接截取当前/指定对话界面，不提问、不设模式。
- :func:`create_share`：直接获取当前/指定对话的分享链接，不提问、不设模式。

连续对话由同 stream_id 复用同一浏览器页面保证；会话保活由 busy 计数与
轮询 touch 共同保障。三个入口共用 :func:`_locate_conversation` 完成对话
定位（空=沿用当前 / 精确标题=进入 / __new__=新建）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.media_api import get_media_info

from .browser_actions import BrowserActions
from .browser_session import BrowserSession, get_manager
from .deepseek_constants import SEARCH_TOGGLE_NAME, THINK_TOGGLE_NAME

logger = get_logger("ai_ui_snapshot.service")


@dataclass(slots=True)
class AskResult:
    """一次 DeepSeek 提问的完整结果（统一入口返回值）。

    Attributes:
        ok: 是否成功。
        error: 失败时的错误信息（ok=False 时非空）。
        reply: 回复正文（按 return_scope 取 last 最新回复 / full 整段对话）。
        data_uri: 截图 data URI 列表（超长对话按高度分片，多张按顺序排列；
            capture_snapshot / ask_deepseek(output_format=snapshot) 且截图
            成功时非空）。
        share_url: 分享链接（create_share / ask_deepseek(output_format=
            share_link，兼容保留) 且成功时非空）。
        conversation: 当前活跃对话标题（DeepSeek 自动生成，供上层返回给
            LLM 记住对话身份；未取到时为空字符串）。
        model_name: 回复来源标识。
        upload: 上传说明（附加了图片/文件时非空）。
    """

    ok: bool = False
    error: str = ""
    reply: str = ""
    data_uri: list[str] = field(default_factory=list)
    share_url: str = ""
    conversation: str = ""
    model_name: str = "deepseek.com"
    upload: str = ""


async def resolve_media_path(media_id: str) -> str | None:
    """通过框架媒体缓存，将 media_id 解析为本地文件路径。

    用户发过的图片在框架中以 media_id（图片哈希）标识并落盘缓存，
    ``media_api.get_media_info`` 返回的记录含 ``path`` 字段。

    Args:
        media_id: 聊天图片占位符中的 media_id。

    Returns:
        str: 本地文件路径；未找到或记录无路径时返回 None。
    """
    info = await get_media_info(media_id)
    if not info:
        return None
    path = info.get("path")
    return str(path) if path else None


async def _locate_conversation(
    actions: BrowserActions,
    session: BrowserSession,
    conversation: str,
    *,
    new_chat: bool = False,
    lock_mode: bool = False,
) -> str | None:
    """定位目标对话：空=沿用当前 / 精确标题=进入 / __new__=新建。

    提问场景（lock_mode=True）：进入历史会话后锁定其原有模式，标题未命中
    历史会话时新建对话；截图/分享场景（lock_mode=False）：仅进入会话、
    不设模式不锁模式，标题未命中时返回错误（无可截取/分享内容）。

    Args:
        actions: DeepSeek 页面动作封装。
        session: 当前 stream 的浏览器会话（模式锁容器）。
        conversation: 对话定位方式。
        new_chat: 旧参数，等价 conversation="__new__"（保留兼容）。
        lock_mode: 进入历史会话后是否锁定其原有模式。

    Returns:
        str | None: 成功返回 None；失败返回错误信息。
    """
    want_conversation = (conversation or "").strip()
    if new_chat:
        want_conversation = "__new__"

    if want_conversation == "__new__":
        # 强制新建：仅清当前对话锁（保留其他历史对话的锁），等待新对话稳定
        session.clear_mode(session.active_conversation)
        await actions.new_chat()
        await actions.page.wait_for_timeout(2500)
        return None
    if not want_conversation:
        return None
    listed = await actions.list_conversations()
    if want_conversation not in listed:
        # 提问场景未命中则新建（DeepSeek 自动命名，不绑定名称）；
        # 截图/分享场景未命中无可截取内容，直接报错
        if lock_mode:
            await actions.new_chat()
            await actions.page.wait_for_timeout(2500)
            return None
        return f"未找到历史会话: {want_conversation}"
    ok = await actions.open_conversation(want_conversation)
    if not ok:
        return f"进入历史会话失败: {want_conversation}"
    if lock_mode:
        shown = await actions.get_mode()
        cid = await actions.get_active_conversation_id()
        if shown and cid:
            session.lock_conversation_mode(cid, shown, want_conversation)
    return None


async def ask_deepseek(
    question: str,
    *,
    stream_id: str = "",
    timeout_s: int = 240,
    mode: str = "",
    deepthink: bool | None = True,
    search: bool | None = True,
    new_chat: bool = False,
    conversation: str = "",
    local_path: str | None = None,
    output_format: str = "auto",
    think: str = "collapse",
    sidebar: str = "auto",
    return_scope: str = "last",
    upload_max_size_mb: float = 10.0,
    upload_allowed_extensions: str = "png,jpg,jpeg,webp,gif,bmp",
) -> AskResult:
    """统一入口：向 DeepSeek 真实提问，按输出形式返回结果。

    完整链路：获取（或创建）共享浏览器会话 → busy 加锁（会话保活）→
    按 conversation 路由到指定/新建对话 → 校验会话模式锁死与能力边界 →
    设模式/开关 → （可选）上传 → 提问 → 等待回复 → 按 output_format
    处理 → 读取当前活跃对话标题 → release 解锁。连续对话由同 stream 复用
    同一页面保证；每个对话模式一经选定即锁定，不可切换，换模式须开新对话。
    output_format 的 share_link 为兼容保留（新代码请用 create_share）。

    Args:
        question: 用户问题（output_format=share_link 时无需提问，可为空）。
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        timeout_s: 等待 AI 回复超时秒数。
        mode: 对话模式（快速模式/专家模式/识图模式，默认快速模式）。
        deepthink: 深度思考开关（True/False/None；默认 True）。
        search: 智能搜索开关（True/False/None；默认 True）。
        new_chat: 是否先开新对话再提问（旧参数，等价 conversation="__new__"）。
        conversation: 对话定位方式：空（默认）沿用当前对话；精确标题进入
            该历史会话（未命中则新建）；"__new__" 强制新建。
        local_path: 已解析好的上传文件路径（None 表示不上传）。
        output_format: 输出形式（auto 纯文本返回 / snapshot 截图 /
            share_link 分享链接）。
        think: 思考过程块展开方式（collapse 折叠隐藏，默认 / auto 保持现状 /
            expand 展开 / reveal 被折叠时展开）。
        sidebar: 侧边栏显示方式（auto 保持现状 / show 展开 / hide 收起）。
        return_scope: 信息返回范围（last 最新回复，默认 / full 整段对话）。
        upload_max_size_mb: 上传文件大小上限（MB）。
        upload_allowed_extensions: 允许上传的扩展名（逗号分隔，小写）。

    Returns:
        AskResult: 结构化结果（成功时 ok=True，含对应输出字段）。
    """
    # share_link 仅获取当前对话的分享链接、无需向 DeepSeek 提问，
    # 故 question 可为空；其余输出形式必须提供有效问题。
    if (not question or not question.strip()) and output_format != "share_link":
        return AskResult(ok=False, error="问题不能为空")

    # 1. 获取共享浏览器会话（首次自动创建）
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key)
        session.hold()
        manager.touch(stream_key)
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        # touch_cb 用于 wait_reply_done 长等待中刷新会话活动时间（会话保活）
        actions = BrowserActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key),
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )

        # share_link 短路：不设模式、不问 DeepSeek，直接取当前对话分享链接。
        if output_format == "share_link":
            share_url = await actions.create_share_link()
            if not share_url:
                return AskResult(ok=False, error="生成分享链接失败")
            current_id = await actions.get_active_conversation_id()
            current_title = await actions.get_active_conversation_title()
            session.set_active_conversation(current_id, current_title)
            return AskResult(
                ok=True,
                share_url=share_url,
                conversation=current_title,
                upload=_upload_notice(local_path),
            )

        # 2. conversation 路由：空=沿用当前 / 精确标题=进入（未命中新建）/
        #    "__new__"=强制新建。new_chat 兼容映射为 "__new__"。
        err = await _locate_conversation(
            actions, session, conversation, new_chat=new_chat, lock_mode=True
        )
        if err:
            return AskResult(ok=False, error=err)

        # 3. 模式解析：mode 为空时沿用当前/锁定模式
        locked = session.get_locked_mode()
        if mode:
            normalized = mode.strip()
            if locked is not None and locked != normalized:
                # 对话模式已锁定，不可切换：提示开新对话
                return AskResult(
                    ok=False,
                    error=f"当前对话已锁定为「{locked}」，不能切换为「{normalized}」。如需换模式请开新对话（conversation=__new__）。",
                )
        else:
            normalized = locked or "快速模式"
        if locked is None:
            # 首次提问：锁定当前对话模式。历史会话页面无模式选择器，
            # set_mode 可能失败；此时若能读取到页面展示的模式则沿用（降级继续）。
            ok, msg = await actions.set_mode(normalized)
            if not ok:
                shown = await actions.get_mode()
                if shown is None:
                    return AskResult(ok=False, error=f"设置对话模式失败: {msg}")
                normalized = shown
            session.lock_mode(normalized)

        # 3. 应用开关设置（提问前）
        if deepthink is not None:
            ok, msg = await actions.set_toggle(THINK_TOGGLE_NAME, deepthink)
            if not ok:
                return AskResult(ok=False, error=f"设置深度思考失败: {msg}")
        if search is not None:
            ok, msg = await actions.set_toggle(SEARCH_TOGGLE_NAME, search)
            if not ok:
                # 专家/识图模式不支持联网搜索，降级为不开启并继续
                logger.warning(f"设置智能搜索失败（可能当前模式不支持）: {msg}")

        # 4. 能力边界校验：专家模式不支持上传
        if local_path:
            current_mode = await actions.get_mode()
            if current_mode == "专家模式":
                return AskResult(ok=False, error="专家模式不支持上传图片/文件，请改用快速模式或识图模式")
            ok, msg = await actions.upload_file(
                local_path,
                max_size_mb=upload_max_size_mb,
                allowed_extensions=upload_allowed_extensions,
            )
            if not ok:
                return AskResult(ok=False, error=msg)

        # 5. 提问并等待回复（返回干净的最新一条 AI 回复）
        try:
            if not await actions.type_text(question.strip()):
                return AskResult(ok=False, error="向网页输入框输入问题失败")
            if not await actions.press("Enter"):
                return AskResult(ok=False, error="发送问题失败")
            done, last_reply = await actions.wait_reply_done(timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 - 网页提问失败
            logger.error(f"网页提问失败: {exc}", exc_info=True)
            return AskResult(ok=False, error=f"网页提问失败: {exc}")
        if not done:
            return AskResult(ok=False, error="等待 AI 回复超时")

        # 6. 按 return_scope 取信息返回文本（last=最新回复 / full=整段对话）
        if return_scope == "full":
            content = await actions.get_conversation_text(scope="full")
        else:
            content = last_reply

        # 7. 读取当前活跃会话 ID 与标题并同步到会话（ID 作模式锁 key、标题作展示）
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.get_active_conversation_title()
        session.set_active_conversation(current_id, current_title)

        # 8. 仅 snapshot 输出形式截图（auto 只返回文本，截图由解耦后的
        #    capture_snapshot / deepseek_snapshot 入口负责）
        if output_format == "snapshot":
            data_uris = await actions.screenshot("conversation", think=think, sidebar=sidebar)
            if not data_uris:
                return AskResult(
                    ok=False,
                    error="对话区截图失败",
                    reply=content,
                    conversation=current_title,
                )
            return AskResult(
                ok=True,
                reply=content,
                data_uri=data_uris,
                conversation=current_title,
                upload=_upload_notice(local_path),
            )
        return AskResult(
            ok=True,
            reply=content,
            conversation=current_title,
            upload=_upload_notice(local_path),
        )
    finally:
        session.release()


async def capture_snapshot(
    *,
    stream_id: str = "",
    conversation: str = "",
    think: str = "collapse",
    sidebar: str = "auto",
) -> AskResult:
    """直接截取当前/指定 DeepSeek 对话界面，不提问、不设模式。

    定位会话（空=沿用当前 / 精确标题=进入该历史会话 / __new__=新建）后
    直接截图；进入历史会话不触发模式锁定或开关设置，避免"必须先选定形式"。

    Args:
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        conversation: 对话定位方式。
        think: 思考过程块展开方式（collapse 默认 / auto / expand / reveal）。
        sidebar: 侧边栏显示方式（auto 默认 / show / hide）。

    Returns:
        AskResult: 成功时 ok=True 且 data_uri 含截图；失败时 ok=False。
    """
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key)
        session.hold()
        manager.touch(stream_key)
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = BrowserActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key),
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )
        err = await _locate_conversation(actions, session, conversation, lock_mode=False)
        if err:
            return AskResult(ok=False, error=err)
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.get_active_conversation_title()
        session.set_active_conversation(current_id, current_title)
        data_uris = await actions.screenshot("conversation", think=think, sidebar=sidebar)
        if not data_uris:
            return AskResult(ok=False, error="对话区截图失败", conversation=current_title)
        return AskResult(ok=True, data_uri=data_uris, conversation=current_title)
    finally:
        session.release()


async def create_share(
    *,
    stream_id: str = "",
    conversation: str = "",
) -> AskResult:
    """直接获取当前/指定 DeepSeek 对话的分享链接，不提问、不设模式。

    定位会话后调用 DeepSeek 官方分享功能生成公开链接；进入历史会话不触发
    模式锁定或开关设置。

    Args:
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        conversation: 对话定位方式。

    Returns:
        AskResult: 成功时 ok=True 且 share_url 非空；失败时 ok=False。
    """
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key)
        session.hold()
        manager.touch(stream_key)
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = BrowserActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key),
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )
        err = await _locate_conversation(actions, session, conversation, lock_mode=False)
        if err:
            return AskResult(ok=False, error=err)
        share_url = await actions.create_share_link()
        if not share_url:
            return AskResult(ok=False, error="生成分享链接失败")
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.get_active_conversation_title()
        session.set_active_conversation(current_id, current_title)
        return AskResult(ok=True, share_url=share_url, conversation=current_title)
    finally:
        session.release()


def _upload_notice(local_path: str | None) -> str:
    """生成上传说明文本。

    Args:
        local_path: 上传的本地文件路径（None 表示未上传）。

    Returns:
        str: 上传说明；未上传时返回空字符串。
    """
    return f"（已附带上传 {local_path}）" if local_path else ""


def strip_data_uri_prefix(data_uri: str) -> str:
    """剥离 ``data:`` URI 前缀，返回可直接发送的媒体数据。

    框架媒体数据统一以 ``base64|`` 前缀下发（见 ``normalize_base64``），
    平台适配器再将其转换为各自要求的格式（如 ``base64://``）后上传。
    ``data:image/png;base64,...`` 若原样传入，会被当作普通 base64 文本
    再次包裹前缀，形成双重前缀导致平台上传失败，故发送前需剥离 ``data:`` 头。

    Args:
        data_uri: data:image/png;base64,... 或任意数据字符串。

    Returns:
        str: 剥离 ``data:`` 前缀后的数据（非 ``data:`` 开头时原样返回）。
    """
    if data_uri.startswith("data:") and "," in data_uri:
        return data_uri.split(",", 1)[1]
    return data_uri
