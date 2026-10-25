"""工具动作对象的站点路由测试。"""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins.ai_ui_snapshot.config import AiUiSnapshotConfig
from plugins.ai_ui_snapshot.services.base.shared import AskResult
from plugins.ai_ui_snapshot.services.deepseek.actions import (
    BrowserActions,
)
from plugins.ai_ui_snapshot.tools import (
    deepseek_tools,
    doubao_tools,
    gemini_tools,
)
from plugins.ai_ui_snapshot.tools.base import _ToolBase
from src.app.plugin_system.base import PlatformSendResult, PlatformSendStatus


@pytest.mark.asyncio
async def test_deepseek_actions_belong_to_deepseek_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """站点无关的工具基类不提供 DeepSeek 动作，DeepSeek 工具照常复用会话。"""
    assert not hasattr(_ToolBase, "_actions")

    page = object()
    manager = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(page=page)),
        touch=Mock(),
        max_screenshot_height=1200,
        decoration_enabled=False,
        decoration_theme="auto",
        decoration_avatar_url="",
    )
    monkeypatch.setattr(deepseek_tools, "get_manager", lambda: manager)
    tool = object.__new__(deepseek_tools.DeepseekHistoryTool)
    monkeypatch.setattr(tool, "get_current_stream_id", lambda: "stream-a")

    actions = await tool._actions()

    assert isinstance(actions, BrowserActions)
    assert actions.page is page
    manager.get.assert_awaited_once_with("stream-a")
    manager.touch.assert_called_once_with("stream-a")


@pytest.mark.asyncio
async def test_gemini_reply_keeps_success_when_generated_image_send_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Gemini 回复和生图成功时，附带图片发送失败应单独返回。"""
    image_path = tmp_path / "generated.png"
    image_path.write_bytes(b"image-data")
    plugin = SimpleNamespace(config=AiUiSnapshotConfig())
    tool = gemini_tools.AskGeminiAiTool(plugin=plugin)
    tool._bind_runtime_context(stream_id="stream-a")
    monkeypatch.setattr(
        gemini_tools,
        "ask_gemini",
        AsyncMock(
            return_value=AskResult(
                ok=True,
                reply="Gemini answer",
                image_path=str(image_path),
                conversation="chat",
            )
        ),
    )
    monkeypatch.setattr(
        gemini_tools.send_api,
        "send_image_result",
        AsyncMock(return_value=PlatformSendResult(success=False, error="平台拒绝")),
    )

    success, result = await tool.execute(question="question")

    assert success is True
    assert result["reply"] == "Gemini answer"
    assert result["image_sent"] is False
    assert "平台拒绝" in result["image_send_error"]
    assert "不要重发图片" in result["summary"]


@pytest.mark.asyncio
async def test_doubao_video_delivery_warning_uses_structured_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """视频已发送但本地后处理失败时保留发送成功并返回原警告。"""
    video_path = tmp_path / "generated.mp4"
    video_path.write_bytes(b"video")
    tool = doubao_tools.DoubaoSendVideoTool(
        plugin=SimpleNamespace(config=AiUiSnapshotConfig())
    )
    tool._bind_runtime_context(stream_id="stream-a")
    send_video = AsyncMock(
        return_value=PlatformSendResult(
            success=False,
            status=PlatformSendStatus.SENT,
            error="历史写入失败",
        )
    )
    monkeypatch.setattr(doubao_tools.send_api, "send_video_result", send_video)

    success, result = await tool.execute(path=str(video_path))

    assert success is True
    assert result["sent"] is True
    assert "历史写入失败" in result["delivery_warning"]
    send_video.assert_awaited_once_with(
        str(video_path), "stream-a", processed_plain_text="[豆包生成视频]"
    )