"""截图交付的分片校验与顺序测试。"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import AsyncMock

import pytest

from src.app.plugin_system.base import PlatformSendResult, PlatformSendStatus

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins.ai_ui_snapshot.services import delivery


@pytest.mark.asyncio
async def test_sends_all_snapshot_pieces_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """多片截图按原顺序剥离 URI 头并发送。"""
    send_image = AsyncMock(return_value=PlatformSendResult(success=True, message_id="sent-1"))
    monkeypatch.setattr(delivery.send_api, "send_image_result", send_image)

    success, error = await delivery.send_snapshot_pieces(
        ["data:image/png;base64,first", "data:image/png;base64,second"],
        "stream-a", "豆包",
    )

    assert success is True
    assert error == ""
    assert [call.args for call in send_image.await_args_list] == [
        ("first", "stream-a"), ("second", "stream-a")
    ]
    assert all(call.kwargs == {"processed_plain_text": "[豆包 界面截图]"} for call in send_image.await_args_list)


@pytest.mark.asyncio
async def test_rejects_invalid_snapshot_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    """无效分片不产生部分发送。"""
    send_image = AsyncMock(return_value=PlatformSendResult(success=True, message_id="sent-1"))
    monkeypatch.setattr(delivery.send_api, "send_image_result", send_image)

    success, error = await delivery.send_snapshot_pieces(
        ["data:image/png;base64,first", "invalid"], "stream-a", "Gemini",
        invalid_error="生成截图失败",
    )

    assert success is False
    assert error == "生成截图失败"
    send_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_stops_when_snapshot_send_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """发送失败后不继续发送剩余分片。"""
    send_image = AsyncMock(
        side_effect=[
            PlatformSendResult(
                success=False,
                status=PlatformSendStatus.FAILED,
                error="平台拒绝",
            ),
            PlatformSendResult(success=True),
        ]
    )
    monkeypatch.setattr(delivery.send_api, "send_image_result", send_image)

    success, error = await delivery.send_snapshot_pieces(
        ["data:image/png;base64,first", "data:image/png;base64,second"],
        "stream-a", "DeepSeek",
    )

    assert success is False
    assert "发送失败：平台拒绝" in error
    assert send_image.await_count == 1


@pytest.mark.parametrize(
    ("status", "error_text"),
    [
        (PlatformSendStatus.UNKNOWN, "发送结果未确认"),
        (PlatformSendStatus.INTERCEPTED, "消息被拦截"),
    ],
)
@pytest.mark.asyncio
async def test_does_not_retry_unconfirmed_or_intercepted_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    status: PlatformSendStatus,
    error_text: str,
) -> None:
    """未知或被接管的分片结果不被当作成功，也不重试。"""
    send_image = AsyncMock(
        return_value=PlatformSendResult(success=False, status=status, error="未确认")
    )
    monkeypatch.setattr(delivery.send_api, "send_image_result", send_image)

    success, error = await delivery.send_snapshot_pieces(
        ["data:image/png;base64,first", "data:image/png;base64,second"],
        "stream-a", "Gemini",
    )

    assert success is False
    assert error_text in error
    assert "不要自动重试" in error or "未下发平台" in error
    send_image.assert_awaited_once()


@pytest.mark.asyncio
async def test_keeps_confirmed_send_success_when_local_postprocessing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SENT 即使本地后处理失败也不重复发送。"""
    send_image = AsyncMock(
        return_value=PlatformSendResult(
            success=False,
            status=PlatformSendStatus.SENT,
            error="历史写入失败",
        )
    )
    monkeypatch.setattr(delivery.send_api, "send_image_result", send_image)

    success, warning = await delivery.send_snapshot_pieces(
        ["data:image/png;base64,first"], "stream-a", "Gemini",
    )

    assert success is True
    assert "不要重复发送" in warning
    send_image.assert_awaited_once()