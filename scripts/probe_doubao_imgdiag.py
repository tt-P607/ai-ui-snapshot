"""豆包生图超时诊断（probe6：截图现场 + 全量 img dump）。

verify 场景 B 二轮"等待图片生成超时"——激活与提交均成功但
IMAGE_READY_SCRIPT 未命中。本脚本短等待后截屏并 dump 页面全部 img，
定位生成结果真实渲染位置。

用法（项目根目录，需已登录）：
    uv run python plugins/ai_ui_snapshot/scripts/probe_doubao_imgdiag.py
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
for _p in (str(_PLUGIN_ROOT), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from plugins.ai_ui_snapshot.scripts.login_doubao import _is_doubao_ready  # noqa: E402
from plugins.ai_ui_snapshot.services.doubao.actions import DoubaoActions  # noqa: E402
from plugins.ai_ui_snapshot.services.doubao.constants import (  # noqa: E402
    SKILL_IMAGE_BUTTON_ID,
    SKILL_IMAGE_PLACEHOLDER,
    SITE_URL,
)

_PROFILE_ROOT = pathlib.Path(
    __import__("os").environ.get("AI_UI_SNAPSHOT_PROFILE_ROOT", _PROJECT_ROOT / "data/ai_ui_snapshot_profile")
)
PROFILE_DIR = _PROFILE_ROOT / "doubao"
REPORT_DIR = _PLUGIN_ROOT / "scripts" / "doubao_probe_report"

DUMP_ALL_IMG_SCRIPT = """() => {
    const dump = (im) => ({
        src: (im.src || '').slice(0, 140),
        type: im.src ? (im.src.startsWith('blob:') ? 'blob' : im.src.startsWith('data:') ? 'data' : im.src.startsWith('http') ? 'http' : 'other') : 'empty',
        w: im.naturalWidth, h: im.naturalHeight, complete: im.complete,
        display: Math.round(im.getBoundingClientRect().width) + 'x' + Math.round(im.getBoundingClientRect().height),
        inVlist: Boolean(im.closest('[class*="v_list"]')),
        classes: (im.className || '').toString().slice(0, 90),
        parentClasses: (im.parentElement && im.parentElement.className || '').toString().slice(0, 90)
    });
    const all = Array.from(document.querySelectorAll('img')).map(dump);
    const vlistExists = Boolean(document.querySelector('[class*="v_list"]'));
    const bodyText = (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 400);
    return {imgTotal: all.length, imgs: all.slice(0, 30), vlistExists, bodyText, url: location.href};
}"""


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


async def main() -> int:
    """运行生图诊断：提交后 45s 采样现场。"""
    chrome = _default_chrome_path()
    report: dict[str, Any] = {}
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
            await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
            if not await _is_doubao_ready(page):
                print("✗ 登录态失效")
                return 1
            actions = DoubaoActions(page)
            if not await actions.new_chat():
                print("✗ 新建对话失败")
                return 1
            await page.wait_for_timeout(2000)
            ok, msg = await actions.activate_skill(SKILL_IMAGE_BUTTON_ID, SKILL_IMAGE_PLACEHOLDER)
            print(f"激活: ok={ok} msg={msg}")
            if not ok:
                return 1
            ok, msg = await actions.submit_prompt("一只柴犬戴宇航员头盔，可爱插画")
            print(f"提交: ok={ok} msg={msg}")
            if not ok:
                return 1
            # 15s / 45s / 90s 三次采样
            prev_wait = 0
            for wait_s in (15, 45, 90):
                await page.wait_for_timeout((wait_s - prev_wait) * 1000)
                report[f"at_{wait_s}s"] = await page.evaluate(DUMP_ALL_IMG_SCRIPT)
                await page.screenshot(path=str(REPORT_DIR / f"p6_img_{wait_s}s.png"))
                n = report[f"at_{wait_s}s"]["imgTotal"]
                print(f"  采样 {wait_s}s: img 总数 {n}")
                prev_wait = wait_s
        finally:
            await context.close()
    out = REPORT_DIR / "report6.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
