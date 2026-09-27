"""Gemini 模型权限探测与选择回归测试。"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from playwright.async_api import Page, async_playwright

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins.ai_ui_snapshot.services.base.browser_session import (  # noqa: E402
    resolve_browser_path,
)
from plugins.ai_ui_snapshot.services.gemini.actions import GeminiActions  # noqa: E402
from plugins.ai_ui_snapshot.services.gemini.constants import (  # noqa: E402
    MODEL_ITEM_SELECTED_SCRIPT,
)


@pytest.fixture
def browser_path() -> str:
    """查找可用于本地合成页面的 Chrome。"""
    path = resolve_browser_path()
    if not path:
        pytest.skip("未安装 Chrome，无法验证 Gemini 页面脚本")
    return path


@asynccontextmanager
async def _new_page(browser_path: str) -> AsyncIterator[Page]:
    """启动不复用用户数据目录的临时浏览器页面。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=browser_path,
            headless=True,
        )
        try:
            yield await browser.new_page()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_model_probe_filters_hidden_disabled_and_focus_items(
    browser_path: str,
) -> None:
    """普通布局应跳过隐藏与禁用选项，且 active 焦点不算选中。"""
    async with _new_page(browser_path) as page:
        await page.set_content(
            "<div role='menu'>"
            "<div role='menuitemradio' aria-label='3.8 Flash' aria-checked='true' hidden>"
            "3.8 Flash</div>"
            "<div role='menuitemradio' aria-label='3.7 Flash' aria-disabled='true'>"
            "3.7 Flash</div>"
            "<div role='menuitemradio' aria-label='3.6 Flash' class='active'>"
            "3.6 Flash</div>"
            "<div role='menuitemradio' aria-label='Pro' aria-checked='true'>Pro</div>"
            "</div>"
        )

        flash = await page.evaluate(MODEL_ITEM_SELECTED_SCRIPT, "Flash")
        pro = await page.evaluate(MODEL_ITEM_SELECTED_SCRIPT, "Pro")

        assert set(flash) == {"hit", "index", "text", "selected", "available"}
        assert flash == {
            "hit": True,
            "index": 2,
            "text": "3.6 Flash",
            "selected": False,
            "available": ["3.6 Flash", "Pro"],
        }
        assert pro == {
            "hit": True,
            "index": 3,
            "text": "Pro",
            "selected": True,
            "available": ["3.6 Flash", "Pro"],
        }


@pytest.mark.asyncio
async def test_model_probe_supports_top_layout_flash_version(browser_path: str) -> None:
    """顶部布局应选可用的 3.8 Flash，而不是更高但禁用的版本。"""
    async with _new_page(browser_path) as page:
        await page.set_content(
            "<div role='menu' class='top-model-menu'>"
            "<div role='menuitem' aria-label='3.9 Flash' aria-disabled='true'>"
            "3.9 Flash</div>"
            "<div role='menuitemradio' aria-label='3.8 Flash' aria-checked='true'>"
            "3.8 Flash</div>"
            "<div role='menuitemradio' aria-label='3.6 Flash' aria-checked='false'>"
            "3.6 Flash</div>"
            "<div role='menuitem' aria-label='Pro' aria-checked='false'>Pro</div>"
            "</div>"
        )

        flash = await page.evaluate(MODEL_ITEM_SELECTED_SCRIPT, "Flash")

        assert flash == {
            "hit": True,
            "index": 1,
            "text": "3.8 Flash",
            "selected": True,
            "available": ["3.8 Flash", "3.6 Flash", "Pro"],
        }


@pytest.mark.asyncio
async def test_set_model_selects_highest_available_when_button_says_flash(
    browser_path: str,
) -> None:
    """按钮只显示 Flash 且切换时重绘，也应验证最高可用项。"""
    async with _new_page(browser_path) as page:
        await page.set_content(
            "<button class='input-area-switch' "
            "aria-label='打开模式选择器，当前模式为 Flash' "
            "onclick=\"const menu = document.querySelector('#menu'); "
            "menu.hidden = !menu.hidden\">Flash</button>"
            "<div id='menu' role='menu' hidden>"
            "<div role='menuitemradio' aria-label='3.9 Flash' aria-checked='true' hidden>"
            "3.9 Flash</div>"
            "<div role='menuitemradio' aria-label='4.0 Flash' aria-disabled='true'>"
            "4.0 Flash</div>"
            "<div id='flash38' role='menuitemradio' aria-label='3.8 Flash' "
            "aria-checked='false' onclick=\"this.setAttribute('aria-checked', 'true'); "
            "document.querySelector('#menu').hidden = true; "
            "const button = document.querySelector('.input-area-switch'); "
            "button.remove(); setTimeout(() => document.body.prepend(button), 1800)\">"
            "3.8 Flash</div>"
            "<div id='flash36' role='menuitemradio' aria-label='3.6 Flash' "
            "aria-checked='false'>3.6 Flash</div>"
            "</div>"
        )
        actions = GeminiActions(page)

        success, _ = await actions.set_model("Flash")

        assert success is True
        assert await page.locator("#flash38").get_attribute("aria-checked") == "true"
        assert await page.locator("#flash36").get_attribute("aria-checked") == "false"


@pytest.mark.asyncio
async def test_set_model_handles_pro_account_top_menu(browser_path: str) -> None:
    """Pro 顶部菜单应按实际选项切换，并识别 selected 而非 active。"""
    async with _new_page(browser_path) as page:
        await page.set_content(
            """<button id="mode" aria-label="打开模式选择器，当前模式为“Flash 扩展”"
              onclick="document.querySelector('#menu').hidden = !document.querySelector('#menu').hidden">
              Flash 扩展</button>
            <div id="menu" role="menu" hidden>
              <div role="menuitem" class="active" id="lite">3.5 Flash-Lite</div>
              <div role="menuitem" class="selected" id="flash">3.8 Flash
                <span aria-label="已选中"></span></div>
              <div role="menuitem" id="pro" onclick="
                document.querySelector('#flash').classList.remove('selected');
                this.classList.add('selected');
                document.querySelector('#mode').setAttribute(
                  'aria-label', '打开模式选择器，当前模式为“3.1 Pro”');
                document.querySelector('#menu').hidden = true">3.1 Pro</div>
            </div>"""
        )
        actions = GeminiActions(page)

        success, selected = await actions.set_model("Pro")

        assert success is True
        assert selected == "3.1 Pro"
        assert await page.locator("#pro").get_attribute("class") == "selected"
        assert await page.locator("#lite").get_attribute("class") == "active"


@pytest.mark.asyncio
async def test_set_model_rejects_unchanged_selection_when_button_says_flash(
    browser_path: str,
) -> None:
    """模型按钮文本相同但点击未改变菜单选中态时不能报成功。"""
    async with _new_page(browser_path) as page:
        await page.set_content(
            "<button class='input-area-switch' "
            "aria-label='打开模式选择器，当前模式为 Flash' "
            "onclick=\"const menu = document.querySelector('#menu'); "
            "menu.hidden = !menu.hidden\">Flash</button>"
            "<div id='menu' role='menu' hidden>"
            "<div id='flash38' role='menuitemradio' aria-label='3.8 Flash' "
            "aria-checked='false' onclick=\"document.querySelector('#menu').hidden = true\">"
            "3.8 Flash</div>"
            "<div id='flash36' role='menuitemradio' aria-label='3.6 Flash' "
            "aria-checked='false'>3.6 Flash</div>"
            "</div>"
        )
        actions = GeminiActions(page)

        success, _ = await actions.set_model("Flash")

        assert success is False
        assert await page.locator("#flash38").get_attribute("aria-checked") == "false"
        assert await page.locator("#flash36").get_attribute("aria-checked") == "false"
