"""DeepSeek 交互式登录脚本。

打开有头浏览器，让用户为 bot 账号在 chat.deepseek.com 完成一次手动登录，
登录态（Cookie）持久化到插件登录态目录（``data/ai_ui_snapshot_profile/deepseek/``），
供插件网页实时模式复用。

用法（插件目录）：
    uv run python scripts/login_deepseek.py

说明：
- 登录态目录默认相对项目根目录（``data/ai_ui_snapshot_profile/deepseek/``），
  已被 ``.gitignore`` 忽略，不随插件发布。
- 建议为 bot 注册独立账号，避免暴露个人账号数据。
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import time
from typing import Any

import playwright.async_api as pw

# 登录态目录：优先用环境变量 AI_UI_SNAPSHOT_PROFILE_ROOT，否则相对项目根
_PROFILE_ROOT = pathlib.Path(
    __import__("os").environ.get("AI_UI_SNAPSHOT_PROFILE_ROOT", "data/ai_ui_snapshot_profile")
)
PROFILE_DIR = _PROFILE_ROOT / "deepseek"
TARGET_URL = "https://chat.deepseek.com/"
LOGIN_TIMEOUT_S = 600
POLL_INTERVAL_S = 2.0

# 任一就绪标志出现即视为已登录进入对话界面
READY_MARKERS = ["给 DeepSeek 发送消息", "发送消息", "开启新对话", "快速模式", "深度思考"]


def _default_chrome_path() -> str:
    """探测系统中已安装的正式版 Chrome 路径（找不到返回空）。

    Returns:
        str: Chrome 可执行文件路径；未找到时返回空字符串。
    """
    candidates = (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    )
    for cand in candidates:
        if pathlib.Path(cand).is_file():
            return cand
    return ""


async def _is_deepseek_ready(page: pw.Page) -> bool:
    """检测 DeepSeek 主界面是否就绪（出现任一就绪标志且无主导航登录按钮）。"""
    try:
        return bool(
            await page.evaluate(
                """(markers) => {
                    const text = (document.body.innerText || '').replace(/\\s+/g, ' ');
                    const hasReady = markers.some(m => text.includes(m));
                    if (!hasReady) return false;
                    const login = ['登录', '手机号', '扫码'];
                    const hasLogin = login.some(m => text.includes(m));
                    return !hasLogin;
                }""",
                list(READY_MARKERS),
            )
        )
    except Exception:  # noqa: BLE001 - 页面未就绪时
        return False


async def main() -> int:
    """运行交互式 DeepSeek 登录流程。"""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    chrome = _default_chrome_path()
    print("== DeepSeek 交互式登录 ==")
    print(f"  会话目录: {PROFILE_DIR}")
    print(f"  目标地址: {TARGET_URL}")
    print(f"  Chrome:   {chrome or '未找到，使用 Playwright 自带 Chromium'}")
    print("  请在浏览器中为 bot 账号完成登录；检测到对话界面就绪即自动保存登录态...")

    async with pw.async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(PROFILE_DIR),
            "headless": False,
            "viewport": {"width": 1440, "height": 900},
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--start-maximized",
            ],
            "ignore_default_args": ["--enable-automation"],
        }
        if chrome:
            launch_kwargs["executable_path"] = chrome
        context = await p.chromium.launch_persistent_context(**launch_kwargs)

        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)

            deadline = time.monotonic() + LOGIN_TIMEOUT_S
            while time.monotonic() < deadline:
                if await _is_deepseek_ready(page):
                    print("  ✓ 登录成功，DeepSeek 对话界面已就绪，登录态已持久化")
                    print("  ✓ 浏览器即将自动关闭")
                    await page.wait_for_timeout(2000)
                    return 0
                await asyncio.sleep(POLL_INTERVAL_S)

            print("  登录超时，请确认已在浏览器中完成登录。")
            return 1
        finally:
            await context.close()
            print("  ✓ 浏览器已关闭")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
