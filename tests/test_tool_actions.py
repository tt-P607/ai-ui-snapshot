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

from plugins.ai_ui_snapshot.tools import deepseek_tools  # noqa: E402
from plugins.ai_ui_snapshot.tools.base import _ToolBase  # noqa: E402
from plugins.ai_ui_snapshot.services.deepseek.actions import BrowserActions  # noqa: E402


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