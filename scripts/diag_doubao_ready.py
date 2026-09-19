"""豆包就绪检测诊断脚本（一次性，定位 login_doubao 检测误判原因）。

用已保存的登录态打开豆包，dump 输入区真实结构（textarea 是否存在、
"登录"按钮检测是否误报），结果输出到 stdout，用于校准就绪判定与探测脚本。

用法（项目根目录）：
    uv run python plugins/ai_ui_snapshot/scripts/diag_doubao_ready.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from typing import Any

import playwright.async_api as pw

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _PLUGIN_ROOT.parent.parent
_PROFILE_ROOT = pathlib.Path(
    __import__("os").environ.get("AI_UI_SNAPSHOT_PROFILE_ROOT", _PROJECT_ROOT / "data/ai_ui_snapshot_profile")
)
PROFILE_DIR = _PROFILE_ROOT / "doubao"
TARGET_URL = "https://www.doubao.com/chat/"


def _default_chrome_path() -> str:
    """探测正式版 Chrome 路径（找不到返回空）。"""
    candidates = (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    )
    for cand in candidates:
        if pathlib.Path(cand).is_file():
            return cand
    return ""


DIAG_SCRIPT = """() => {
    const visible = (el) => el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0;
    // 1. 所有 textarea 与 contenteditable（输入框真实形态）
    const textareas = Array.from(document.querySelectorAll('textarea')).map(el => ({
        classes: (el.className || '').toString().slice(0, 200),
        placeholder: el.getAttribute('placeholder') || '',
        visible: visible(el),
        id: el.id || '',
        ariaLabel: el.getAttribute('aria-label') || ''
    }));
    const editables = Array.from(document.querySelectorAll('[contenteditable="true"]')).map(el => ({
        tag: el.tagName,
        classes: (el.className || '').toString().slice(0, 200),
        ariaLabel: el.getAttribute('aria-label') || '',
        visible: visible(el)
    }));
    // 2. 精确文本"登录"的可点击元素（就绪判定的否定信号）
    const loginEls = [];
    document.querySelectorAll('button, a, [role="button"], span, div').forEach(el => {
        if (el.children.length > 2) return;
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (t === '登录' && visible(el)) {
            loginEls.push({tag: el.tagName, classes: (el.className || '').toString().slice(0, 150),
                           ariaLabel: el.getAttribute('aria-label') || ''});
        }
    });
    // 3. 就绪正面信号探测（常见提示语）
    const bodyText = (document.body.innerText || '').replace(/\\s+/g, ' ');
    const readyMarkers = ['发消息', '有什么我能帮你的吗', '新对话', '历史对话', ' 由AI生成 ', '内容由 AI 生成'];
    const markersHit = readyMarkers.filter(m => bodyText.includes(m));
    // 4. 页面元信息
    return {
        title: document.title,
        url: location.href,
        textareas, editables, loginEls,
        markersHit,
        bodyTextPreview: bodyText.slice(0, 300)
    };
}"""


async def main() -> int:
    """运行就绪检测诊断。"""
    chrome = _default_chrome_path()
    async with pw.async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(PROFILE_DIR),
            "headless": False,
            "viewport": {"width": 1440, "height": 900},
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-infobars"],
            "ignore_default_args": ["--enable-automation"],
        }
        if chrome:
            launch_kwargs["executable_path"] = chrome
        context = await p.chromium.launch_persistent_context(**launch_kwargs)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000)
            result = await page.evaluate(DIAG_SCRIPT)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        finally:
            await context.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
