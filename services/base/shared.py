"""站点服务共用的数据类型、对话定位与媒体处理工具。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from src.app.plugin_system.api.media_api import get_media_info


class ConversationActions(Protocol):
    """站点页面动作实现对话定位所需的最小接口。"""

    page: Any

    def use_page(self, page: Any) -> None: ...

    async def new_chat(self) -> Any: ...

    async def list_conversations(self) -> list[str]: ...

    async def open_conversation(self, title: str) -> bool: ...


@dataclass(slots=True)
class AskResult:
    """一次网页提问或站点操作的完整结果。

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
    images: list[str] = field(default_factory=list)
    image_descriptions: list[str] = field(default_factory=list)
    images_base64: list[str] = field(default_factory=list)
    snapshot_meta: dict[str, Any] = field(default_factory=dict)


async def resolve_media_path(media_id: str) -> str | None:
    """通过框架媒体缓存，将 media_id 解析为本地文件路径。

    Args:
        media_id: 聊天图片占位符中的 media_id。

    Returns:
        str | None: 本地文件路径；未找到或记录无路径时返回 None。
    """
    info = await get_media_info(media_id)
    if not info:
        return None
    path = info.get("path")
    return str(path) if path else None


async def _locate_conversation(
    actions: ConversationActions,
    conversation: str,
    *,
    new_chat: bool = False,
    new_chat_on_miss: bool = False,
    default_new: bool = False,
    new_page_cb: Any | None = None,
) -> str | None:
    """定位目标对话：顯式标题=进入继续；未显式指定时根据 default_new 决定。

    提问场景可通过 default_new 在未指定标题时新建对话；只读场景默认沿用
    当前页面。显式标题未命中时由 new_chat_on_miss 决定是否新建。

    Args:
        actions: 站点页面动作封装。
        conversation: 对话定位标题。
        new_chat: 是否强制开新对话。
        new_chat_on_miss: 显式传入的标题未命中历史会话时是否新建。
        default_new: 未显式传入标题时是否默认创建新对话。
        new_page_cb: 已有对话时用于创建新页面的异步回调。

    Returns:
        str | None: 成功返回 None；失败返回错误信息。
    """
    want_conversation = (conversation or "").strip()
    if new_chat:
        want_conversation = "__new__"

    if want_conversation == "__new__" or (not want_conversation and default_new):
        if new_page_cb is not None:
            page_or_session = await new_page_cb()
            actions.use_page(getattr(page_or_session, "page", page_or_session))
        await actions.new_chat()
        await actions.page.wait_for_timeout(2500)
        return None

    if not want_conversation:
        return None

    listed = await actions.list_conversations()
    if want_conversation not in listed:
        if new_chat_on_miss:
            if new_page_cb is not None:
                page_or_session = await new_page_cb()
                actions.use_page(getattr(page_or_session, "page", page_or_session))
            await actions.new_chat()
            await actions.page.wait_for_timeout(2500)
            return None
        return f"未找到历史会话: {want_conversation}"

    if not await actions.open_conversation(want_conversation):
        return f"进入历史会话失败: {want_conversation}"

    await actions.page.wait_for_timeout(1000)
    return None


def sniff_image_suffix(raw: bytes) -> str:
    """按文件头判断图片扩展名。

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
        str: 上传说明；未上传时返回空字符串。
    """
    paths = [str(path) for path in (local_paths or []) if str(path).strip()]
    if not paths:
        return ""
    if len(paths) == 1:
        return f"（已附带上传 {paths[0]}）"

    names = "、".join(Path(path).name for path in paths)
    return f"（已附带上传 {len(paths)} 个附件: {names}）"


def strip_data_uri_prefix(data_uri: str) -> str:
    """剥离 ``data:`` URI 前缀，返回可直接发送的媒体数据。

    Args:
        data_uri: data:image/png;base64,... 或任意数据字符串。

    Returns:
        str: 剥离 ``data:`` 前缀后的数据（非 ``data:`` 开头时原样返回）。
    """
    if data_uri.startswith("data:") and "," in data_uri:
        return data_uri.split(",", 1)[1]
    return data_uri