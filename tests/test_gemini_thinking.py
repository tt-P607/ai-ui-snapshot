"""Gemini 扩展思考状态识别测试。"""

from __future__ import annotations

import pathlib
import sys

import pytest
from playwright.async_api import async_playwright

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins.ai_ui_snapshot.services.base.browser_session import (  # noqa: E402
    resolve_browser_path,
)
from plugins.ai_ui_snapshot.services.gemini.actions import GeminiActions  # noqa: E402
from plugins.ai_ui_snapshot.services.gemini.constants import (  # noqa: E402
    THINKING_SELECTED_SCRIPT,
)


@pytest.mark.asyncio
async def test_thinking_ignores_hidden_and_active_menu_items() -> None:
    """隐藏的旧菜单项和焦点状态不能误报扩展思考已开启。"""
    browser_path = resolve_browser_path()
    if not browser_path:
        pytest.skip("未安装 Chrome，无法验证 Gemini 页面脚本")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=browser_path,
            headless=True,
        )
        try:
            page = await browser.new_page()
            await page.set_content(
                "<button class='input-area-switch' "
                "aria-label='打开模式选择器，当前模式为 Flash'>Flash</button>"
                "<div style='display:none'>"
                "<div role='menuitem' class='selected active'>扩展思考</div>"
                "</div>"
                "<div id='current' role='menuitem' class='active'>扩展思考</div>"
            )
            actions = GeminiActions(page)

            assert await page.evaluate(THINKING_SELECTED_SCRIPT) is False
            assert await actions.get_thinking() is False

            await page.evaluate(
                """() => {
                    document.querySelector('.input-area-switch').setAttribute(
                        'aria-label', '打开模式选择器，当前模式为 Flash 扩展'
                    );
                    document.querySelector('#current').classList.add('selected');
                }"""
            )
            assert await page.evaluate(THINKING_SELECTED_SCRIPT) is True
            assert await actions.get_thinking() is True
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_thinking_toggle_reads_menu_when_button_label_does_not_change() -> None:
    """模式按钮不显示开关状态时仍能按菜单选中态切换并校验。"""
    browser_path = resolve_browser_path()
    if not browser_path:
        pytest.skip("未安装 Chrome，无法验证 Gemini 页面脚本")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=browser_path,
            headless=True,
        )
        try:
            page = await browser.new_page()
            await page.set_content(
                "<button class='input-area-switch' "
                "aria-label='打开模式选择器，当前模式为 Flash' "
                "onclick=\"const menu = document.querySelector('#menu'); "
                "menu.hidden = !menu.hidden\">Flash</button>"
                "<div id='menu' hidden>"
                "<div role='menuitemradio' aria-checked='false' "
                "onclick=\"this.setAttribute('aria-checked', "
                "this.getAttribute('aria-checked') === 'true' ? 'false' : 'true'); "
                "document.querySelector('#menu').hidden = true\">扩展思考</div>"
                "</div>"
                "<script>document.addEventListener('keydown', event => {"
                "if (event.key === 'Escape') document.querySelector('#menu').hidden = true"
                "})</script>"
            )
            actions = GeminiActions(page)

            assert await page.evaluate(THINKING_SELECTED_SCRIPT) is None
            assert await actions.get_thinking() is False
            assert await actions.set_thinking(True) == (True, "扩展思考: 已开启")
            assert await actions.get_thinking() is True
            assert await actions.set_thinking(False) == (True, "扩展思考: 已关闭")
            assert await actions.get_thinking() is False
        finally:
            await browser.close()
