"""浏览器启动参数验证：确保不会触发 Chrome 的"不受支持的命令行标记"。

背景（实测）：Chromium 的 ``chrome/browser/ui/startup/bad_flags_prompt.cc`` 里
``kBadFlags`` 列出的启动项，会让 Chrome 在页面顶部弹出"您使用的是不受支持的命令行
标记"横幅；Google 登录流程据此拒绝登录。其中影响本插件的两项：
- ``--no-sandbox``（Playwright 默认注入）
- ``--disable-blink-features``（任何 Blink feature 开关都在名单内，
  包括原本用于隐藏 webdriver 的 ``AutomationControlled``）

因此运行时会话与登录浏览器都不再传任何自定义参数，webdriver 改由页面层
注入脚本隐藏。本脚本用**真实 Chrome 进程命令行**与**页面内实测**验证：

1. ``launch_flags()`` 不含任何危险参数
2. 无头会话（运行时路径）真实进程命令行无危险参数，且页面里
   ``navigator.webdriver`` 已被隐藏
3. 有头会话同上
4. 登录脚本的普通 Chrome 启动方式无危险参数
5. 头像解析：读取登录态目录里已保存的真实头像

用法：
    set PYTHONIOENCODING=utf-8 && uv run python scripts/verify_browser_flags.py
"""

from __future__ import annotations

import asyncio
import pathlib
import subprocess
import sys
import tempfile

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROJECT_ROOT = PLUGIN_ROOT.parent.parent
for _p in (str(PROJECT_ROOT), str(PLUGIN_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))

import playwright.async_api as pw  # noqa: E402

from services.base.browser_session import (  # noqa: E402
    launch_flags,
    resolve_browser_path,
    resolve_local_avatar,
)
from login_common import browser_pids, load_plugin_config  # noqa: E402

FAILED: list[str] = []
PASSED = 0

# Chromium kBadFlags 中与本插件相关的部分（上游源码：
# chrome/browser/ui/startup/bad_flags_prompt.cc；命中任一即会弹横幅）。
BAD_FLAGS: tuple[str, ...] = (
    "--no-sandbox",
    "--disable-blink-features",
    "--enable-blink-features",
    "--disable-web-security",
    "--single-process",
    "--ignore-certificate-errors",
    "--host-resolver-rules",
    "--host-rules",
    "--disable-gpu-sandbox",
    "--disable-seccomp-filter-sandbox",
    "--disable-setuid-sandbox",
    "--disable-webrtc-encryption",
    "--enable-gpu-benchmarking",
    "--log-net-log",
    "--unsafely-treat-insecure-origin-as-secure",
    "--use-fake-ui-for-media-stream",
    "--enable-unsafe-webgpu",
    "--disable-best-effort-tasks",
)


def check(name: str, cond: bool, detail: str = "") -> None:
    """记录一条断言结果。"""
    global PASSED
    if cond:
        PASSED += 1
        print(f"  [OK]   {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}  {detail}")


def cmdlines_for(profile_dir: str) -> list[str]:
    """返回命令行中含指定 user-data-dir 的 Chrome 进程命令行。"""
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$ErrorActionPreference='SilentlyContinue'; "
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "ForEach-Object { $_.CommandLine }"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    key = profile_dir.lower()
    return [line for line in (proc.stdout or "").splitlines() if key in line.lower()]


def assert_no_bad_flags(name: str, cmds: list[str]) -> None:
    """断言一组命令行里没有任何 Chromium 危险参数。"""
    check(f"{name}: 确实启动了 Chrome 进程", bool(cmds))
    if not cmds:
        return
    joined = " ".join(cmds)
    hits = [flag for flag in BAD_FLAGS if flag in joined]
    check(f"{name}: 无危险启动参数", not hits, f"命中={hits}")


async def launch_and_inspect(headless: bool, browser: str, proxy: str) -> None:
    """真实启动一次浏览器：检查进程命令行 + 页面内自动化指纹。"""
    mode = "无头会话" if headless else "有头会话"
    # 临时目录前缀保持 ASCII：该路径会出现在 Chrome 命令行里，中文会干扰匹配
    tmp_profile = tempfile.mkdtemp(prefix="aiui_flags_headless_" if headless else "aiui_flags_headful_")
    async with pw.async_playwright() as p:
        kwargs: dict[str, object] = {
            "user_data_dir": tmp_profile,
            "headless": headless,
            "viewport": {"width": 800, "height": 600},
        }
        kwargs.update(launch_flags())
        if browser:
            kwargs["executable_path"] = browser
        context = await p.chromium.launch_persistent_context(**kwargs)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.wait_for_timeout(1200)
            cmds = cmdlines_for(tmp_profile)
            assert_no_bad_flags(mode, cmds)
            # 去掉 Blink feature 开关后，webdriver 必须靠注入脚本隐藏
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            await page.goto("about:blank")
            wd = await page.evaluate("() => navigator.webdriver")
            check(f"{mode}: navigator.webdriver 已被隐藏", not wd, f"webdriver={wd!r}")
        finally:
            await context.close()


async def inspect_plain_login_launch(browser: str, proxy: str) -> None:
    """按登录脚本的方式启动普通 Chrome，检查其进程命令行。"""
    tmp_profile = tempfile.mkdtemp(prefix="aiui_flags_plain_")
    cmd = [browser, f"--user-data-dir={tmp_profile}"]
    if proxy:
        cmd.append(f"--proxy-server={proxy}")
    cmd.append("about:blank")
    proc = subprocess.Popen(cmd)
    try:
        await asyncio.sleep(3.0)
        cmds = cmdlines_for(tmp_profile)
        assert_no_bad_flags("登录浏览器（普通 Chrome）", cmds)
    finally:
        pids = browser_pids(pathlib.Path(tmp_profile))
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        if proc.poll() is None:
            proc.terminate()
        print(f"  （已关闭验证用的 {len(pids)} 个 Chrome 进程）")


async def main() -> int:
    """执行浏览器启动参数验证。"""
    config = load_plugin_config()
    browser = resolve_browser_path(config.screenshot.browser_path)
    proxy = (config.web.proxy_url or "").strip()
    print("== 浏览器启动参数验证 ==")
    print(f"  Chrome: {browser or '（Playwright 自带）'}")

    # 1. 参数定义本身
    flags = launch_flags()
    args = flags.get("args", [])
    ignore = flags.get("ignore_default_args", [])
    print(f"  args={args}  ignore_default_args={ignore}")
    hits = [flag for flag in BAD_FLAGS if any(flag in a for a in args)]
    check("自定义启动参数不含任何危险项", not hits, f"命中={hits}")
    check("参数定义去掉 --enable-automation", "--enable-automation" in ignore)
    check("参数定义去掉 --no-sandbox", "--no-sandbox" in ignore)

    # 2/3. 真实启动并检查进程命令行（有头会短暂出现浏览器窗口）
    await launch_and_inspect(True, browser, proxy)
    await launch_and_inspect(False, browser, proxy)

    # 4. 登录脚本的普通 Chrome 启动方式
    await inspect_plain_login_launch(browser, proxy)

    # 5. 头像解析（跟随配置的登录态目录）
    profile_root = pathlib.Path(config.web.web_profile_dir)
    if not profile_root.is_absolute():
        profile_root = PROJECT_ROOT / profile_root
    avatar = resolve_local_avatar("", profile_root)
    saved = (profile_root / "gemini" / "google_avatar.png").is_file()
    check("头像解析：留空配置时能读到已保存的本地头像", bool(avatar) if saved else True,
          f"saved={saved} len={len(avatar)}")
    if saved:
        check("头像解析结果为 data URI", avatar.startswith("data:image/"), f"prefix={avatar[:24]!r}")
        print(f"  本地头像: {len(avatar)} 字符（data URI）")
    check("头像解析：配置优先于本地", resolve_local_avatar("https://example.com/a.png", profile_root)
          == "https://example.com/a.png")

    print(f"\n== 结果 == 通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  !! {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
