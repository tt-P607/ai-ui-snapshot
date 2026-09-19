"""DeepSeek 登录脚本。

打开普通 Chrome（不经自动化驱动）到 chat.deepseek.com，让用户为 bot 账号完成
登录；用户关闭浏览器后脚本自动校验登录态。

用法（任意目录均可，脚本按自身位置定位项目根）：
    uv run python scripts/login_deepseek.py

说明：
- 登录态目录、代理、浏览器路径全部跟随插件配置
  （``config/plugins/ai_ui_snapshot/config.toml``）。
- 换账号：在打开的浏览器里退出旧账号，再用新账号登录。
- 登录态目录已被 ``.gitignore`` 忽略，不随插件发布。
"""

from __future__ import annotations

import asyncio
import sys

import playwright.async_api as pw

from login_common import run_login_flow

from services.deepseek import constants as DS  # noqa: E402


async def is_ready(page: pw.Page) -> bool:
    """检测 DeepSeek 是否已登录并就绪（就绪脚本见 :mod:`services.deepseek.constants`）。"""
    try:
        return bool(await page.evaluate(DS.READY_CHECK_SCRIPT, list(DS.READY_MARKERS)))
    except Exception:  # noqa: BLE001 - 页面未就绪时
        return False


async def main() -> int:
    """运行 DeepSeek 登录流程。"""
    return await run_login_flow(site="deepseek", url=DS.SITE_URL, is_ready=is_ready)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
