"""登录脚本验证：配置同源 + 真实登录态就绪判定（无需人工操作）。

验证两件事（都不需要手动登录、不提问）：

1. **配置同源**：登录脚本读的登录态目录 / 代理 / 浏览器路径，与运行时
   ``BrowserSessionManager`` 实际使用的一致（不存在第二份默认值）。
2. **就绪判定有效**：用运行时同一套会话（真实 profile、真实站点）执行登录
   脚本的 ``is_ready``，确认能正确识别「已登录」；顺带读出 Gemini 当前账号。

**运行前提**：bot 已关闭（同一 profile 双开 Chrome 会崩溃）。

用法：
    set PYTHONIOENCODING=utf-8 && uv run python scripts/verify_login_readiness.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import login_common  # noqa: E402  （完成 sys.path / 工作目录准备）
import login_deepseek  # noqa: E402
import login_doubao  # noqa: E402
import login_gemini  # noqa: E402
from services.base.browser_session import (  # noqa: E402
    BrowserSessionManager,
    resolve_browser_path,
)

FAILED: list[str] = []
PASSED = 0

# 站点 → （登录脚本模块, 就绪判定函数名）
SITES: dict[str, object] = {
    "deepseek": login_deepseek,
    "gemini": login_gemini,
    "doubao": login_doubao,
}


def check(name: str, cond: bool, detail: str = "") -> None:
    """记录一条断言结果。"""
    global PASSED
    if cond:
        PASSED += 1
        print(f"  [OK]   {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}  {detail}")


async def main() -> int:
    """执行登录脚本验证。"""
    config = login_common.load_plugin_config()
    browser = resolve_browser_path(config.screenshot.browser_path)
    proxy = (config.web.proxy_url or "").strip()
    print("== 登录脚本验证 ==")
    print(f"  插件配置: {login_common.CONFIG_PATH}")
    print(f"  会话目录: {config.web.web_profile_dir}")
    print(f"  代理:     {proxy or '（直连）'}")
    print(f"  浏览器:   {browser or '（Playwright 自带）'}")

    check("配置读取成功（与运行时同一份 config.toml）", login_common.CONFIG_PATH.is_file())

    for site, module in SITES.items():
        print(f"\n=== {site} ===")
        manager = BrowserSessionManager(
            profile_root=config.web.web_profile_dir,
            theme=site,
            headless=True,
            browser_path=browser,
            proxy_url=proxy,
        )
        check(
            f"{site}: 登录脚本与运行时用同一登录态目录",
            login_common.site_profile_dir(config, site).resolve() == manager.profile_dir.resolve(),
            f"script={login_common.site_profile_dir(config, site)} runtime={manager.profile_dir}",
        )
        check(
            f"{site}: 登录态目录存在（尚未登录时为首次运行）",
            manager.profile_dir.exists(),
            f"dir={manager.profile_dir}",
        )
        try:
            session = await manager.get(f"verify_login_{site}", theme=site)
            await session.page.wait_for_timeout(2500)
            is_ready = getattr(module, "is_ready")
            try:
                ready = bool(await is_ready(session.page))
                check(f"{site}: 就绪判定脚本可执行（无 JS 错误）", True)
            except Exception as exc:  # noqa: BLE001 - 脚本错误即失败
                ready = False
                check(f"{site}: 就绪判定脚本可执行（无 JS 错误）", False, f"{type(exc).__name__}: {exc}")
            print(f"  就绪判定结果: {ready}（该站点当前{'已登录' if ready else '未登录或未就绪'}）")
            if site == "gemini":
                label = await login_gemini.read_account(session.page)
                check("gemini: 可读取当前账号文案", bool(label), f"label={label!r}")
                print(f"  当前账号: {label or '（未登录）'}")
        except Exception as exc:  # noqa: BLE001 - 会话失败记录即可
            FAILED.append(f"{site}: 会话异常 {exc}")
            print(f"  [FAIL] {site}: 会话异常 {type(exc).__name__}: {exc}")
        finally:
            await manager.close_all()

    print(f"\n== 结果 == 通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  !! {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
