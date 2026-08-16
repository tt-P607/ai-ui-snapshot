"""Gemini 交互式登录脚本（可靠就绪检测）。

打开有头浏览器，让用户为 bot 账号在 gemini.google.com 完成登录，
检测到 Gemini 输入框真实就绪（div.ql-editor）后才判定登录成功并持久化登录态。
登录成功后自动提取 Google 头像资产并保存到 profile 目录。

用法（插件目录）：
    uv run python scripts/login_gemini.py

说明：
- 依赖本地代理（默认 http://127.0.0.1:7890）访问 Gemini；可用环境变量
  HTTPS_PROXY / HTTP_PROXY / ALL_PROXY 覆盖。
- 登录态目录默认相对项目根目录（``data/ai_ui_snapshot_profile/gemini/``），
  已被 ``.gitignore`` 忽略，不随插件发布。
- 建议为 bot 注册独立账号，避免暴露个人账号数据。
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import time
from typing import Any

import playwright.async_api as pw

# 登录态目录：优先用环境变量 AI_UI_SNAPSHOT_PROFILE_ROOT，否则相对项目根
_PROFILE_ROOT = pathlib.Path(os.environ.get("AI_UI_SNAPSHOT_PROFILE_ROOT", "data/ai_ui_snapshot_profile"))
PROFILE_DIR = _PROFILE_ROOT / "gemini"
TARGET_URL = "https://gemini.google.com/app"
LOGIN_TIMEOUT_S = 600
POLL_INTERVAL_S = 2.0


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


def _proxy_server() -> str:
    """解析代理地址（环境变量优先，缺省本地代理）。"""
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("ALL_PROXY")
        or "http://127.0.0.1:7890"
    )


# 彻底抹除自动化指纹的初始化脚本（Google 反自动化检测只信任正式版 Chrome）
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});
window.chrome = {runtime: {}};
const origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (origQuery) {
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : origQuery(parameters)
    );
}
"""


async def _is_gemini_ready(page: pw.Page) -> bool:
    """检测 Gemini 主界面是否就绪（输入框存在且可见、无主导航登录按钮）。"""
    try:
        return bool(
            await page.evaluate(
                """() => {
                    const editor = document.querySelector('div.ql-editor[contenteditable="true"], div[role="textbox"][aria-label*="Gemini"], div[role="textbox"][aria-label*="提示"]');
                    if (!editor) return false;
                    const r = editor.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) return false;
                    const btns = Array.from(document.querySelectorAll('button, a[role="button"]'));
                    const loginBtn = btns.some(el => {
                        const t = (el.innerText || '').trim();
                        return t === '登录' || t === 'Sign in';
                    });
                    const accountBtn = btns.some(el => {
                        const a = el.getAttribute('aria-label') || '';
                        return a.includes('Google 账号') || a.includes('Google account');
                    });
                    return loginBtn === false && accountBtn === true;
                }"""
            )
        )
    except Exception:  # noqa: BLE001 - 页面未就绪时
        return False


async def _save_google_assets(page: pw.Page, profile_dir: pathlib.Path) -> None:
    """提取并保存 Google 账号的真实头像与用户元数据。"""
    try:
        data = await page.evaluate(
            """() => {
                const img = document.querySelector('img[src*="googleusercontent.com"], a[aria-label*="Google"] img, button.gb_d img, header img');
                const accBtn = document.querySelector('[aria-label*="Google 账号"], [aria-label*="Google account"]');
                return {
                    avatar_url: img ? img.src : null,
                    account_label: accBtn ? accBtn.getAttribute('aria-label') : null
                };
            }"""
        )
        if isinstance(data, dict):
            avatar_url = data.get("avatar_url")
            if avatar_url and isinstance(avatar_url, str):
                resp = await page.request.get(avatar_url)
                if resp.ok:
                    avatar_bytes = await resp.body()
                    avatar_path = profile_dir / "google_avatar.png"
                    avatar_path.write_bytes(avatar_bytes)
                    print(f"  ✓ 已保存 Google 真实头像: {avatar_path} ({len(avatar_bytes)} 字节)")
            account_label = data.get("account_label")
            if account_label:
                (profile_dir / "google_user.json").write_text(
                    account_label, encoding="utf-8"
                )
                print(f"  ✓ 已保存账号信息: {account_label!r}")
    except Exception as exc:  # noqa: BLE001 - 提取失败不阻塞登录
        print(f"  ! 提取 Google 账号资产跳过: {exc}")


async def main() -> int:
    """运行交互式 Gemini 登录流程。"""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    chrome = _default_chrome_path()
    proxy = _proxy_server()
    print("== Gemini 交互式登录 ==")
    print(f"  会话目录: {PROFILE_DIR}")
    print(f"  目标地址: {TARGET_URL}")
    print(f"  代理配置: {proxy}")
    print(f"  Chrome:   {chrome or '未找到，使用 Playwright 自带 Chromium'}")
    print("  请在浏览器中为 bot 账号完成登录；检测到输入框就绪即自动保存登录态...")

    async with pw.async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(PROFILE_DIR),
            "headless": False,
            "viewport": {"width": 1440, "height": 900},
            "proxy": {"server": proxy},
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
            await page.add_init_script(STEALTH_INIT_SCRIPT)
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)

            deadline = time.monotonic() + LOGIN_TIMEOUT_S
            while time.monotonic() < deadline:
                if await _is_gemini_ready(page):
                    print("  ✓ 登录成功，Gemini 输入框已就绪，登录态已持久化")
                    await _save_google_assets(page, PROFILE_DIR)
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
