"""截图业务服务：DeepSeek 提问 / 直接截图 / 直接取分享链接。

对外暴露三个解耦入口，均用共享的任务级浏览器会话（复用 bot 登录态）驱动
真实 DeepSeek 网页，返回结构化结果 :class:`AskResult`：
- :func:`ask_deepseek`：真实提问，按 output_format 返回回复文本（auto）或
  当前对话界面截图（snapshot），供快捷命令使用。
- :func:`capture_snapshot`：直接截取当前/指定对话界面，不提问。
- :func:`create_share`：直接获取当前/指定对话的分享链接，不提问。

连续对话由同 stream_id 复用同一浏览器页面保证；会话保活由 busy 计数与
轮询 touch 共同保障。三个入口共用 :func:`_locate_conversation` 完成对话
定位（空=沿用当前 / 精确标题=进入 / __new__=新建）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.media_api import get_media_info
from src.app.plugin_system.api import prompt_api

from .base.browser_session import get_manager
from .base.page_actions import PageActions
from .deepseek.actions import BrowserActions
from .deepseek.constants import SEARCH_TOGGLE_NAME, THINK_TOGGLE_NAME
from .doubao.actions import DoubaoActions
from .gemini.actions import GeminiActions

logger = get_logger("ai_ui_snapshot.service")

# 网页附件接受格式。DeepSeek 与 Gemini 支持范围不同，须分开：
# - DeepSeek：不写死名单 —— 网页 accept 声明了 900+ 种扩展（图片 + 文档 + 电子书
#   + 几乎全部代码/配置/标记语言），由网页自己维护；插件直接读 accept 作白名单，
#   避免手写名单与站点漂移（站点随时扩充，手写必滞后）。无音视频。
# - Gemini：真实"上传文件"accept 为 文档+代码+zip（无图片/音视频），但 set_input_files
#   注入可绕过 accept 白名单，图片/音频/视频均真实可用（全模态），音视频覆盖常见格式
GEMINI_ALLOWED_EXTENSIONS = (
    "png,jpg,jpeg,webp,gif,bmp,svg,ico,tif,tiff,avif,apng,jfif,psd,eps,"
    "md,txt,pdf,doc,docx,xls,xlsx,csv,ppt,pptx,json,py,zip,"
    # 常见音频
    "mp3,wav,ogg,flac,aac,aiff,m4a,opus,wma,amr,mid,oga,mp2,m4b,"
    # 常见视频
    "mp4,mov,m4v,webm,avi,mkv,mpg,mpeg,3gp,ts,flv,ogv,wmv,rmvb,m2ts"
)


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
            share_link) 且成功时非空）。
        conversation: 当前活跃对话标题（DeepSeek 自动生成，供上层返回给
            LLM 记住对话身份；未取到时为空字符串）。
        model_name: 回复来源标识。
        upload: 上传说明（附加了图片/文件时非空）。
        image_path: Gemini 对话中生成图片的本地下载路径（ask_gemini 检测到
            生成图并成功下载时非空；纯文本回复时为空）。
    """

    ok: bool = False
    error: str = ""
    reply: str = ""
    data_uri: list[str] = field(default_factory=list)
    share_url: str = ""
    conversation: str = ""
    model_name: str = "deepseek.com"
    upload: str = ""
    image_path: str = ""


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
    actions: PageActions,
    conversation: str,
    *,
    new_chat: bool = False,
    new_chat_on_miss: bool = False,
) -> str | None:
    """定位目标对话：空=沿用当前 / 精确标题=进入 / __new__=新建。

    三个站点共用此路由，语义统一：
    - 空：沿用当前对话
    - ``__new__``（或 new_chat=True）：新建对话
    - 精确标题：在历史会话中查找，命中则进入；未命中时按场景决定：

    提问场景（new_chat_on_miss=True）：标题未命中时新建对话继续提问；
    截图/分享场景（new_chat_on_miss=False）：标题未命中时返回错误
    （无可截取/分享的内容，不截空会话）。

    Args:
        actions: 站点页面动作封装（三站点接口一致）。
        conversation: 对话定位方式。
        new_chat: 等价 conversation="__new__"，两者取其一。
        new_chat_on_miss: 标题未命中历史会话时是否新建对话。

    Returns:
        str | None: 成功返回 None（已位于目标对话）；失败返回错误信息。
    """
    want_conversation = (conversation or "").strip()
    if new_chat:
        want_conversation = "__new__"

    if want_conversation == "__new__":
        # 强制新建：等待新对话稳定
        await actions.new_chat()
        await actions.page.wait_for_timeout(2500)
        return None
    if not want_conversation:
        return None
    listed = await actions.list_conversations()
    if want_conversation not in listed:
        # 提问场景未命中则新建（DeepSeek 自动命名，不绑定名称）；
        # 截图/分享场景未命中无可截取内容，直接报错
        if new_chat_on_miss:
            await actions.new_chat()
            await actions.page.wait_for_timeout(2500)
            return None
        return f"未找到历史会话: {want_conversation}"
    if not await actions.open_conversation(want_conversation):
        return f"进入历史会话失败: {want_conversation}"
    # 侧边栏点击后 URL/标题会立即更新，但会话内容仍需加载；稍等一拍
    # 再截图/提问，避免拍到尚未渲染完的对话。
    await actions.page.wait_for_timeout(1000)
    return None


# ----------------------------------------------------------------------
# 识图（供框架 on_media_recognize 事件链接管使用）
# ----------------------------------------------------------------------

#: 识图站点白名单（与 [sites] 各开关、各站点 ask 入口一一对应）
RECOGNIZE_SITES: tuple[str, ...] = ("deepseek", "doubao", "gemini")


async def framework_recognize_prompt(media_type: str) -> str:
    """读取框架的识图提示词（单一来源，插件不自带一份）。

    框架初始化媒体管理器时注册 ``media.image_recognition`` /
    ``media.emoji_recognition`` 两个提示词模板，内容取自 ``config/core.toml``
    的 ``[chat] image_recognition_prompt`` / ``emoji_recognition_prompt``
    （留空则用框架内置默认）；框架内置 VLM 引擎读的就是这两个模板。
    这里经插件规范的 ``prompt_api`` 读同一模板，因此网页识图与框架内置
    VLM 共用一套提示词——要改提示词只需改框架配置一处。

    Args:
        media_type: 媒体类型（image / emoji）。

    Returns:
        str: 构建好的提示词；模板未注册或渲染为空时返回空字符串，
        调用方据此交回框架内置 VLM（不自行拼一套提示词）。
    """
    name = "media.emoji_recognition" if media_type == "emoji" else "media.image_recognition"
    try:
        template = prompt_api.get_template(name)
    except Exception as exc:  # noqa: BLE001 - 提示词管理器不可用
        logger.warning(f"读取框架识图提示词失败（{name}）: {exc}")
        return ""
    if template is None:
        logger.warning(f"框架未注册识图提示词模板 {name}")
        return ""
    try:
        return (await template.build()).strip()
    except Exception as exc:  # noqa: BLE001 - 模板渲染失败
        logger.warning(f"渲染框架识图提示词失败（{name}）: {exc}")
        return ""


def sniff_image_suffix(raw: bytes) -> str:
    """按文件头判断图片扩展名（上传需要正确后缀才能通过站点校验）。

    Args:
        raw: 图片二进制内容（至少前 12 字节）。

    Returns:
        str: 扩展名（png/jpg/gif/webp/bmp）；无法识别时返回 jpg。
    """
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a"):
        return "gif"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "webp"
    if raw.startswith(b"BM"):
        return "bmp"
    return "jpg"


async def recognize_image_by_site(
    image_path: str,
    *,
    site: str = "deepseek",
    media_type: str = "image",
    stream_id: str = "",
    timeout_s: int = 120,
) -> str:
    """用指定站点的真实网页给一张本地图片生成文字描述。

    三个站点都走各自既有的 ``ask_*`` 入口（附件上传 + 提问 + 等回复），
    因此登录态、代理、无头等行为与手动提问完全一致；提示词取框架的识图
    提示词模板（见 :func:`framework_recognize_prompt`），与框架内置 VLM
    保持一致，不另立一套。

    对话定位：每次识图都开新会话（``conversation="__new__"``）。如果沿用带历史的
    会话，一旦图片没真正附上（站点静默丢掉附件），模型会对着历史里的旧图作答，
    产出一条对不上号的描述。描述会被框架按图哈希缓存并长期复用，因此新会话保证
    回复只对应当前这张图。

    Args:
        image_path: 本地图片路径。
        site: 识图站点（deepseek / doubao / gemini），非法值回退 deepseek。
        media_type: 媒体类型（image / emoji，决定用哪个框架提示词模板）。
        stream_id: 聊天流 ID（复用该流的浏览器会话）。
        timeout_s: 单张识图超时秒数。

    Returns:
        str: 描述文本；失败或返回空时为空字符串。
    """
    question = await framework_recognize_prompt(media_type)
    if not question:
        return ""
    target = (site or "").strip().lower()
    if target not in RECOGNIZE_SITES:
        logger.warning(f"未知识图站点 {site!r}，回退 deepseek")
        target = "deepseek"

    if target == "doubao":
        result = await ask_doubao(
            question,
            stream_id=stream_id,
            timeout_s=timeout_s,
            conversation="__new__",
            local_paths=[image_path],
            output_format="auto",
            return_scope="last",
        )
    elif target == "gemini":
        result = await ask_gemini(
            question,
            stream_id=stream_id,
            timeout_s=timeout_s,
            conversation="__new__",
            local_path=image_path,
            output_format="auto",
            return_scope="last",
        )
    else:
        # 识图不需要联网检索与深度思考，关掉可明显加快出结果
        result = await ask_deepseek(
            question,
            stream_id=stream_id,
            timeout_s=timeout_s,
            deepthink=False,
            search=False,
            conversation="__new__",
            local_path=image_path,
            output_format="auto",
            return_scope="last",
        )
    if not result.ok:
        logger.warning(f"{target} 识图失败: {result.error}")
        return ""
    return (result.reply or "").strip()


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

        # 2. conversation 路由：空=沿用当前 / 精确标题=进入（未命中新建）/
        #    "__new__"=强制新建。new_chat 兼容映射为 "__new__"。
        err = await _locate_conversation(
            actions, conversation, new_chat=new_chat, new_chat_on_miss=True
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

        # 7. 读取当前活跃会话 ID 与标题并同步到会话（ID 作模式锁 key、标题作展示）。
        #    新建对话首条提问时标题由 AI 异步生成，先短暂等待确保返回时非空。
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.wait_conversation_title()
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


async def ask_gemini(
    question: str,
    *,
    stream_id: str = "",
    timeout_s: int = 240,
    model: str = "",
    think: bool | None = None,
    conversation: str = "",
    local_path: str | None = None,
    output_format: str = "auto",
    return_scope: str = "last",
    upload_max_size_mb: float = 10.0,
    upload_allowed_extensions: str = GEMINI_ALLOWED_EXTENSIONS,
) -> AskResult:
    """统一入口：向 Gemini 真实提问，按输出形式返回结果。

    完整链路：获取（或创建）共享浏览器会话 → busy 加锁（会话保活）→
    设置模型（可选）→ （可选）上传 → 提问 → 等待回复 → 按 output_format
    处理 → release 解锁。Gemini 为并行第二站点，与 DeepSeek 互不影响；
    浏览器会话经代理（theme=gemini 的 profile）访问。

    Args:
        question: 用户问题（output_format=snapshot 时无需提问，可为空）。
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        timeout_s: 等待 AI 回复超时秒数。
        model: Gemini 模型（家族关键词 Flash-Lite / Flash / Pro，按关键词
            匹配网页当前版本；空默认用 Flash；Gemini 不锁定模型，每次调用
            可自由切换）。
        think: 是否开启扩展思考（True 开 / False 关 / None 不修改）。
        conversation: 对话定位方式：空（默认）沿用当前对话；精确标题进入
            该历史会话（未命中则新建）；"__new__" 强制新建。
        local_path: 已解析好的上传文件路径（None 表示不上传）。
        output_format: 输出形式（auto 纯文本返回 / snapshot 截图）。
        return_scope: 信息返回范围（last 最新回复，默认 / full 整段对话）。
        upload_max_size_mb: 上传文件大小上限（MB）。
        upload_allowed_extensions: 允许上传的扩展名（逗号分隔，小写）。

    Returns:
        AskResult: 结构化结果（成功时 ok=True，含对应输出字段）。
    """
    if not question or not question.strip():
        return AskResult(ok=False, error="问题不能为空")

    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key, theme="gemini")
        session.hold()
        manager.touch(stream_key, theme="gemini")
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = GeminiActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key, theme="gemini"),
            theme=manager.page_theme,
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )

        # 0b. 应用页面明暗主题（Gemini 改 body class，无 reload 副作用）
        if manager.page_theme:
            try:
                await actions.set_theme(manager.page_theme)
            except Exception:  # noqa: BLE001 - 主题应用失败不阻塞提问
                logger.warning("应用 Gemini 页面主题失败，使用默认主题")

        # 1. conversation 路由：空=沿用当前 / 精确标题=进入（未命中新建）/
        #    "__new__"=强制新建
        err = await _locate_conversation(actions, conversation, new_chat_on_miss=True)
        if err:
            return AskResult(ok=False, error=err)

        # 2. 设置模型：Gemini 不锁定模型，每次可切换。model 为空时默认用
        #    Flash 家族（set_model 按关键词匹配网页当前版本，已在该家族时跳过）。
        want_model = (model or "").strip() or "Flash"
        ok, msg = await actions.set_model(want_model)
        if not ok:
            return AskResult(ok=False, error=f"设置 Gemini 模型失败: {msg}")

        # 2. 设置扩展思考开关（think 为空则不修改，由上层自主决定）
        if think is not None:
            ok, msg = await actions.set_thinking(think)
            if not ok:
                return AskResult(ok=False, error=f"设置扩展思考失败: {msg}")

        # 3. 上传附件（可选）
        if local_path:
            ok, msg = await actions.upload_file(
                local_path,
                max_size_mb=upload_max_size_mb,
                allowed_extensions=GEMINI_ALLOWED_EXTENSIONS,
            )
            if not ok:
                return AskResult(ok=False, error=msg)

        # 3. 提问并等待回复
        try:
            ok, msg = await actions.ask(question.strip())
            if not ok:
                return AskResult(ok=False, error=msg)
            done, last_reply = await actions.wait_reply_done(timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 - 网页提问失败
            logger.error(f"Gemini 网页提问失败: {exc}", exc_info=True)
            return AskResult(ok=False, error=f"Gemini 网页提问失败: {exc}")
        if not done:
            return AskResult(ok=False, error="等待 Gemini 回复超时")

        # 3b. 检测对话中是否生成了图片（AI 直接出图时自动下载；纯文本回复跳过）
        image_path = ""
        try:
            image_path = await actions.try_download_generated_image(
                save_dir="data/ai_ui_snapshot_profile/gemini/images",
                wait_s=30,
            ) or ""
        except Exception as exc:  # noqa: BLE001 - 检测/下载失败不阻塞提问
            logger.warning(f"检测 Gemini 生成图片失败: {exc}")

        # 4. 按 return_scope 取信息返回文本
        if return_scope == "full":
            content = await actions.get_conversation_text(scope="full")
        else:
            content = last_reply

        # 5. 读取当前活跃会话 ID 与标题并同步到会话（ID 作稳定 key、标题作展示）。
        #    新建对话首条提问时标题由 AI 异步生成，先短暂等待确保返回时非空。
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.wait_conversation_title()
        session.set_active_conversation(current_id, current_title)

        # 6. 仅 snapshot 输出形式截图
        if output_format == "snapshot":
            data_uris = await actions.screenshot("conversation")
            if not data_uris:
                return AskResult(ok=False, error="对话区截图失败", reply=content,
                                 conversation=current_title)
            return AskResult(
                ok=True,
                reply=content,
                data_uri=data_uris,
                conversation=current_title,
                model_name="gemini.google.com",
                upload=_upload_notice(local_path),
                image_path=image_path,
            )
        return AskResult(
            ok=True,
            reply=content,
            conversation=current_title,
            model_name="gemini.google.com",
            upload=_upload_notice(local_path),
            image_path=image_path,
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
    """直接截取当前/指定 DeepSeek 对话界面，不提问。

    定位会话（空=沿用当前 / 精确标题=进入该历史会话 / __new__=新建）后
    直接截图；进入历史会话不触发开关设置。

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
        err = await _locate_conversation(actions, conversation)
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


def _upload_notice(local_path: str | None) -> str:
    """生成单附件上传说明文本。

    Args:
        local_path: 上传的本地文件路径（None 表示未上传）。

    Returns:
        str: 上传说明；未上传时返回空字符串。
    """
    return f"（已附带上传 {local_path}）" if local_path else ""


def _upload_notice_many(local_paths: list[str] | None) -> str:
    """生成多附件上传说明文本。

    Args:
        local_paths: 上传的本地文件路径列表（空/None 表示未上传）。

    Returns:
        str: 上传说明（单个直接列路径，多个列出数量与文件名）；未上传时返回空字符串。
    """
    paths = [str(p) for p in (local_paths or []) if str(p).strip()]
    if not paths:
        return ""
    if len(paths) == 1:
        return f"（已附带上传 {paths[0]}）"
    import pathlib as _pl

    names = "、".join(_pl.Path(p).name for p in paths)
    return f"（已附带上传 {len(paths)} 个附件: {names}）"


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


async def capture_gemini_snapshot(
    *,
    stream_id: str = "",
    conversation: str = "",
    think: str = "auto",
) -> AskResult:
    """直接截取当前/指定 Gemini 对话界面，不提问、不改设置。

    定位会话（空=沿用当前 / 精确标题=进入该历史会话 / __new__=新建）后
    直接长截图；与 DeepSeek 的 :func:`capture_snapshot` 对应，供 Gemini
    侧 ``gemini_snapshot`` 工具调用。

    Args:
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        conversation: 对话定位：空（默认）沿用当前对话；精确标题进入
            该历史会话（未命中报错）；"__new__" 强制新建。

    Returns:
        AskResult: 成功时 ok=True 且 data_uri 含截图；失败时 ok=False。
    """
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key, theme="gemini")
        session.hold()
        manager.touch(stream_key, theme="gemini")
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = GeminiActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key, theme="gemini"),
            theme=manager.page_theme,
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )
        # 定位会话：空=沿用当前 / 精确标题=进入 / __new__=新建
        # （未命中报错：无可截取内容，不截空会话）
        err = await _locate_conversation(actions, conversation)
        if err:
            return AskResult(ok=False, error=err)
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.get_active_conversation_title()
        session.set_active_conversation(current_id, current_title)
        data_uris = await actions.screenshot("conversation", think=think)
        if not data_uris:
            return AskResult(ok=False, error="对话区截图失败", conversation=current_title)
        return AskResult(
            ok=True,
            data_uri=data_uris,
            conversation=current_title,
            model_name="gemini.google.com",
        )
    finally:
        session.release()


async def generate_gemini_image(
    prompt: str,
    *,
    stream_id: str = "",
    timeout_s: int = 180,
    reference_paths: list[str] | None = None,
    save_dir: str = "data/ai_ui_snapshot_profile/gemini/images",
    upload_allowed_extensions: str = "png,jpg,jpeg,webp,gif,bmp",
) -> str | None:
    """用 Gemini 原生能力生成图片并保存到本地。

    Args:
        prompt: 图片描述（传 reference_paths 时描述修改意图）。
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        timeout_s: 等待生成超时秒数。
        reference_paths: 参考图本地路径列表（可空；提供后基于参考图改图/生成）。
        save_dir: 生成图片保存目录（自动创建）。
        upload_allowed_extensions: 参考图允许的扩展名。

    Returns:
        str | None: 保存的本地文件路径；失败返回 None。
    """
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key, theme="gemini")
        session.hold()
        manager.touch(stream_key, theme="gemini")
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return None

    try:
        actions = GeminiActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key, theme="gemini"),
            theme=manager.page_theme,
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )
        ok, path = await actions.generate_image(
            prompt,
            save_dir,
            timeout_s=timeout_s,
            reference_paths=reference_paths,
            upload_allowed_extensions=upload_allowed_extensions,
        )
        return path if ok else None
    except Exception as exc:  # noqa: BLE001 - 生成失败
        logger.error(f"Gemini 图片生成失败: {exc}", exc_info=True)
        return None
    finally:
        session.release()


async def create_gemini_share(
    *,
    stream_id: str = "",
    conversation: str = "",
) -> AskResult:
    """直接获取当前/指定 Gemini 对话的公开分享链接，不提问。

    Args:
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        conversation: 对话定位：空（默认）沿用当前对话；精确标题进入
            该历史会话（未命中报错）。

    Returns:
        AskResult: 成功时 ok=True 且 share_url 非空；失败时 ok=False。
    """
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key, theme="gemini")
        session.hold()
        manager.touch(stream_key, theme="gemini")
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = GeminiActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key, theme="gemini"),
            theme=manager.page_theme,
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )
        # 定位会话：空=沿用当前 / 精确标题=进入 / __new__=新建
        # （未命中报错：无可分享内容，不拿空会话去建链接）
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


# 豆包附件真实 accept 白名单（实测 2026-08：文档 + 图片，无音视频）
DOUBAO_ALLOWED_EXTENSIONS = (
    "pdf,txt,csv,doc,docx,xls,xlsx,ppt,pptx,md,mobi,epub,png,jpeg,jpg,webp"
)
# 豆包上传硬边界（probe7/8 实测）：仅图片+文档，单文件 ≤20MB；音频/视频
# 注入会弹 semi-toast-error 被拒绝；DOUBAO_UPLOAD_MAX_MB 与之一致
DOUBAO_UPLOAD_MAX_MB = 20.0

# 豆包生图/生视频产物保存目录
DOUBAO_IMAGE_SAVE_DIR = "data/ai_ui_snapshot_profile/doubao/images"
DOUBAO_VIDEO_SAVE_DIR = "data/ai_ui_snapshot_profile/doubao/videos"

# 豆包媒体生成互斥锁：媒体专用浏览器与共享会话共用同一 doubao profile，
# 同 profile 双开 Chrome 会崩溃（exitCode=21），媒体任务期间须独占
_doubao_media_lock = asyncio.Lock()


def _release_media_lock() -> None:
    """释放豆包媒体互斥锁（仅在确实持有时释放）。

    重复释放会抛 ``RuntimeError`` 并顶掉函数真实返回值（表现为工具报
    "Lock is not acquired"，掩盖真正的失败原因），故所有释放路径统一经
    本函数；锁未被持有时记录告警而非抛错。
    """
    if not _doubao_media_lock.locked():
        logger.warning("豆包媒体锁未被持有，跳过释放（调用方未取得锁）")
        return
    _doubao_media_lock.release()


async def _open_doubao_media_context(headless: bool | None = None):
    """打开豆包媒体生成专用的独立浏览器上下文（需持有 _doubao_media_lock）。

    媒体生成（生图/生视频/附件提问）用独立 persistent context（复用同一
    登录 profile），任务结束即关。headless 默认跟随共享管理器配置
    （服务器无显示器环境为无头）；同一 profile 可能被共享会话占用：
    本函数会先关闭 doubao 主题的共享会话再启动；调用方必须全程持有
    ``_doubao_media_lock`` 直至上下文关闭。

    Args:
        headless: 是否无头；None（默认）跟随共享管理器 headless 配置。

    Returns:
        tuple: (playwright, context, page)；启动失败抛异常。
    """
    if headless is None:
        headless = get_manager().headless
    manager = get_manager()
    # 关闭 doubao 共享会话，避免两个 Chrome 实例争用同一 profile 崩溃
    await manager.close_all_theme("doubao")
    from playwright.async_api import async_playwright

    from .base.browser_session import (
        STEALTH_INIT_SCRIPT,
        _default_browser_path,
        _resolve_real_user_agent,
    )

    p = await async_playwright().start()
    chrome_path = manager.browser_path_attr or _default_browser_path()
    profile_dir = manager.profile_root_attr / "doubao"
    profile_dir.mkdir(parents=True, exist_ok=True)
    launch_kwargs: dict = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "viewport": manager.viewport,
        "device_scale_factor": manager.device_scale_factor,
        "permissions": ["clipboard-read", "clipboard-write"],
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-infobars",
        ],
        "ignore_default_args": ["--enable-automation"],
    }
    if chrome_path:
        launch_kwargs["executable_path"] = chrome_path
        if headless:
            # 无头启动需替换掉 UA 中的无头标识，避免站点风控吊销登录态
            real_ua = await _resolve_real_user_agent(chrome_path)
            if real_ua:
                launch_kwargs["user_agent"] = real_ua
    try:
        context = await p.chromium.launch_persistent_context(**launch_kwargs)
    except Exception:
        try:
            await p.stop()
        except Exception:  # noqa: BLE001
            pass
        raise
    page = context.pages[0] if context.pages else await context.new_page()
    if chrome_path:
        # 与共享会话一致：真实 Chrome 时注入反指纹脚本（有头同样需要）
        await page.add_init_script(STEALTH_INIT_SCRIPT)
    return p, context, page


async def ask_doubao(
    question: str,
    *,
    stream_id: str = "",
    timeout_s: int = 240,
    model: str = "",
    new_chat: bool = False,
    conversation: str = "",
    local_paths: list[str] | None = None,
    output_format: str = "auto",
    return_scope: str = "last",
    upload_max_size_mb: float = DOUBAO_UPLOAD_MAX_MB,
    upload_allowed_extensions: str = DOUBAO_ALLOWED_EXTENSIONS,
) -> AskResult:
    """统一入口：向豆包真实提问，按输出形式返回结果。

    完整链路：获取（或创建）豆包浏览器会话（theme=doubao）→（带附件时
    先取媒体锁防同 profile 双开）→（可选）设置模型档位 → conversation
    路由（空沿用/标题进入/新建）→（可选）批量上传附件 → tiptap 编辑器
    提问 → 等待回复 → 按 output_format 处理 → release 解锁。豆包无独立
    联网开关（模型自动决策），专家档即深度思考。

    附件与对话延续：多张图片作为一条消息一次注入（形成多图提问），
    且与纯文本提问共用同一浏览器会话，因此 conversation 路由完全生效
    （空沿用当前对话可连续追问，不会每张图都另开新对话）。

    Args:
        question: 用户问题。
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        timeout_s: 等待 AI 回复超时秒数。
        model: 模型档位（快速/专家；空沿用当前档位）。
        new_chat: 是否先开新对话再提问（等价 conversation="__new__"）。
        conversation: 对话定位：空沿用当前；精确标题进入（未命中新建）；
            "__new__" 强制新建。
        local_paths: 已解析好的上传文件路径列表（None/空表示不上传；
            多个文件作为一条消息的多附件）。
        output_format: 输出形式（auto 纯文本 / snapshot 截图）。
        return_scope: 信息返回范围（last 最新回复 / full 整段对话）。
        upload_max_size_mb: 上传文件大小上限（MB）。
        upload_allowed_extensions: 允许上传的扩展名（逗号分隔，小写）。

    Returns:
        AskResult: 结构化结果（成功时 ok=True，含对应输出字段）。
    """
    if not question or not question.strip():
        return AskResult(ok=False, error="问题不能为空")

    attachments = [str(p) for p in (local_paths or []) if str(p).strip()]
    # 带附件时整体持媒体锁：共享会话与媒体生成上下文共用同一 profile，
    # 同 profile 双开 Chrome 会崩溃，锁同时保证媒体任务期间不抢 profile
    if attachments:
        await _doubao_media_lock.acquire()
    try:
        return await _ask_doubao_in_shared_session(
            question.strip(),
            stream_id=stream_id,
            timeout_s=timeout_s,
            model=model,
            new_chat=new_chat,
            conversation=conversation,
            local_paths=attachments,
            output_format=output_format,
            return_scope=return_scope,
            upload_max_size_mb=upload_max_size_mb,
            upload_allowed_extensions=upload_allowed_extensions,
        )
    finally:
        if attachments:
            _release_media_lock()


async def _ask_doubao_in_shared_session(
    question: str,
    *,
    stream_id: str = "",
    timeout_s: int = 240,
    model: str = "",
    new_chat: bool = False,
    conversation: str = "",
    local_paths: list[str] | None = None,
    output_format: str = "auto",
    return_scope: str = "last",
    upload_max_size_mb: float = DOUBAO_UPLOAD_MAX_MB,
    upload_allowed_extensions: str = DOUBAO_ALLOWED_EXTENSIONS,
) -> AskResult:
    """共享会话内完成一次豆包提问（含可选多附件上传）。

    Args:
        question: 用户问题。
        stream_id: 聊天流 ID。
        timeout_s: 等待回复超时秒数。
        model: 模型档位（空沿用当前）。
        new_chat: 是否先开新对话。
        conversation: 对话定位（空沿用 / 精确标题 / __new__）。
        local_paths: 附件本地路径列表（空表示不上传）。
        output_format: 输出形式（auto/snapshot）。
        return_scope: 信息返回范围（last/full）。
        upload_max_size_mb: 上传大小上限（MB）。
        upload_allowed_extensions: 允许的扩展名。

    Returns:
        AskResult: 结构化结果。
    """
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key, theme="doubao")
        session.hold()
        manager.touch(stream_key, theme="doubao")
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = DoubaoActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key, theme="doubao"),
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )

        # 0. 登录预检：豆包共享会话（无头）登录态可能被服务端吊销，
        # 未登录时立即报错（绝不硬用登出态操作，防加深风控）
        if not await actions.ensure_logged_in():
            return AskResult(
                ok=False,
                error="豆包登录态失效：请运行 scripts/login_doubao.py 重新登录后重试",
            )

        # 1. 设置模型档位（空沿用当前）
        want_model = (model or "").strip()
        if want_model:
            ok, msg = await actions.set_model(want_model)
            if not ok:
                return AskResult(ok=False, error=f"设置豆包档位失败: {msg}")

        # 2. conversation 路由（与 Gemini 同款：未命中历史则新建）
        err = await _locate_conversation(
            actions, conversation, new_chat=new_chat, new_chat_on_miss=True
        )
        if err:
            return AskResult(ok=False, error=err)

        # 3. 上传附件（可选，多附件一次注入为同一条消息）
        upload_notice = ""
        if local_paths:
            ok, msg = await actions.upload_files(
                local_paths,
                max_size_mb=upload_max_size_mb,
                allowed_extensions=upload_allowed_extensions,
            )
            if not ok:
                return AskResult(ok=False, error=msg)
            upload_notice = _upload_notice_many(local_paths)
        else:
            # 无附件提问：清理输入区残留草稿（上一轮上传成功但未发出的附件
            # 会跟随本轮问题一起发出，豆包切新对话不清理草稿）
            stale = await actions.count_attachments()
            if stale > 0:
                removed = await actions.clear_attachments()
                logger.warning(f"提问前清理输入区残留附件 {removed}/{stale} 个")

        # 4. 提问并等待回复
        try:
            ok, msg = await actions.ask(question)
            if not ok:
                return AskResult(ok=False, error=msg)
            done, last_reply = await actions.wait_reply_done(timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 - 网页提问失败
            logger.error(f"豆包网页提问失败: {exc}", exc_info=True)
            return AskResult(ok=False, error=f"豆包网页提问失败: {exc}")
        if not done:
            if actions.last_blocker:
                return AskResult(
                    ok=False,
                    error=(
                        f"豆包触发了人机验证（{actions.last_blocker}），自动化无法完成。"
                        "请手动在浏览器中完成验证后重试。"
                    ),
                )
            return AskResult(ok=False, error="等待豆包回复超时")

        # 5. 按 return_scope 取信息返回文本
        if return_scope == "full":
            content = await actions.get_conversation_text(scope="full")
        else:
            content = last_reply

        # 6. 读取当前活跃会话 ID 与标题并同步到会话
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.wait_conversation_title()
        session.set_active_conversation(current_id, current_title)

        # 7. 仅 snapshot 输出形式截图
        if output_format == "snapshot":
            data_uris = await actions.screenshot("conversation")
            if not data_uris:
                return AskResult(ok=False, error="对话区截图失败", reply=content,
                                 conversation=current_title)
            return AskResult(
                ok=True,
                reply=content,
                data_uri=data_uris,
                conversation=current_title,
                model_name="doubao.com",
                upload=upload_notice,
            )
        return AskResult(
            ok=True,
            reply=content,
            conversation=current_title,
            model_name="doubao.com",
            upload=upload_notice,
        )
    finally:
        session.release()


async def capture_doubao_snapshot(
    *,
    stream_id: str = "",
    conversation: str = "",
) -> AskResult:
    """直接截取当前/指定豆包对话界面，不提问、不改设置。

    定位会话（空=沿用当前 / 精确标题=进入该历史会话）后直接截图；
    与 DeepSeek 的 :func:`capture_snapshot` 对应，供豆包侧
    ``doubao_snapshot`` 工具调用。

    Args:
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        conversation: 对话定位：空（默认）沿用当前对话；精确标题进入
            该历史会话（未命中报错）。

    Returns:
        AskResult: 成功时 ok=True 且 data_uri 含截图；失败时 ok=False。
    """
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key, theme="doubao")
        session.hold()
        manager.touch(stream_key, theme="doubao")
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = DoubaoActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key, theme="doubao"),
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )
        # 定位会话：空=沿用当前 / 精确标题=进入 / __new__=新建
        # （未命中报错：无可截取内容，不截空会话）
        err = await _locate_conversation(actions, conversation)
        if err:
            return AskResult(ok=False, error=err)
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.get_active_conversation_title()
        session.set_active_conversation(current_id, current_title)
        data_uris = await actions.screenshot("conversation")
        if not data_uris:
            return AskResult(ok=False, error="对话区截图失败", conversation=current_title)
        return AskResult(
            ok=True,
            data_uri=data_uris,
            conversation=current_title,
            model_name="doubao.com",
        )
    finally:
        session.release()


async def generate_doubao_image(
    prompt: str,
    *,
    model: str = "",
    aspect_ratio: str = "",
    style: str = "",
    auto_confirm: bool = True,
    confirm_text: str = "",
    local_paths: list[str] | None = None,
    stream_id: str = "",
    timeout_s: int = 150,
    save_dir: str = DOUBAO_IMAGE_SAVE_DIR,
) -> tuple[bool, list[str], str, str]:
    """用豆包"图像生成"技能生成图片并保存到本地（同步等待完成）。

    豆包一次生成多张候选（通常 4 张），全部下载返回。流程：独立浏览器
    （媒体生成需独占 media profile）→ 新对话 → 激活技能 → （可选）上传
    参考图（图生图/改图，可多张）→ 设置原生 UI 参数（模型/比例/风格）→
    提交描述 → 处理参数确认/建议 → 等 CDN 大图（全量候选）→ 批量 fetch 落盘 → 关闭。

    Args:
        prompt: 图片描述。
        model: 生图模型（仅限免费模型 Seedream 4.5 / Seedream 4.0）。
        aspect_ratio: 画面比例（自动/1:1/16:9/9:16/3:4/4:3/2:3/3:2）。
        style: 风格（自动/动漫/电影写真 等 32 种原生风格）。
        auto_confirm: 是否自动确认豆包给出的参数调整建议。
        confirm_text: 自定义确认/调整文案（空且 auto_confirm=True 则默认发"确认"）。
        local_paths: 参考图本地路径列表（图生图/改图，可多张；空表示纯文生图）。
        stream_id: 聊天流 ID（日志与保活标识）。
        timeout_s: 等待生成超时秒数（生图通常 <90s）。
        save_dir: 生成图片保存目录（自动创建）。

    Returns:
        tuple[bool, list[str], str, str]: (是否成功, 本地路径列表, 错误信息, 参数建议文本)。
    """
    from .doubao.actions import DoubaoActions
    from .doubao.constants import (
        DEFAULT_IMAGE_MODEL,
        DEFAULT_IMAGE_RATIO,
        DEFAULT_IMAGE_STYLE,
        SKILL_IMAGE_BUTTON_ID,
        SKILL_IMAGE_PLACEHOLDER,
        SITE_URL,
    )

    want_model = (model or "").strip() or DEFAULT_IMAGE_MODEL
    want_ratio = (aspect_ratio or "").strip() or DEFAULT_IMAGE_RATIO
    want_style = (style or "").strip() or DEFAULT_IMAGE_STYLE

    async with _doubao_media_lock:
        try:
            p, context, page = await _open_doubao_media_context()
        except Exception as exc:  # noqa: BLE001 - 启动失败
            logger.error(f"打开豆包媒体浏览器失败: {exc}", exc_info=True)
            return False, [], f"打开豆包媒体浏览器失败: {exc}", ""
        try:
            await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            actions = DoubaoActions(page)
            # 登录预检：未登录绝不继续自动化（防加深风控）
            if not await actions.ensure_logged_in():
                return False, [], "豆包登录态失效：请运行 scripts/login_doubao.py 重新登录后重试", ""
            if not await actions.new_chat():
                return False, [], "新建豆包对话失败", ""
            await page.wait_for_timeout(2000)
            ok, msg = await actions.activate_skill(SKILL_IMAGE_BUTTON_ID, SKILL_IMAGE_PLACEHOLDER)
            if not ok:
                return False, [], f"激活图像生成技能失败: {msg}", ""

            # 参考图（图生图/改图）：技能受理后注入图片（可多张）
            ref_paths = [str(x) for x in (local_paths or []) if str(x).strip()]
            if ref_paths:
                ok, msg = await actions.upload_files(
                    ref_paths,
                    max_size_mb=DOUBAO_UPLOAD_MAX_MB,
                    allowed_extensions=DOUBAO_ALLOWED_EXTENSIONS,
                )
                if not ok:
                    return False, [], f"上传参考图失败: {msg}", ""
                await page.wait_for_timeout(1500)

            # 设置原生 UI 参数
            param_ok, param_msg = await actions.set_image_params(
                model=want_model,
                aspect_ratio=want_ratio,
                style=want_style,
            )
            if not param_ok:
                logger.warning(f"设置生图原生参数提示: {param_msg}")

            ok, msg = await actions.submit_prompt(prompt.strip())
            if not ok:
                return False, [], msg, ""
            # 豆包可能先返回参数确认消息（需回复"确认"才开始生成）
            confirm_ok, confirm_msg, proposal = await actions.await_param_confirm(
                auto_confirm=auto_confirm,
                confirm_text=confirm_text,
            )
            if not confirm_ok:
                if actions.last_blocker:
                    return False, [], (
                        f"豆包触发了人机验证（{actions.last_blocker}），自动化无法完成。"
                        "请手动在浏览器中完成验证后重试。"
                    ), ""
                if not auto_confirm and proposal:
                    return True, [], "", proposal
                return False, [], confirm_msg, proposal

            image_urls = await actions.wait_image_ready(timeout_s=timeout_s)
            if not image_urls:
                if actions.last_blocker:
                    return False, [], (
                        f"豆包触发了人机验证（{actions.last_blocker}），自动化无法完成。"
                        "请手动在浏览器中完成验证后重试（必要时降低调用频率）。"
                    ), ""
                page_text = (await actions.read_text(max_chars=400)) or ""
                return False, [], (
                    "等待图片生成超时（页面状态: "
                    f"{page_text[-200:]}）。若页面无生成动作，可能触发了豆包生成频控，"
                    "请间隔几分钟后再试。"
                ), ""
            paths = await actions.download_generated_images(image_urls, save_dir)
            if not paths:
                return False, [], "图片已生成但下载失败", ""
            return True, paths, "", proposal
        finally:
            await _close_media_context(p, context)


async def submit_doubao_video(
    prompt: str,
    *,
    model: str = "",
    aspect_ratio: str = "",
    duration: str = "",
    auto_confirm: bool = True,
    confirm_text: str = "",
    local_paths: list[str] | None = None,
    stream_id: str = "",
) -> tuple[bool, str, str]:
    """提交豆包"视频生成"任务并转后台等待（立即返回，不阻塞）。

    流程：独立浏览器（媒体生成需独占 media profile）→ 新对话 → 激活视频
    生成技能 → 设置原生 UI 参数（模型/比例/时长）→ （可选）上传参考图
    （图生视频，可多张）→ 提交描述 → 处理参数建议卡片 → 启动后台守护任务
    （持有该浏览器等待完成 → 下载 → 注入系统未读消息唤醒 bot 决策是否
    发送）→ 本调用立即返回。

    Args:
        prompt: 视频描述。
        model: 视频模型（仅限免费模型 Seedance 2.0 Fast / Seedance 2.0 Mini）。
        aspect_ratio: 画面比例（自动/16:9/9:16/3:4/4:3/1:1/21:9）。
        duration: 视频时长（4s/10s/15s）。
        auto_confirm: 是否自动确认豆包给出的参数调整建议。
        confirm_text: 自定义确认/调整文案（空且 auto_confirm=True 则默认发"确认"）。
        local_paths: 参考图本地路径列表（图生视频，可多张；空表示纯文生视频）。
        stream_id: 聊天流 ID（唤醒目标）。

    Returns:
        tuple[bool, str, str]: (是否提交成功, 说明, 参数建议文本)。
    """
    from .doubao.actions import DoubaoActions
    from .doubao.constants import (
        DEFAULT_VIDEO_DURATION,
        DEFAULT_VIDEO_MODEL,
        DEFAULT_VIDEO_RATIO,
        SKILL_VIDEO_BUTTON_ID,
        SKILL_VIDEO_PLACEHOLDER,
        SITE_URL,
    )
    from .doubao.video_wake import start_video_background_wait

    want_model = (model or "").strip() or DEFAULT_VIDEO_MODEL
    want_ratio = (aspect_ratio or "").strip() or DEFAULT_VIDEO_RATIO
    want_duration = (duration or "").strip() or DEFAULT_VIDEO_DURATION

    # 手动持有媒体锁（不用 async with）：提交成功后锁与浏览器一并移交后台
    # 任务（其在视频任务结束时关闭浏览器并释放锁）；其余路径统一在本函数
    # finally 关闭浏览器并释放锁
    await _doubao_media_lock.acquire()
    lock_transferred = False
    browser: tuple[Any, Any] | None = None
    try:
        try:
            p, context, page = await _open_doubao_media_context()
            browser = (p, context)
        except Exception as exc:  # noqa: BLE001 - 启动失败
            logger.error(f"打开豆包媒体浏览器失败: {exc}", exc_info=True)
            return False, f"打开豆包媒体浏览器失败: {exc}", ""
        try:
            await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            actions = DoubaoActions(page)
            # 登录预检：未登录绝不继续自动化（防加深风控）
            if not await actions.ensure_logged_in():
                return False, "豆包登录态失效：请运行 scripts/login_doubao.py 重新登录后重试", ""
            if not await actions.new_chat():
                return False, "新建豆包对话失败", ""
            await page.wait_for_timeout(2000)
            ok, msg = await actions.activate_skill(SKILL_VIDEO_BUTTON_ID, SKILL_VIDEO_PLACEHOLDER)
            if not ok:
                return False, f"激活视频生成技能失败: {msg}", ""

            # 参考图（图生视频）：视频技能受理后注入图片，豆包以其作参考帧（可多张）
            ref_paths = [str(x) for x in (local_paths or []) if str(x).strip()]
            if ref_paths:
                ok, msg = await actions.upload_files(
                    ref_paths,
                    max_size_mb=DOUBAO_UPLOAD_MAX_MB,
                    allowed_extensions=DOUBAO_ALLOWED_EXTENSIONS,
                )
                if not ok:
                    return False, f"上传参考图失败: {msg}", ""
                await page.wait_for_timeout(1500)

            # 设置原生 UI 参数
            param_ok, param_msg = await actions.set_video_params(
                model=want_model,
                aspect_ratio=want_ratio,
                duration=want_duration,
            )
            if not param_ok:
                logger.warning(f"设置视频原生参数提示: {param_msg}")

            ok, msg = await actions.submit_prompt(prompt.strip())
            if not ok:
                return False, msg, ""
            # 豆包会先返回参数确认消息（需回复"确认"才真正开始生成）
            confirm_ok, confirm_msg, proposal = await actions.await_param_confirm(
                auto_confirm=auto_confirm,
                confirm_text=confirm_text,
            )
            if not confirm_ok:
                if actions.last_blocker:
                    return False, (
                        f"豆包触发了人机验证（{actions.last_blocker}），自动化无法完成。"
                        "请手动在浏览器中完成验证后重试。"
                    ), ""
                if not auto_confirm and proposal:
                    return True, "检测到豆包参数建议，等待大模型决策", proposal
                return False, f"确认生成参数失败: {confirm_msg}", proposal

            # 提交成功：后台守护任务接管浏览器与媒体锁（任务结束后关闭
            # 浏览器并释放锁），本调用立即返回
            started = await start_video_background_wait(
                stream_id,
                actions,
                prompt.strip()[:60],
                closer=lambda: _close_media_context_sync(p, context),
                lock=_doubao_media_lock,
            )
            if started:
                lock_transferred = True
                # 浏览器与锁一并移交后台任务，由其在任务结束时关闭/释放
                browser = None
                return True, (
                    "视频生成任务已提交并转入后台等待（豆包生成约需 2-5 分钟）。"
                    "完成后会收到系统通知，届时再决定是否把视频发给用户。"
                ), proposal
            return True, "视频生成任务已提交（后台等待任务启动失败，结果需稍后手动查看豆包页面）", proposal
        except Exception as exc:  # noqa: BLE001 - 提交异常
            logger.error(f"提交豆包视频任务异常: {exc}", exc_info=True)
            return False, f"提交视频任务异常: {exc}", ""
    finally:
        if browser is not None:
            await _close_media_context(*browser)
        if not lock_transferred:
            _release_media_lock()


async def _close_media_context(p, context) -> None:  # noqa: ANN001 - playwright 对象
    """关闭媒体生成专用浏览器上下文（异常静默）。"""
    try:
        await context.close()
        await p.stop()
    except Exception:  # noqa: BLE001 - 关闭异常忽略
        pass


def _close_media_context_sync(p, context) -> None:  # noqa: ANN001 - playwright 对象
    """同步包装：为后台任务关闭回调创建异步关闭任务。

    由后台协程（视频生成守护任务）的 finally 调用，故取运行中的事件循环；
    用 ``get_event_loop`` 在无运行循环时会创建/意外复用循环，已弃用。

    Args:
        p: Playwright 实例。
        context: 浏览器上下文。
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_close_media_context(p, context))
    except Exception:  # noqa: BLE001 - 关闭异常忽略
        pass
