"""通用页面截图生命周期测试。"""

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

from services.base.page_actions import PageActions  # noqa: E402
from services.gemini.actions import GeminiActions  # noqa: E402


class FakePage:
    """记录页面展开与恢复脚本调用。"""

    def __init__(self, expand_result: object) -> None:
        self.expand_result = expand_result
        self.evaluate_calls: list[tuple[str, object]] = []
        self.waits: list[int] = []

    async def evaluate(self, script: str, arg: object = None) -> object:
        """返回展开结果并记录恢复参数。"""
        self.evaluate_calls.append((script, arg))
        if script == "expand":
            return self.expand_result
        return None

    async def wait_for_timeout(self, milliseconds: int) -> None:
        """记录页面重排等待时间。"""
        self.waits.append(milliseconds)


@pytest.mark.asyncio
async def test_expanded_fullpage_restores_site_state() -> None:
    """展开后的整页截图应复用基类截图并恢复站点状态。"""
    page = FakePage([{"path": "main"}])
    actions = PageActions(page, decoration_enabled=False)
    actions._fullpage_shots = AsyncMock(return_value=["data:image/png;base64,test"])

    shots = await actions._expanded_fullpage_shots(
        "expand",
        "restore",
        expand_arg="main",
        wait_ms=150,
    )

    assert shots == ["data:image/png;base64,test"]
    assert page.evaluate_calls == [
        ("expand", "main"),
        ("restore", {"saved": [{"path": "main"}]}),
    ]
    assert page.waits == [150]


@pytest.mark.asyncio
async def test_expanded_fullpage_restores_when_capture_raises() -> None:
    """截图异常时仍应在 finally 中恢复站点状态。"""
    page = FakePage({"saved": [{"path": "main"}]})
    actions = PageActions(page, decoration_enabled=False)
    actions._fullpage_shots = AsyncMock(side_effect=RuntimeError("capture failed"))

    with pytest.raises(RuntimeError, match="capture failed"):
        await actions._expanded_fullpage_shots(
            "expand",
            "restore",
            saved_key="saved",
            require_saved=True,
        )

    assert page.evaluate_calls[-1] == (
        "restore",
        {"saved": [{"path": "main"}]},
    )


@pytest.mark.asyncio
async def test_expanded_fullpage_can_require_restore_state() -> None:
    """站点未成功展开时不应截取错误的普通视口页面。"""
    page = FakePage({"saved": []})
    actions = PageActions(page, decoration_enabled=False)
    actions._fullpage_shots = AsyncMock(return_value=["unexpected"])

    shots = await actions._expanded_fullpage_shots(
        "expand",
        "restore",
        saved_key="saved",
        require_saved=True,
    )

    assert shots == []
    actions._fullpage_shots.assert_not_awaited()
    assert page.evaluate_calls == [("expand", None)]


def test_gemini_reuses_base_fullpage_implementation() -> None:
    """Gemini 不应重新实现通用整页截图逻辑。"""
    assert "_fullpage_shots" not in GeminiActions.__dict__


@pytest.mark.asyncio
async def test_wait_reply_done_ignores_previous_reply_after_reasoning() -> None:
    """思考指示器消失后仍应等待本轮正式正文出现并稳定。"""
    page = FakePage(None)
    page.evaluate = AsyncMock(
        side_effect=[True, False, False, False, False, False, False, False, False]
    )
    actions = PageActions(page, decoration_enabled=False)
    actions.generating_script = "generating"
    actions.poll_interval_s = 0
    actions.get_conversation_text = AsyncMock(
        side_effect=[
            "上一轮回复",
            "上一轮回复",
            "上一轮回复",
            "上一轮回复",
            "本轮正式回复",
            "本轮正式回复",
            "本轮正式回复",
            "本轮正式回复",
            "本轮正式回复",
        ]
    )

    done, reply = await actions.wait_reply_done(
        timeout_s=1,
        previous_reply="上一轮回复",
    )

    assert done is True
    assert reply == "本轮正式回复"
    assert actions.get_conversation_text.await_count == 9
