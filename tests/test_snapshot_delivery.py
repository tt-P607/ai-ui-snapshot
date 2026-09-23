"""截图交付的分片校验与顺序测试。"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import AsyncMock

import pytest

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins.ai_ui_snapshot.services import delivery  # noqa: E402


@pytest.mark.asyncio
async def test_sends_all_snapshot_pieces_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """多片截图按原顺序剥离 URI 头并发送。"""
    send_image = AsyncMock(return_value=True)
    monkeypatch.setattr(delivery.send_api, "send_image", send_image)

    error = await delivery.send_snapshot_pieces(
        ["data:image/png;base64,first", "data:image/png;base64,second"],
        "stream-a", "豆包",
    )

    assert error == ""
    assert [call.args for call in send_image.await_args_list] == [
        ("first", "stream-a"), ("second", "stream-a")
    ]
    assert all(call.kwargs == {"processed_plain_text": "[豆包 界面截图]"} for call in send_image.await_args_list)


@pytest.mark.asyncio
async def test_rejects_invalid_snapshot_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    """无效分片不产生部分发送。"""
    send_image = AsyncMock(return_value=True)
    monkeypatch.setattr(delivery.send_api, "send_image", send_image)

    error = await delivery.send_snapshot_pieces(
        ["data:image/png;base64,first", "invalid"], "stream-a", "Gemini",
        invalid_error="生成截图失败",
    )

    assert error == "生成截图失败"
    send_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_stops_when_snapshot_send_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """发送失败后不继续发送剩余分片。"""
    send_image = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(delivery.send_api, "send_image", send_image)

    error = await delivery.send_snapshot_pieces(
        ["data:image/png;base64,first", "data:image/png;base64,second"],
        "stream-a", "DeepSeek",
    )

    assert error == "截图已生成但发送失败"
    assert send_image.await_count == 1