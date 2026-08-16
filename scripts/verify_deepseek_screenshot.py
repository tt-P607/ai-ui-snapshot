"""DeepSeek 截图行为验证脚本（走插件完整逻辑）。

调用插件真实动作链 ``BrowserActions.screenshot()`` 验证长截图行为：
撑开 → 整页截图/分片 → 浏览器外壳横幅拼接 → 还原，并覆盖思考块展开/折叠、
侧边栏收起/展开参数。产物输出到本目录，供人工确认截图行为正确。

用法（插件目录，依赖项目根目录的登录态与依赖）：
    set PYTHONIOENCODING=utf-8 && uv run python scripts/verify_deepseek_screenshot.py

说明：
- 使用 ``data/ai_ui_snapshot_profile/deepseek/`` 登录态（需先运行
  ``scripts/login_deepseek.py`` 完成登录）。
- 产物保存为 ``scripts/verify_deepseek_*.png``。
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
from services.deepseek.actions import BrowserActions  # noqa: E402

PROFILE = PROJECT_ROOT / "data/ai_ui_snapshot_profile/deepseek"
OUT_DIR = pathlib.Path(__file__).resolve().parent
TARGET_URL = "https://chat.deepseek.com/"


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
        out = OUT_DIR / f"verify_deepseek_{tag}_{i + 1}.png"
        out.write_bytes(raw)
        print(f"  ✓ 已保存: {out.name} ({len(raw)} bytes)")


async def run_scene(
    page: Any,
    actions: BrowserActions,
    tag: str,
    think: str = "collapse",
    sidebar: str = "auto",
) -> None:
    """执行单个截图场景。

    Args:
        page: Playwright 页面。
        actions: 插件动作对象。
        tag: 场景标记。
        think: 思考块展开方式。
        sidebar: 侧边栏显示方式。
    """
    shots = await actions.screenshot("conversation", think=think, sidebar=sidebar)
    if not shots:
        print(f"[{tag}] 截图失败（返回空）")
        return
    print(f"[{tag}] 截图片数: {len(shots)}")
    _save_shots(shots, tag)


async def main() -> int:
    """运行 DeepSeek 截图验证流程。"""
    parser = argparse.ArgumentParser(description="DeepSeek 截图验证")
    parser.add_argument("--theme", choices=["light", "dark", "auto"], default="auto")
    args = parser.parse_args()

    if not PROFILE.exists():
        print(f"未找到登录态目录: {PROFILE}")
        print("请先运行 scripts/login_deepseek.py 完成 DeepSeek 登录。")
        return 1

    chrome = _chrome_path()
    print(f"== DeepSeek 截图验证 ==  profile={PROFILE}")
    print(f"  Chrome: {chrome or '未找到，用 Playwright 自带 Chromium'}")

    async with pw.async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(PROFILE),
            "headless": True,
            "viewport": {"width": 1440, "height": 900},
            "device_scale_factor": 2,
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

            actions = BrowserActions(
                page,
                max_screenshot_height=8000,
                decoration_enabled=True,
                decoration_theme=args.theme,
            )

            # 进入最近的历史会话，确保截图有真实对话内容
            titles = await actions.list_conversations()
            print(f"历史会话: {titles[:5] if titles else '无'}")
            if titles:
                entered = await actions.open_conversation(titles[0])
                print(f"进入历史会话 [{titles[0]}]: {'成功' if entered else '失败'}")
                await page.wait_for_timeout(2000)

            # 场景1：默认（思考折叠 + 侧边栏保持）
            print("=== 场景1: 默认（思考折叠 + 侧边栏保持）===")
            await run_scene(page, actions, "default", think="collapse", sidebar="auto")

            # 场景2：侧边栏收起
            print("=== 场景2: 侧边栏收起 ===")
            await run_scene(page, actions, "sidebar_hide", think="collapse", sidebar="hide")

            # 场景3：思考块展开
            print("=== 场景3: 思考块展开 ===")
            await run_scene(page, actions, "think_expand", think="expand", sidebar="auto")

            print("验证完成，产物见本目录 verify_deepseek_*.png")
        finally:
            await context.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
