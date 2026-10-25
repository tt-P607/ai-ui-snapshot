"""将网页截图分片交付到当前聊天。"""

from __future__ import annotations

from src.app.plugin_system.api import send_api
from src.app.plugin_system.base import PlatformSendStatus

from .base.shared import strip_data_uri_prefix


async def send_snapshot_pieces(
    pieces: list[str], stream_id: str, site_name: str, *, invalid_error: str = "截图失败"
) -> tuple[bool, str]:
    """按顺序发送截图分片并汇总已确认的平台回执。"""
    if not pieces or any(not piece.startswith("data:") for piece in pieces):
        return False, invalid_error

    warnings: list[str] = []
    for index, piece in enumerate(pieces, 1):
        result = await send_api.send_image_result(
            strip_data_uri_prefix(piece),
            stream_id,
            processed_plain_text=f"[{site_name} 界面截图]",
        )
        sent, receipt = send_api.format_send_result(result, f"第 {index}/{len(pieces)} 片截图已发送")
        if not sent:
            return False, f"截图已生成，{receipt}；已确认发送 {index - 1}/{len(pieces)} 片"
        if result.status is PlatformSendStatus.SENT and not result.success:
            warnings.append(receipt)
    if warnings:
        return True, "；".join(warnings)
    return True, ""