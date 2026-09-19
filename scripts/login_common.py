"""登录脚本共用基础设施：普通浏览器登录 + 登录态校验。

登录流程刻意**不经过任何自动化驱动**：以子进程启动本机 Chrome，只传
``--user-data-dir``（与可选 ``--proxy-server``），没有任何会被 Chromium 判为
危险参数、或暴露自动化环境的启动项，因此不会出现"不受支持的命令行标记"横幅，
站点也不会把登录环境判定为自动化（实测：带 ``--no-sandbox`` /
``--disable-blink-features`` 时 Google 直接拒绝登录）。

用户在普通 Chrome 里手动完成登录后关闭窗口，脚本再用插件运行时同一套会话
（``BrowserSessionManager``）做一次无头校验：确认站点就绪并保存账号资产。

配置读同一份：``config/plugins/ai_ui_snapshot/config.toml``（经插件配置类加载），
登录态目录 / 代理 / 浏览器路径都跟随插件配置，不另写默认值。
"""

from __future__ import annotations

import asyncio
import pathlib
import subprocess
import sys
from typing import Awaitable, Callable

import playwright.async_api as pw

# 插件根与项目根：脚本无论从哪里启动都用绝对路径定位，避免按 cwd 误建目录
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROJECT_ROOT = PLUGIN_ROOT.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "plugins" / "ai_ui_snapshot" / "config.toml"

# 插件根必须在项目根之前：项目根有个同名 ``config/`` 目录（命名空间包），
# 顺序反了会让 ``from config import AiUiSnapshotConfig`` 解析到目录而非插件模块。
for _p in (str(PROJECT_ROOT), str(PLUGIN_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))

from config import AiUiSnapshotConfig  # noqa: E402
from services.base.browser_session import BrowserSessionManager, resolve_browser_path  # noqa: E402

# 等待浏览器关闭：最长 30 分钟（够慢速手工登录），每 2 秒扫一次进程
BROWSER_WAIT_TIMEOUT_S = 1800.0
BROWSER_POLL_INTERVAL_S = 2.0


def load_plugin_config() -> AiUiSnapshotConfig:
    """读取插件配置（与运行时同一份 config.toml）。

    Returns:
        AiUiSnapshotConfig: 插件配置；文件缺失或损坏时回退到模型默认值。
    """
    if CONFIG_PATH.is_file():
        try:
            return AiUiSnapshotConfig.load(CONFIG_PATH)
        except Exception as exc:  # noqa: BLE001 - 配置异常不阻塞登录
            print(f"  ! 读取插件配置失败，改用默认值: {exc}")
    else:
        print(f"  ! 未找到插件配置 {CONFIG_PATH}，改用默认值")
    return AiUiSnapshotConfig()


def site_profile_dir(config: AiUiSnapshotConfig, site: str) -> pathlib.Path:
    """返回站点的登录态目录（跟随插件配置的会话目录）。

    Args:
        config: 插件配置。
        site: 站点标识（deepseek / gemini / doubao）。

    Returns:
        pathlib.Path: 登录态目录绝对路径。
    """
    root = pathlib.Path(config.web.web_profile_dir)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root / site


def browser_pids(profile_dir: pathlib.Path) -> list[int]:
    """返回占用指定登录态目录的 Chrome 进程 PID（只枚举，不杀进程）。

    Args:
        profile_dir: 登录态目录。

    Returns:
        list[int]: 进程 PID 列表；无匹配时为空。
    """
    quoted = str(profile_dir).replace("'", "''")
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$ErrorActionPreference='SilentlyContinue'; "
        f"$p='{quoted}'; "
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -like \"*$p*\" } | "
        "ForEach-Object { $_.ProcessId }"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    pids: list[int] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


async def wait_browser_closed(profile_dir: pathlib.Path) -> bool:
    """等待用户关闭浏览器（按登录态目录识别进程）。

    以连续两次探测为空作为关闭判据，避免刚启动、进程尚未出现时提前判定结束。

    Args:
        profile_dir: 登录态目录。

    Returns:
        bool: 是否在超时前观测到浏览器关闭。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + BROWSER_WAIT_TIMEOUT_S
    empty_streak = 0
    seen_running = False
    while loop.time() < deadline:
        pids = await asyncio.to_thread(browser_pids, profile_dir)
        if pids:
            seen_running = True
            empty_streak = 0
        else:
            empty_streak += 1
            if seen_running and empty_streak >= 2:
                return True
            # 从未观测到运行（启动失败）时，连续 3 次为空即判失败
            if not seen_running and empty_streak >= 3:
                return False
        await asyncio.sleep(BROWSER_POLL_INTERVAL_S)
    return False


async def verify_login(
    *,
    site: str,
    config: AiUiSnapshotConfig,
    is_ready: Callable[[pw.Page], Awaitable[bool]],
    on_ready: Callable[[pw.Page, pathlib.Path], Awaitable[None]] | None = None,
    describe: Callable[[pw.Page], Awaitable[str]] | None = None,
) -> bool:
    """用插件运行时同一套会话校验登录态（无头），并保存账号资产。

    Args:
        site: 站点标识。
        config: 插件配置。
        is_ready: 就绪判定。
        on_ready: 就绪后的站点动作（如保存头像）。
        describe: 可选，返回当前账号描述（用于打印）。

    Returns:
        bool: 登录态是否有效（站点就绪）。
    """
    manager = BrowserSessionManager(
        profile_root=config.web.web_profile_dir,
        theme=site,
        headless=True,
        browser_path=resolve_browser_path(config.screenshot.browser_path),
        proxy_url=(config.web.proxy_url or "").strip(),
    )
    try:
        session = await manager.get("login_verify", theme=site)
        await session.page.wait_for_timeout(2500)
        if not await is_ready(session.page):
            return False
        if describe is not None:
            label = await describe(session.page)
            print(f"  ✓ 当前账号: {label or '（未能读取账号信息）'}")
        if on_ready is not None:
            await on_ready(session.page, site_profile_dir(config, site))
        return True
    finally:
        await manager.close_all()


async def run_login_flow(
    *,
    site: str,
    url: str,
    is_ready: Callable[[pw.Page], Awaitable[bool]],
    on_ready: Callable[[pw.Page, pathlib.Path], Awaitable[None]] | None = None,
    describe: Callable[[pw.Page], Awaitable[str]] | None = None,
) -> int:
    """执行一次交互式登录（普通 Chrome，无自动化驱动）并校验。

    步骤：启动本机 Chrome 打开站点 → 用户手动登录 → 关闭浏览器 →
    无头会话校验登录态并保存账号资产。

    Args:
        site: 站点标识（决定登录态目录）。
        url: 站点地址。
        is_ready: 就绪判定。
        on_ready: 就绪后的站点动作（如保存头像）。
        describe: 可选，返回当前账号描述。

    Returns:
        int: 进程退出码（0 成功，1 失败/超时）。
    """
    config = load_plugin_config()
    profile = site_profile_dir(config, site)
    profile.mkdir(parents=True, exist_ok=True)
    browser = resolve_browser_path(config.screenshot.browser_path)
    proxy = (config.web.proxy_url or "").strip()

    print(f"== {site} 登录 ==")
    print(f"  登录态目录: {profile}")
    print(f"  登录地址:   {url}")
    print(f"  代理:       {proxy or '（直连）'}")
    print(f"  Chrome:     {browser or '（未找到：请安装 Chrome 或配置 screenshot.browser_path）'}")
    if not browser:
        return 1

    cmd = [browser, f"--user-data-dir={profile}"]
    if proxy:
        cmd.append(f"--proxy-server={proxy}")
    cmd.append(url)
    print("  已打开普通 Chrome（无自动化驱动、无特殊启动参数）。")
    print("  请在浏览器里完成登录（换账号：先在里面退出旧账号，再登新账号）。")
    print("  登录完成后关闭该浏览器窗口，脚本会自动校验登录态并保存。")
    proc = subprocess.Popen(cmd)
    try:
        if not await wait_browser_closed(profile):
            print("  等待浏览器关闭超时，未完成校验。")
            return 1
    finally:
        if proc.poll() is None:
            proc.terminate()

    print("  ✓ 浏览器已关闭，正在校验登录态…")
    if not await verify_login(
        site=site, config=config, is_ready=is_ready, on_ready=on_ready, describe=describe
    ):
        print("  ✗ 登录态校验未通过：站点未就绪（通常表示尚未完成登录）。")
        print("    请重新运行本脚本并完成登录。")
        return 1
    print("  ✓ 登录成功，登录态已持久化，可重启 bot 使用。")
    return 0
