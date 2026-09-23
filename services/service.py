"""站点服务兼容入口与跨站点识图编排。"""

from __future__ import annotations

from src.app.plugin_system.api import prompt_api
from src.app.plugin_system.api.log_api import get_logger

from .base.shared import AskResult, resolve_media_path, sniff_image_suffix, strip_data_uri_prefix
from .deepseek.service import ask_deepseek, capture_snapshot, create_share
from .gemini.service import (
    GEMINI_ALLOWED_EXTENSIONS,
    ask_gemini,
    capture_gemini_snapshot,
    create_gemini_share,
    download_gemini_chat_image,
    generate_gemini_image,
)
from .doubao.service import (
    DOUBAO_ALLOWED_EXTENSIONS, DOUBAO_UPLOAD_MAX_MB,
    DOUBAO_IMAGE_SAVE_DIR, DOUBAO_VIDEO_SAVE_DIR,
    ask_doubao, capture_doubao_snapshot, download_doubao_chat_images,
    generate_doubao_image, submit_doubao_video,
)
from .sites import SITE_NAMES

__all__ = [
    "AskResult", "resolve_media_path", "sniff_image_suffix", "strip_data_uri_prefix",
    "GEMINI_ALLOWED_EXTENSIONS", "DOUBAO_ALLOWED_EXTENSIONS", "DOUBAO_UPLOAD_MAX_MB",
    "DOUBAO_IMAGE_SAVE_DIR", "DOUBAO_VIDEO_SAVE_DIR", "RECOGNIZE_SITES",
    "ask_deepseek", "capture_snapshot", "create_share",
    "ask_gemini", "capture_gemini_snapshot", "create_gemini_share",
    "download_gemini_chat_image", "generate_gemini_image",
    "ask_doubao", "capture_doubao_snapshot", "download_doubao_chat_images",
    "generate_doubao_image", "submit_doubao_video",
    "framework_recognize_prompt", "recognize_image_by_site",
]

logger = get_logger("ai_ui_snapshot.service")
RECOGNIZE_SITES: tuple[str, ...] = SITE_NAMES


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
