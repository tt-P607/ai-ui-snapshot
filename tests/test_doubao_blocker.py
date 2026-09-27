"""豆包验证窗口的识别和拦截状态测试。"""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import Route, async_playwright

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins.ai_ui_snapshot.services.base.browser_session import (  # noqa: E402
    resolve_browser_path,
)
from plugins.ai_ui_snapshot.services.doubao.actions import DoubaoActions  # noqa: E402
from plugins.ai_ui_snapshot.services.doubao.constants import CAPTCHA_SCRIPT  # noqa: E402


@pytest.mark.asyncio
async def test_captcha_requires_visible_matching_iframe() -> None:
    """历史消息或无关验证 iframe 不应被判成人机验证。"""
    browser_path = resolve_browser_path()
    if not browser_path:
        pytest.skip("未安装 Chrome，无法验证豆包页面脚本")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=browser_path,
            headless=True,
        )
        try:
            page = await browser.new_page(viewport={"width": 1000, "height": 800})

            async def block_external(route: Route) -> None:
                """阻止测试 iframe 请求外部验证服务。"""
                await route.abort()

            await page.route("**/*", block_external)
            await page.set_content(
                "<main>请选择所有符合上文描述的图片。拖拽到这里进行验证。</main>"
            )
            assert await page.evaluate(CAPTCHA_SCRIPT) == ""

            await page.evaluate(
                """() => {
                    const wrapper = document.createElement('div');
                    wrapper.style.cssText = 'position:fixed;left:200px;top:100px;opacity:0';
                    const frame = document.createElement('iframe');
                    frame.id = 'test-captcha';
                    frame.style.cssText = 'width:380px;height:563px';
                    frame.src = 'https://rmc.bytedance.com/verifycenter/captcha';
                    wrapper.appendChild(frame);
                    document.body.appendChild(wrapper);
                }"""
            )
            assert await page.evaluate(CAPTCHA_SCRIPT) == ""

            await page.evaluate(
                "() => document.querySelector('#test-captcha').parentElement.style.opacity = '1'"
            )
            assert await page.evaluate(CAPTCHA_SCRIPT) == "可见图片验证窗口"

            await page.evaluate(
                "() => document.querySelector('#test-captcha').src = "
                "'https://rmc.bytedance.com/verifycenter/captcha-unrelated'"
            )
            assert await page.evaluate(CAPTCHA_SCRIPT) == ""
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_blocker_state_clears_when_page_evaluation_fails() -> None:
    """页面脚本异常不能沿用上一次的验证判断。"""
    page = SimpleNamespace(evaluate=AsyncMock(side_effect=RuntimeError("page closed")))
    actions = DoubaoActions(page)
    actions.last_blocker = "可见图片验证窗口"

    assert await actions.check_blocker() == ""
    assert actions.last_blocker == ""
