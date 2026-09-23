"""豆包超高虚拟视口长截图测试。"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import AsyncMock

import pytest

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _PLUGIN_ROOT.parent.parent
for import_root in (_PLUGIN_ROOT, _PROJECT_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from services.doubao.actions import DoubaoActions  # noqa: E402


class FakePage:
    """记录豆包长截图期间的视口、脚本和截图调用。"""

    def __init__(self, *, screenshot_error: bool = False) -> None:
        self.viewport_size = {"width": 1440, "height": 900}
        self.viewport_changes: list[dict[str, int]] = []
        self.evaluate_calls = 0
        self.screenshot_calls: list[dict[str, object]] = []
        self.screenshot_error = screenshot_error

    async def evaluate(self, script: str, __: object = None) -> dict[str, int] | int | None:
        """首次返回页面布局，其余调用只记录恢复流程。"""
        self.evaluate_calls += 1
        if script.startswith("() => document.querySelectorAll"):
            return 78
        if self.evaluate_calls == 1:
            return {
                "contentTop": 56,
                "contentHeight": 1784,
                "footerHeight": 116,
                "conversationTop": 1056,
                "sidebarTop": 260,
                "sidebarItemCount": 11,
            }
        return None

    async def set_viewport_size(self, viewport: dict[str, int]) -> None:
        """记录并应用视口尺寸。"""
        self.viewport_size = dict(viewport)
        self.viewport_changes.append(dict(viewport))

    async def wait_for_timeout(self, _: int) -> None:
        """跳过真实等待。"""

    async def screenshot(self, **kwargs: object) -> bytes:
        """记录截图参数并返回最小 PNG 替身。"""
        self.screenshot_calls.append(kwargs)
        if self.screenshot_error:
            raise RuntimeError("capture failed")
        return b"png"


@pytest.mark.asyncio
async def test_full_screenshot_uses_one_tall_viewport_and_restores_page() -> None:
    """完整截图应一次拉高视口，并在截图后恢复原始页面尺寸。"""
    page = FakePage()
    actions = DoubaoActions(
        page,
        max_screenshot_height=8000,
        decoration_enabled=False,
    )

    shots = await actions._tall_viewport_shot()

    assert len(shots) == 1
    assert page.screenshot_calls == [{"type": "png", "full_page": False}]
    assert page.viewport_changes == [
        {"width": 1440, "height": 1956},
        {"width": 1440, "height": 900},
    ]
    assert page.evaluate_calls == 9


@pytest.mark.asyncio
async def test_full_screenshot_keeps_full_viewport_and_splits_output() -> None:
    """视口须撑满内容高度（虚拟列表按视口渲染），超限部分在输出侧分片。"""
    page = FakePage()
    actions = DoubaoActions(
        page,
        max_screenshot_height=1600,
        decoration_enabled=False,
    )

    await actions._tall_viewport_shot()

    # 视口不能被单图上限压低，否则未渲染的消息行会整段消失
    assert page.viewport_changes[0] == {"width": 1440, "height": 1956}
    assert page.viewport_changes[-1] == {"width": 1440, "height": 900}
    assert page.screenshot_calls == [
        {
            "type": "png",
            "full_page": True,
            "clip": {"x": 0, "y": 0, "width": 1440, "height": 978},
        },
        {
            "type": "png",
            "full_page": True,
            "clip": {"x": 0, "y": 978, "width": 1440, "height": 978},
        },
    ]


@pytest.mark.asyncio
async def test_full_screenshot_restores_viewport_after_capture_failure() -> None:
    """截图失败时仍应恢复原始视口和页面状态。"""
    page = FakePage(screenshot_error=True)
    actions = DoubaoActions(
        page,
        max_screenshot_height=8000,
        decoration_enabled=False,
    )

    shots = await actions._tall_viewport_shot()

    assert shots == []
    assert page.viewport_changes == [
        {"width": 1440, "height": 1956},
        {"width": 1440, "height": 900},
    ]
    assert page.evaluate_calls == 9


@pytest.mark.asyncio
async def test_full_screenshot_waits_for_sidebar_after_expanding() -> None:
    """侧栏的异步历史条目稳定后才应截取豆包长图。"""
    page = FakePage()
    actions = DoubaoActions(page, decoration_enabled=False)
    actions._wait_sidebar_ready = AsyncMock()
    actions._tall_viewport_shots = AsyncMock(return_value=["image"])

    shots = await actions._tall_viewport_shot()

    assert shots == ["image"]
    actions._wait_sidebar_ready.assert_awaited_once()
    assert actions._wait_sidebar_ready.await_args.args == (11,)
    actions._tall_viewport_shots.assert_awaited_once_with(1956)


@pytest.mark.asyncio
async def test_sidebar_waits_until_item_count_stops_changing() -> None:
    """侧栏异步补全期间不应把第一批已显示条目当成加载完成。"""
    page = FakePage()
    actions = DoubaoActions(page, decoration_enabled=False)
    page.evaluate = AsyncMock(side_effect=[11, 31, 31, 78, 78, 78, 78])

    await actions._wait_sidebar_ready(11)

    assert page.evaluate.await_count == 7


@pytest.mark.asyncio
async def test_sidebar_wait_accepts_already_loaded_history() -> None:
    """同一会话再次截图时，已加载的侧栏不需要再增加条目。"""
    page = FakePage()
    actions = DoubaoActions(page, decoration_enabled=False)
    page.evaluate = AsyncMock(return_value=78)

    await actions._wait_sidebar_ready(78)

    assert page.evaluate.await_count == 6