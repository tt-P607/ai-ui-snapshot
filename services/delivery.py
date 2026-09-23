"""将网页截图分片交付到当前聊天。"""

from __future__ import annotations

from src.app.plugin_system.api import send_api

from .base.shared import strip_data_uri_prefix


async def send_snapshot_pieces(
    pieces: list[str], stream_id: str, site_name: str, *, invalid_error: str = "截图失败"
) -> str:
    """按顺序发送截图分片；成功返回空字符串，失败返回对应错误信息。"""
    if not pieces or any(not piece.startswith("data:") for piece in pieces):
        return invalid_error

    for piece in pieces:
        sent = await send_api.send_image(
            strip_data_uri_prefix(piece),
            stream_id,
            processed_plain_text=f"[{site_name} 界面截图]",
        )
        if not sent:
            return "截图已生成但发送失败"
    return ""