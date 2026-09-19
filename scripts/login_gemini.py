"""Gemini 登录 / 换账号脚本。

打开普通 Chrome（不经自动化驱动）到 gemini.google.com，让用户为 bot 账号完成
登录；用户关闭浏览器后脚本自动校验登录态，并提取 Google 头像与账号信息保存到
登录态目录（截图外壳装饰用）。

用法（任意目录均可，脚本按自身位置定位项目根）：
    uv run python scripts/login_gemini.py

换账号：在打开的浏览器里点右上角头像 →「退出」→ 用新账号登录 → 关闭窗口。
脚本结束前会打印校验后的当前账号。

说明：
- 登录态目录、代理、浏览器路径全部跟随插件配置
  （``config/plugins/ai_ui_snapshot/config.toml``）。
- 刻意不传 ``--no-sandbox`` / ``--disable-blink-features`` 等启动参数：Chromium
  把它们判定为危险参数并弹出"不受支持的命令行标记"横幅，Google 登录流程会
  据此拒绝登录（实测）。详见 :mod:`login_common` 与 :mod:`services.base.browser_session`。
- 登录态目录已被 ``.gitignore`` 忽略，不随插件发布。
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

import playwright.async_api as pw

from login_common import run_login_flow

from services.gemini import constants as GC  # noqa: E402

# Gemini 官方入口（与运行时站点地址同源）
TARGET_URL = GC.SITE_URL


async def is_ready(page: pw.Page) -> bool:
    """检测 Gemini 是否已登录并就绪（就绪脚本见 :mod:`services.gemini.constants`）。"""
    try:
        return bool(await page.evaluate(GC.READY_CHECK_SCRIPT, GC.INPUT_SELECTOR))
    except Exception:  # noqa: BLE001 - 页面未就绪时
        return False


async def read_account(page: pw.Page) -> str:
    """读取当前 Google 账号按钮文案（未登录时为空）。"""
    try:
        return str(await page.evaluate(GC.ACCOUNT_LABEL_SCRIPT) or "").strip()
    except Exception:  # noqa: BLE001 - 页面未就绪时
        return ""


async def save_account_assets(page: pw.Page, profile_dir: pathlib.Path) -> None:
    """提取并保存 Google 账号的真实头像与账号信息。"""
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
        if not isinstance(data, dict):
            return
        avatar_url = data.get("avatar_url")
        if avatar_url and isinstance(avatar_url, str):
            resp = await page.request.get(avatar_url)
            if resp.ok:
                raw = await resp.body()
                (profile_dir / "google_avatar.png").write_bytes(raw)
                print(f"  ✓ 已保存 Google 头像: {len(raw)} 字节")
        label = data.get("account_label")
        if label:
            (profile_dir / "google_user.json").write_text(str(label), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - 提取失败不阻塞登录
        print(f"  ! 提取 Google 账号资产跳过: {exc}")


async def main() -> int:
    """运行 Gemini 登录流程。"""
    return await run_login_flow(
        site="gemini",
        url=TARGET_URL,
        is_ready=is_ready,
        on_ready=save_account_assets,
        describe=read_account,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
