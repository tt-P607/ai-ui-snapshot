"""DeepSeek 站点的提问、截图及分享服务。"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger

from ..base.browser_session import get_manager
from ..base.shared import AskResult, _locate_conversation, _upload_notice
from .actions import BrowserActions
from .constants import SEARCH_TOGGLE_NAME, THINK_TOGGLE_NAME

logger = get_logger("ai_ui_snapshot.service")


async def ask_deepseek(
    question: str,
    *,
    stream_id: str = "",
    timeout_s: int = 240,
    deepthink: bool | None = True,
    search: bool | None = True,
    new_chat: bool = False,
    conversation: str = "",
    local_path: str | None = None,
    output_format: str = "auto",
    think: str = "collapse",
    sidebar: str = "auto",
    return_scope: str = "last",
    scope: str = "viewport",
    rounds: int = 1,
    upload_max_size_mb: float = 10.0,
    upload_allowed_extensions: str | None = None,
) -> AskResult:
    """统一入口：向 DeepSeek 真实提问，按输出形式返回结果。

    完整链路：获取（或创建）共享浏览器会话 → busy 加锁（会话保活）→
    按 conversation 路由到指定/新建对话 → 设开关 → （可选）上传 → 提问 →
    等待回复 → 按 output_format 处理 → 读取当前活跃对话标题 → release 解锁。
    连续对话由同 stream 复用同一页面保证。
    output_format 的 share_link 亦可用 create_share 实现。

    Args:
        question: 用户问题（output_format=share_link 时无需提问，可为空）。
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        timeout_s: 等待 AI 回复超时秒数。
        deepthink: 深度思考开关（True/False/None；默认 True）。
        search: 智能搜索开关（True/False/None；默认 True）。
        new_chat: 是否先开新对话再提问（等价 conversation="__new__"）。
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
        upload_allowed_extensions: 允许上传的扩展名（逗号分隔，小写）；None 时
            按 DeepSeek 网页 accept 声明校验。

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
            decoration_theme=manager.page_theme,
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

        # 1b. 应用页面明暗主题（DeepSeek 主题经 localStorage + reload 生效，
        #     先应用再定位会话，避免 reload 重置已定位的会话）
        if manager.page_theme:
            try:
                await actions.set_theme(manager.page_theme)
            except Exception:  # noqa: BLE001 - 主题应用失败不阻塞提问
                logger.warning("应用 DeepSeek 页面主题失败，使用默认主题")

        # 2. conversation 路由：未显式指定对话标题时默认创建新对话；
        #    显式传精确标题则进入继续（未命中新建）。
        err = await _locate_conversation(
            actions,
            conversation,
            new_chat=new_chat,
            new_chat_on_miss=True,
            default_new=True,
            new_page_cb=(
                (lambda: manager.new_session_page(stream_key))
                if session.active_conversation
                else None
            ),
        )
        if err:
            return AskResult(ok=False, error=err)

        # 3. 应用开关设置（提问前）
        if deepthink is not None:
            ok, msg = await actions.set_toggle(THINK_TOGGLE_NAME, deepthink)
            if not ok:
                return AskResult(ok=False, error=f"设置深度思考失败: {msg}")
        if search is not None:
            ok, msg = await actions.set_toggle(SEARCH_TOGGLE_NAME, search)
            if not ok:
                logger.warning(f"设置智能搜索失败: {msg}")

        # 4. 上传附件（可选）
        if local_path:
            ok, msg = await actions.upload_file(
                local_path,
                max_size_mb=upload_max_size_mb,
                allowed_extensions=upload_allowed_extensions,
            )
            if not ok:
                return AskResult(ok=False, error=msg)

        # 5. 提问并等待回复（返回干净的最新一条 AI 回复）
        try:
            previous_reply = await actions.get_conversation_text(scope="last")
            if not await actions.type_text(question.strip()):
                return AskResult(ok=False, error="向网页输入框输入问题失败")
            if not await actions.press("Enter"):
                return AskResult(ok=False, error="发送问题失败")
            done, last_reply = await actions.wait_reply_done(
                timeout_s=timeout_s,
                previous_reply=previous_reply,
            )
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

        # 7. 读取当前活跃会话 ID 与标题并同步到会话（ID 作模式锁 key、标题作展示）。
        #    新建对话首条提问时标题由 AI 异步生成，先短暂等待确保返回时非空。
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.wait_conversation_title()
        session.set_active_conversation(current_id, current_title)

        # 8. 仅 snapshot 输出形式截图（auto 只返回文本，截图由解耦后的
        #    capture_snapshot / deepseek_snapshot 入口负责）
        if output_format == "snapshot":
            data_uris = await actions.screenshot(
                "conversation", think=think, sidebar=sidebar, scope=scope, rounds=rounds
            )
            if not data_uris:
                return AskResult(
                    ok=False,
                    error="对话区截图失败",
                    reply=content,
                    conversation=current_title,
                )
            meta = await actions.get_snapshot_meta(scope=scope, rounds=rounds)
            return AskResult(
                ok=True,
                reply=content,
                data_uri=data_uris,
                conversation=current_title,
                upload=_upload_notice(local_path),
                snapshot_meta=meta,
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
    scope: str = "viewport",
    rounds: int = 1,
) -> AskResult:
    """直接截取当前/指定 DeepSeek 对话界面，不提问。

    定位会话（空=沿用当前 / 精确标题=进入该历史会话 / __new__=新建）后
    直接截图；进入历史会话不触发开关设置。

    Args:
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        conversation: 对话定位方式。
        think: 思考过程块展开方式（collapse 默认 / auto / expand / reveal）。
        sidebar: 侧边栏显示方式（auto 默认 / show / hide）。
        scope: 截图范围模式（viewport 正常视窗 / rounds 倒序轮次 / full 整页长图）。
        rounds: 截取回复轮数（scope=rounds 时生效）。

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
        err = await _locate_conversation(actions, conversation)
        if err:
            return AskResult(ok=False, error=err)
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.get_active_conversation_title()
        session.set_active_conversation(current_id, current_title)
        data_uris = await actions.screenshot(
            "conversation", think=think, sidebar=sidebar, scope=scope, rounds=rounds
        )
        if not data_uris:
            return AskResult(ok=False, error="对话区截图失败", conversation=current_title)
        meta = await actions.get_snapshot_meta(scope=scope, rounds=rounds)
        return AskResult(
            ok=True, data_uri=data_uris, conversation=current_title, snapshot_meta=meta
        )
    finally:
        session.release()


async def create_share(
    *,
    stream_id: str = "",
    conversation: str = "",
) -> AskResult:
    """直接获取当前/指定 DeepSeek 对话的分享链接，不提问。

    定位会话后调用 DeepSeek 官方分享功能生成公开链接；进入历史会话不
    触发开关设置。

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
        err = await _locate_conversation(actions, conversation)
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
