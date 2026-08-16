"""Gemini 截图行为验证脚本（走插件完整逻辑）。

调用插件真实动作链 ``GeminiActions.screenshot()`` 验证长截图行为：
应用主题 → 撑开整页 → 整页截图/分片 → 浏览器外壳横幅拼接 → 还原，
含侧边栏与左下角账号区渲染。产物输出到本目录，供人工确认截图行为正确。

用法（插件目录，依赖项目根目录的登录态与依赖）：
    set PYTHONIOENCODING=utf-8 && uv run python scripts/verify_gemini_screenshot.py

说明：
- 使用 ``data/ai_ui_snapshot_profile/gemini/`` 登录态（需先运行
  ``scripts/login_gemini.py`` 完成登录），并依赖本地代理访问 Gemini。
- 产物保存为 ``scripts/verify_gemini_*.png``。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
from typing import Any

import playwright.async_api as pw

# 使插件可被 import：脚本位于插件 scripts/ 下，需把插件根加入 sys.path；
# 插件内部依赖 src.*（Neo-MoFox 框架），故把项目根也加入 sys.path。
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROJECT_ROOT = PLUGIN_ROOT.parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))
# 插件内部用相对路径（data/...、google_avatar.png 等）解析登录态与头像资产，
# 依赖运行目录为项目根；脚本启动时切到项目根，与插件实际运行环境一致。
os.chdir(PROJECT_ROOT)

from services.base.browser_session import STEALTH_INIT_SCRIPT  # noqa: E402
from services.gemini.actions import GeminiActions  # noqa: E402

PROFILE = PROJECT_ROOT / "data/ai_ui_snapshot_profile/gemini"
OUT_DIR = pathlib.Path(__file__).resolve().parent
TARGET_URL = "https://gemini.google.com/app"


def _chrome_path() -> str:
    """探测系统中已安装的正式版 Chrome 路径（找不到返回空）。"""
    candidates = (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    )
    for cand in candidates:
        if pathlib.Path(cand).is_file():
            return cand
    return ""


def _proxy_server() -> str:
    """解析代理地址（环境变量优先，缺省本地代理）。"""
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("ALL_PROXY")
        or "http://127.0.0.1:7890"
    )


def _save_shots(shots: list[str], tag: str) -> None:
    """把插件返回的 data URI 列表落盘为 PNG 文件。

    Args:
        shots: data URI 列表（自上而下）。
        tag: 产物文件名标记。
    """
    for i, shot in enumerate(shots):
        if not shot.startswith("data:"):
            print(f"  ! 第 {i + 1} 片非 data URI，跳过")
            continue
        import base64

        raw = base64.b64decode(shot.split(",", 1)[1])
        out = OUT_DIR / f"verify_gemini_{tag}_{i + 1}.png"
        out.write_bytes(raw)
        print(f"  ✓ 已保存: {out.name} ({len(raw)} bytes)")


async def main() -> int:
    """运行 Gemini 截图验证流程。"""
    parser = argparse.ArgumentParser(description="Gemini 截图验证")
    parser.add_argument("--theme", choices=["light", "dark", "auto"], default="auto")
    parser.add_argument(
        "--short",
        action="store_true",
        help="把对话缩短为单条回复后再截图（验证短对话场景）",
    )
    args = parser.parse_args()

    if not PROFILE.exists():
        print(f"未找到登录态目录: {PROFILE}")
        print("请先运行 scripts/login_gemini.py 完成 Gemini 登录。")
        return 1

    chrome = _chrome_path()
    proxy = _proxy_server()
    print(f"== Gemini 截图验证 ==  profile={PROFILE}")
    print(f"  Chrome: {chrome or '未找到，用 Playwright 自带 Chromium'}")
    print(f"  Proxy:  {proxy}")

    async with pw.async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(PROFILE),
            "headless": True,
            "viewport": {"width": 1440, "height": 900},
            "device_scale_factor": 2,
            "proxy": {"server": proxy},
            "args": ["--disable-blink-features=AutomationControlled", "--disable-infobars"],
            "ignore_default_args": ["--enable-automation"],
        }
        if chrome:
            launch_kwargs["executable_path"] = chrome
        context = await p.chromium.launch_persistent_context(**launch_kwargs)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            if chrome:
                await page.add_init_script(STEALTH_INIT_SCRIPT)
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)

            # 点击历史会话进入真实长对话（验证长对话侧边栏对齐与账号区）
            await page.evaluate(
                """() => {
                    const links = Array.from(document.querySelectorAll(
                        "mat-nav-list a[href*='/app/'], [class*='gem-nav-list-item'] a[href*='/app/'], a[href*='c/']"
                    ));
                    const target = links.find(a => (a.getAttribute('href') || '').length > 6);
                    if (target) target.click();
                }"""
            )
            await page.wait_for_timeout(4000)

            actions = GeminiActions(
                page,
                max_screenshot_height=8000,
                decoration_enabled=True,
                decoration_theme=args.theme,
            )

            # 场景1：真实长对话（默认主题，走插件完整 screenshot 链）
            print("=== 场景1: 真实长对话 ===")
            shots = await actions.screenshot("conversation", think="auto")
            if not shots:
                print("  截图失败（返回空）")
            else:
                print(f"  截图片数: {len(shots)}")
                _save_shots(shots, "longshot")

            # 场景2（可选）：缩短为短回复后再截图
            if args.short:
                print("=== 场景2: 短回复 ===")
                await page.evaluate(
                    """() => {
                        const cc = document.querySelector('.conversation-container');
                        if (!cc) return;
                        const responses = Array.from(cc.querySelectorAll('.response-container'));
                        for (let i = responses.length - 1; i > 0; i--) {
                            responses[i].remove();
                        }
                        const keep = cc.querySelector('.response-container .markdown, .response-container .model-response-text, .response-container .response-content');
                        if (keep) { keep.textContent = '好的，明白！'; }
                    }"""
                )
                await page.wait_for_timeout(500)
                shots = await actions.screenshot("conversation", think="auto")
                if not shots:
                    print("  截图失败（返回空）")
                else:
                    print(f"  截图片数: {len(shots)}")
                    _save_shots(shots, "short")

            print("验证完成，产物见本目录 verify_gemini_*.png")
        finally:
            await context.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
