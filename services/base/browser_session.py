"""任务级临时浏览器会话管理器。

LLM 处理一个任务时临时打开一个 Playwright 浏览器（复用 bot 账号登录态），
任务过程内按会话（stream_id）共享同一个页面，支持跨多次工具调用保持状态；
任务结束（空闲超时无活动）自动关闭，插件卸载时全部关闭，不常驻占用资源。
站点（deepseek/gemini 等）通过 ``theme`` 参数路由到各自的登录态 profile
与默认 URL，会话以 (theme, stream_id) 隔离。
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.kernel.concurrency import get_task_manager

from .utils import data_uri

logger = get_logger("ai_ui_snapshot.browser_session")

# 抹除自动化指纹的初始化脚本（Google 反自动化检测只信任正式版 Chrome + 无 webdriver 指纹）。
# 登录脚本（scripts/login_*.py）经 login_common 复用此脚本，不再各自维护一份。
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

# 浏览器启动的自动化相关参数（运行时会话与登录脚本共用同一份定义）。
#
# 不传任何自定义启动参数：Chromium 把 `--disable-blink-features` 整个开关判定为
# 危险参数（来源：chrome/browser/ui/startup/bad_flags_prompt.cc 的 kBadFlags，
# 注释明确写"These flags control Blink feature state, which is not supported"），
# Chrome 会因此在页面顶部弹出"您使用的是不受支持的命令行标记"横幅，
# Google 登录流程据此拒绝登录（实测：横幅消失后登录不再被拒）。
# navigator.webdriver 等自动化指纹改由 STEALTH_INIT_SCRIPT 在页面层抹除。
#
# 以下启动项必须从 Playwright 默认参数中剔除（它们是默认注入的，不是我们传的）：
# - --enable-automation：页面可见的自动化标记。
# - --no-sandbox：同上危险参数名单（Windows 桌面无需关闭沙箱）。
# - --disable-infobars：Playwright 注入，Chrome 76 起已废弃、无作用。
_LAUNCH_ARGS: tuple[str, ...] = ()
_IGNORE_DEFAULT_ARGS: tuple[str, ...] = (
    "--enable-automation",
    "--no-sandbox",
    "--disable-infobars",
)


def launch_flags() -> dict[str, list[str]]:
    """浏览器启动的自动化相关参数（运行时会话与登录脚本共用）。

    Returns:
        dict[str, list[str]]: 可直接合并进 ``launch_persistent_context`` 的关键字参数。
    """
    return {"args": list(_LAUNCH_ARGS), "ignore_default_args": list(_IGNORE_DEFAULT_ARGS)}


def resolve_local_avatar(configured: str, profile_root: pathlib.Path) -> str:
    """解析截图外壳装饰的头像：配置优先，留空时读登录态目录里已保存的真实头像。

    登录脚本会把站点账号头像存到 ``<profile_root>/<site>/google_avatar.png``；
    配置未指定头像时用这份真实头像（读成 data URI），使截图顶栏与登录账号一致。
    按传入的会话目录查找，不硬编码路径。

    Args:
        configured: 配置中的头像 URL（可空）。
        profile_root: 登录态根目录。

    Returns:
        str: 头像 URL 或 data URI；都取不到时返回空字符串（渲染时回退默认图标）。
    """
    if configured:
        return configured
    for site in ("gemini", "deepseek"):
        path = profile_root / site / "google_avatar.png"
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if raw:
            return data_uri(raw)
    return ""


# Windows 常见 Chrome 安装路径（运行时优先用真实 Chrome，避免 Playwright 自带被风控判定）
_DEFAULT_CHROME_CANDIDATES: tuple[str, ...] = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"$LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
)


def _default_browser_path() -> str:
    """探测系统中已安装的正式版 Chrome 路径（找不到返回空）。

    Returns:
        str: Chrome 可执行文件路径；未找到时返回空字符串。
    """
    for cand in _DEFAULT_CHROME_CANDIDATES:
        expanded = pathlib.Path(os.path.expandvars(cand))
        if expanded.is_file():
            return str(expanded)
    return ""


def resolve_browser_path(configured: str = "") -> str:
    """解析实际使用的浏览器可执行路径。

    配置了就用配置的，未配置时探测系统正式版 Chrome（Playwright 自带
    Chromium 的自动化指纹会被 Google 风控判定并登出）。运行时会话与
    登录脚本共用此解析，避免各自维护一份探测逻辑。

    Args:
        configured: 配置中的浏览器路径（可空）。

    Returns:
        str: 浏览器可执行路径；都不可用时返回空字符串（回退 Playwright 自带）。
    """
    return (configured or "").strip() or _default_browser_path()


# 本机 Chrome 的真实 UA 缓存（无头启动时显式指定，避免暴露无头特征）
_real_user_agent: str | None = None
_real_user_agent_resolved: bool = False


async def _resolve_real_user_agent(browser_path: str) -> str:
    """探测本机 Chrome 的真实 UA（去掉无头模式附加的 ``Headless`` 前缀）。

    Playwright 无头启动会把 UA 中的 ``Chrome`` 换成 ``HeadlessChrome``，
    该特征与 ``navigator.webdriver`` 一样可被站点风控直接识别（字节系站点
    会据此吊销登录态）。这里用一次性临时 profile 探测出等价 UA，供无头
    启动显式指定，使无头与有头的浏览器指纹一致。

    探测使用临时目录，不触碰任何站点登录态 profile；结果按进程缓存。

    Args:
        browser_path: Chrome 可执行文件路径（可空，空则跳过探测）。

    Returns:
        str: 真实 UA；无法探测时返回空字符串。
    """
    global _real_user_agent, _real_user_agent_resolved
    if _real_user_agent_resolved:
        return _real_user_agent or ""
    _real_user_agent_resolved = True
    if not browser_path:
        return ""
    tmp_dir = ""
    try:
        from playwright.async_api import async_playwright

        tmp_dir = tempfile.mkdtemp(prefix="ai_ui_snapshot_ua_")
        p = await async_playwright().start()
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=tmp_dir,
                headless=True,
                executable_path=browser_path,
                ignore_default_args=["--enable-automation"],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            raw = str(await page.evaluate("() => navigator.userAgent") or "")
        finally:
            await p.stop()
        ua = raw.replace("Headless", "")
        if ua and "Headless" not in ua:
            _real_user_agent = ua
            logger.info(f"已探测真实 UA 用于无头启动: {ua}")
        return ua
    except Exception as exc:  # noqa: BLE001 - 探测失败不阻塞启动
        logger.warning(f"探测真实 UA 失败，无头启动将保留默认 UA: {exc}")
        return ""
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@dataclass
class BrowserSession:
    """单个任务级浏览器会话。

    Attributes:
        stream_id: 关联的聊天流 ID。
        context: Playwright 浏览器上下文。
        playwright: Playwright 实例（关闭时 stop）。
        page: 当前页面对象（可能为 None）。
        last_active: 最后活动时间戳（epoch 秒）。
        busy: 当前处于活跃操作（提问/等待回复）的计数，大于 0 时空闲清理跳过。
        active_conversation: 当前活跃对话的稳定 ID（URL 中的会话 UUID）。
        active_conversation_title: 当前活跃对话标题（仅作展示/返回）。
    """

    stream_id: str
    context: Any
    playwright: Any = None
    page: Any = None
    last_active: float = field(default_factory=time.time)
    busy: int = 0
    active_conversation: str = ""
    active_conversation_title: str = ""

    def touch(self) -> None:
        """刷新最后活动时间（每次工具调用后调用）。"""
        self.last_active = time.time()

    def hold(self) -> None:
        """标记会话进入活跃操作，避免被空闲清理关闭。"""
        self.busy += 1

    def release(self) -> None:
        """退出活跃操作，允许空闲清理。"""
        if self.busy > 0:
            self.busy -= 1

    def set_active_conversation(self, conversation_id: str, title: str = "") -> None:
        """记录当前活跃对话的稳定 ID 与标题。

        Args:
            conversation_id: 会话稳定 ID（URL 中的 UUID）。
            title: 会话标题（仅展示用）。
        """
        self.active_conversation = conversation_id or ""
        if title:
            self.active_conversation_title = title


class BrowserSessionManager:
    """按 stream_id 管理任务级临时浏览器会话。

    用法：进程内单例。``get(stream_id, theme)`` 取或建会话；``touch(stream_id)``
    刷新活动时间；后台任务定期关闭空闲会话；``close_all`` 关闭全部。
    站点主题（deepseek/gemini/doubao）决定登录态 profile 目录与默认 URL。
    """

    def __init__(
        self,
        *,
        profile_root: str,
        theme: str = "deepseek",
        site_urls: dict[str, str] | None = None,
        idle_timeout_s: int = 600,
        headless: bool = True,
        forced_headful_themes: tuple[str, ...] = (),
        browser_path: str = "",
        proxy_url: str = "",
        page_theme: str = "auto",
        viewport_width: int = 1440,
        viewport_height: int = 900,
        device_scale_factor: int = 2,
        max_screenshot_height: int = 8000,
        decoration_enabled: bool = True,
        decoration_theme: str = "auto",
        decoration_avatar_url: str = "",
    ) -> None:
        """初始化管理器。

        Args:
            profile_root: 持久化浏览器会话根目录（含登录态）。
            theme: 默认站点主题（deepseek / gemini / doubao）。
            site_urls: 站点主题 → 默认 URL 映射（缺省用内置三站点地址）。
            idle_timeout_s: 空闲自动关闭秒数。
            headless: 是否无头。
            browser_path: Chromium 可执行路径（可空）。
            viewport_width: 浏览器视口宽度。
            viewport_height: 浏览器视口高度。
            device_scale_factor: 高清渲染倍率。
            max_screenshot_height: 长截图最大高度（像素）。
            decoration_enabled: 截图时是否叠加浏览器外壳装饰。
            decoration_theme: 外壳配色（auto/light/dark）。
            decoration_avatar_url: 自定义 Google 账号头像 URL。
        """
        self._profile_root = pathlib.Path(profile_root)
        self._theme = theme
        self._site_urls = dict(site_urls or {})
        self._idle_timeout_s = idle_timeout_s
        self._headless = headless
        self._forced_headful_themes = tuple(forced_headful_themes)
        self._browser_path = browser_path
        self._proxy_url = proxy_url
        self._page_theme = page_theme
        self._viewport_width = viewport_width
        self._viewport_height = viewport_height
        self._device_scale_factor = device_scale_factor
        self._max_screenshot_height = max_screenshot_height
        self._decoration_enabled = decoration_enabled
        self._decoration_theme = decoration_theme
        self._decoration_avatar_url = resolve_local_avatar(decoration_avatar_url, self._profile_root)
        self._sessions: dict[str, BrowserSession] = {}
        # 按 session_key 的创建互斥锁：同一会话首次并发获取时只启动一个浏览器，
        # 避免多个 Chrome 实例争用同一 profile 崩溃（exitCode=21）
        self._create_locks: dict[str, asyncio.Lock] = {}
        self._cleanup_task: asyncio.Task | None = None

    @property
    def profile_dir(self) -> pathlib.Path:
        """登录态持久化目录。"""
        return self._profile_root / self._theme

    @property
    def viewport(self) -> dict[str, int]:
        """浏览器视口尺寸。"""
        return {"width": self._viewport_width, "height": self._viewport_height}

    @property
    def device_scale_factor(self) -> int:
        """高清渲染倍率。"""
        return self._device_scale_factor

    @property
    def max_screenshot_height(self) -> int:
        """长截图最大高度（像素）。"""
        return self._max_screenshot_height

    @property
    def decoration_enabled(self) -> bool:
        """是否在截图时叠加浏览器外壳装饰。"""
        return self._decoration_enabled

    @property
    def page_theme(self) -> str:
        """页面明暗主题（auto/light/dark）。"""
        return self._page_theme

    @property
    def decoration_theme(self) -> str:
        """浏览器外壳配色（auto/light/dark）。"""
        return self._decoration_theme

    @property
    def decoration_avatar_url(self) -> str:
        """自定义 Google 账号头像 URL。"""
        return self._decoration_avatar_url

    @property
    def headless(self) -> bool:
        """是否以无头模式启动浏览器（媒体专用上下文跟随此配置）。"""
        return self._headless

    @property
    def browser_path_attr(self) -> str:
        """配置的浏览器可执行路径（可空）。"""
        return self._browser_path
    @property
    def profile_root_attr(self) -> pathlib.Path:
        """登录态持久化根目录。"""
        return self._profile_root

    @property
    def sessions(self) -> dict[str, BrowserSession]:
        """当前全部活跃会话（键为 theme:stream_id）。"""
        return dict(self._sessions)

    async def close_by_key(self, key: str) -> None:
        """按存储键关闭会话（供媒体生成等独立上下文清理同 profile 会话）。

        Args:
            key: 会话存储键（theme:stream_id）。
        """
        await self._close_by_key(key)

    async def close_all_theme(self, theme: str) -> None:
        """关闭指定站点的全部会话。

        Args:
            theme: 站点主题（deepseek/gemini/doubao）。
        """
        for key in [k for k in list(self._sessions) if k.startswith(f"{theme}:")]:
            await self._close_by_key(key)

    def _site_url(self, theme: str) -> str:
        """按站点主题返回默认网址（站点地址由映射决定，不暴露配置）。

        Args:
            theme: 站点主题（deepseek / gemini / doubao）。

        Returns:
            str: 目标网址。
        """
        return self._site_urls.get(theme) or {
            "deepseek": "https://chat.deepseek.com/",
            "gemini": "https://gemini.google.com/app",
            "doubao": "https://www.doubao.com/chat/",
        }.get(theme, "https://chat.deepseek.com/")

    def _key(self, stream_id: str, theme: str = "") -> str:
        """构造会话存储键（theme:stream_id）。

        Args:
            stream_id: 聊天流 ID。
            theme: 站点主题；空用管理器默认主题。

        Returns:
            str: 会话存储键。
        """
        return f"{theme or self._theme}:{stream_id}"

    async def get(self, stream_id: str, theme: str = "") -> BrowserSession:
        """获取（或创建）指定会话的浏览器会话。

        按站点主题路由：不同主题（deepseek/gemini/doubao）使用各自的登录态
        profile 与默认 URL，会话以 (theme, stream_id) 隔离，避免站点串会话。

        Args:
            stream_id: 聊天流 ID。
            theme: 站点主题（deepseek/gemini/doubao）；空用管理器默认主题。

        Returns:
            BrowserSession: 该任务的浏览器会话。

        Raises:
            RuntimeError: Playwright 不可用。
        """
        theme = theme or self._theme
        session_key = self._key(stream_id, theme)
        session = self._sessions.get(session_key)
        if session is not None:
            session.touch()
            return session

        # 同 key 的创建互斥：并发首次调用只启动一个浏览器，避免争用同一 profile
        lock = self._create_locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._create_locks[session_key] = lock
        async with lock:
            # 锁内二次检查（等待锁期间可能有其他协程已创建）
            session = self._sessions.get(session_key)
            if session is not None:
                session.touch()
                return session
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover - 依赖缺失时
                raise RuntimeError("Playwright 未安装，插件启动时会自动安装") from exc

            profile_dir = self._profile_root / theme
            profile_dir.mkdir(parents=True, exist_ok=True)
            url = self._site_url(theme)
            p = await async_playwright().start()
            # 指定站点可强制有头（forced_headful_themes 非空时）；默认全部无头
            headful = theme in self._forced_headful_themes
            headless_now = False if headful else self._headless
            launch_kwargs: dict[str, Any] = {
                "user_data_dir": str(profile_dir),
                "headless": headless_now,
                "viewport": self.viewport,
                "device_scale_factor": self._device_scale_factor,
                "permissions": ["clipboard-read", "clipboard-write"],
            }
            # 自动化相关参数与登录脚本同源，避免两处不一致触发站点风控
            launch_kwargs.update(launch_flags())
            # 优先使用配置的浏览器路径；未配置时自动探测正式版 Chrome
            # （Playwright 自带 Chromium 的自动化指纹会被 Google 风控判定并登出）
            browser_path = resolve_browser_path(self._browser_path)
            if headless_now and browser_path:
                # 无头启动前替换掉 UA 中的无头标识，避免站点风控识别（详见函数文档）
                real_ua = await _resolve_real_user_agent(browser_path)
                if real_ua:
                    launch_kwargs["user_agent"] = real_ua
            if browser_path:
                launch_kwargs["executable_path"] = browser_path
            if self._proxy_url:
                launch_kwargs["proxy"] = {"server": self._proxy_url}
            try:
                context = await p.chromium.launch_persistent_context(**launch_kwargs)
            except Exception:
                # 启动失败时释放 playwright 实例，避免资源泄漏后重试
                try:
                    await p.stop()
                except Exception:  # noqa: BLE001 - 关闭失败不掩盖原异常
                    pass
                raise
            page = context.pages[0] if context.pages else await context.new_page()
            if browser_path:
                await page.add_init_script(STEALTH_INIT_SCRIPT)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)

            session = BrowserSession(stream_id=stream_id, context=context, playwright=p, page=page)
            self._sessions[session_key] = session
            self._ensure_cleanup_task()
            logger.info(f"浏览器会话已创建（theme={theme}, stream={stream_id}）")
            return session

    def touch(self, stream_id: str, theme: str = "") -> None:
        """刷新会话活动时间。

        Args:
            stream_id: 聊天流 ID。
            theme: 站点主题；空用管理器默认主题。
        """
        session = self._sessions.get(self._key(stream_id, theme))
        if session is not None:
            session.touch()

    def set_active_conversation(
        self, stream_id: str, conversation_id: str, title: str = "", theme: str = ""
    ) -> None:
        """记录指定会话当前活跃会话的 ID 与标题。

        Args:
            stream_id: 聊天流 ID。
            conversation_id: 会话稳定 ID（URL 中的 UUID）。
            title: 会话标题（仅展示用）。
            theme: 站点主题；空用管理器默认主题。
        """
        session = self._sessions.get(self._key(stream_id, theme))
        if session is not None:
            session.set_active_conversation(conversation_id, title)

    async def close(self, stream_id: str, theme: str = "") -> None:
        """关闭指定会话。

        Args:
            stream_id: 聊天流 ID。
            theme: 站点主题；空用管理器默认主题。
        """
        await self._close_by_key(self._key(stream_id, theme))

    async def _close_by_key(self, key: str) -> None:
        """按存储键关闭会话（关闭异常静默忽略）。

        Args:
            key: 会话存储键（theme:stream_id）。
        """
        session = self._sessions.pop(key, None)
        if session is None:
            return
        try:
            await session.context.close()
        except Exception:  # noqa: BLE001 - 关闭异常忽略
            pass
        try:
            if session.playwright is not None:
                await session.playwright.stop()
        except Exception:  # noqa: BLE001 - 停止异常忽略
            pass
        logger.info(f"浏览器会话已关闭（{key}）")

    async def close_all(self) -> None:
        """关闭所有会话（插件卸载时调用）。"""
        for key in list(self._sessions):
            await self._close_by_key(key)
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None

    def _ensure_cleanup_task(self) -> None:
        """确保后台空闲清理任务已启动（经 task_manager 管理）。"""
        if self._cleanup_task is None or self._cleanup_task.done():
            info = get_task_manager().create_task(
                self._cleanup_loop(),
                name="ai_ui_snapshot_cleanup",
                daemon=True,
            )
            self._cleanup_task = info.task

    async def _cleanup_loop(self) -> None:
        """后台轮询，关闭空闲超时会话（活跃操作中的会话跳过）。"""
        try:
            while True:
                await asyncio.sleep(10)
                now = time.time()
                stale = [
                    key
                    for key, s in self._sessions.items()
                    if s.busy <= 0 and now - s.last_active > self._idle_timeout_s
                ]
                for key in stale:
                    logger.info(f"会话空闲超时自动关闭（{key}）")
                    await self._close_by_key(key)
        except asyncio.CancelledError:
            return


# 模块级共享管理器（进程内单例，所有工具/命令共用）
_manager: BrowserSessionManager | None = None


def get_manager() -> BrowserSessionManager:
    """获取共享的 BrowserSessionManager 单例。

    默认参数（deepseek 站点、登录态目录、无头）由插件配置在首次使用前
    通过 :func:`init_manager` 覆盖；未初始化时使用默认值。

    Returns:
        BrowserSessionManager: 共享管理器实例。
    """
    global _manager
    if _manager is None:
        _manager = BrowserSessionManager(profile_root="data/ai_ui_snapshot_profile")
    return _manager


def init_manager(
    *,
    profile_root: str,
    idle_timeout_s: int = 600,
    headless: bool = True,
    forced_headful_themes: tuple[str, ...] = (),
    browser_path: str = "",
    proxy_url: str = "",
    page_theme: str = "auto",
    viewport_width: int = 1440,
    viewport_height: int = 900,
    device_scale_factor: int = 2,
    max_screenshot_height: int = 8000,
    decoration_enabled: bool = True,
    decoration_theme: str = "auto",
    decoration_avatar_url: str = "",
) -> BrowserSessionManager:
    """初始化共享管理器（插件加载时调用，覆盖默认参数）。

    Args:
        profile_root: 登录态根目录。
        idle_timeout_s: 空闲自动关闭秒数。
        headless: 是否无头。
        forced_headful_themes: 强制使用有头模式的站点主题列表（默认空，
            全部站点跟随 headless 无头；仅在需要个别站点有头时指定）。
        browser_path: Chromium 可执行路径（可空）。
        viewport_width: 视口宽度。
        viewport_height: 视口高度。
        device_scale_factor: 高清渲染倍率。
        max_screenshot_height: 长截图最大高度（像素）。
        decoration_enabled: 是否在截图时叠加浏览器外壳装饰。
        decoration_theme: 浏览器外壳配色（auto/light/dark）。
        decoration_avatar_url: 自定义 Google 账号头像 URL。

    Returns:
        BrowserSessionManager: 初始化后的共享管理器。
    """
    global _manager
    _manager = BrowserSessionManager(
        profile_root=profile_root,
        idle_timeout_s=idle_timeout_s,
        headless=headless,
        forced_headful_themes=forced_headful_themes,
        browser_path=browser_path,
        proxy_url=proxy_url,
        page_theme=page_theme,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        device_scale_factor=device_scale_factor,
        max_screenshot_height=max_screenshot_height,
        decoration_enabled=decoration_enabled,
        decoration_theme=decoration_theme,
        decoration_avatar_url=decoration_avatar_url,
    )
    return _manager


async def close_all_sessions() -> None:
    """关闭共享管理器所有会话（插件卸载时调用）。"""
    global _manager
    if _manager is not None:
        await _manager.close_all()
        _manager = None
