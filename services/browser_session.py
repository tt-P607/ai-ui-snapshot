"""任务级临时浏览器会话管理器。

LLM 处理一个任务时临时打开一个 Playwright 浏览器（复用 bot 账号登录态），
任务过程内按会话（stream_id）共享同一个页面，支持跨多次工具调用保持状态；
任务结束（空闲超时无活动）自动关闭，插件卸载时全部关闭，不常驻占用资源。
"""

from __future__ import annotations

import asyncio
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.kernel.concurrency import get_task_manager

logger = get_logger("ai_ui_snapshot.browser_session")


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
        conversation_mode: 对话模式锁（会话稳定 ID → 模式），进入/新建对话时锁定，
            切换对话互不覆盖。ID 取 URL 中的会话 UUID，标题会变而 ID 稳定。
        active_conversation: 当前活跃对话的稳定 ID（用于模式锁查询）。
        active_conversation_title: 当前活跃对话标题（仅作展示/返回，不作为锁 key）。
    """

    stream_id: str
    context: Any
    playwright: Any = None
    page: Any = None
    last_active: float = field(default_factory=time.time)
    busy: int = 0
    conversation_mode: dict[str, str] = field(default_factory=dict)
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
        """记录当前活跃对话的稳定 ID 与标题，并迁移未命名对话的模式锁。

        新建对话提问后 URL 的会话 UUID 生成/变化，原锁定在空键（或旧 ID）
        上的模式锁随之迁移到新 ID，避免模式锁因 key 变化而丢失。

        Args:
            conversation_id: 会话稳定 ID（URL 中的 UUID）。
            title: 会话标题（仅展示用，不作为锁 key）。
        """
        cid = conversation_id or ""
        if cid and cid != self.active_conversation:
            if "" in self.conversation_mode:
                self.conversation_mode[cid] = self.conversation_mode.pop("")
        self.active_conversation = cid
        if title:
            self.active_conversation_title = title

    def lock_mode(self, mode: str) -> None:
        """锁定当前活跃对话的对话模式（选定后不可再切换）。"""
        self.conversation_mode[self.active_conversation] = mode

    def lock_conversation_mode(self, conversation_id: str, mode: str, title: str = "") -> None:
        """锁定指定会话 ID 的模式，并切换当前活跃会话。

        Args:
            conversation_id: 会话稳定 ID（URL 中的 UUID）。
            mode: 要锁定的模式名。
            title: 会话标题（仅展示用，不作为锁 key）。
        """
        if conversation_id:
            self.conversation_mode[conversation_id] = mode
            self.active_conversation = conversation_id
            if title:
                self.active_conversation_title = title

    def get_locked_mode(self, conversation_id: str = "") -> str | None:
        """读取当前（或指定）会话的模式锁。

        Args:
            conversation_id: 会话稳定 ID；空表示当前活跃会话。

        Returns:
            str | None: 锁定模式；未锁定返回 None。
        """
        return self.conversation_mode.get(conversation_id or self.active_conversation)

    def clear_mode(self, conversation_id: str = "") -> None:
        """清除当前（或全部）会话的模式锁。

        Args:
            conversation_id: 会话稳定 ID；空表示清除全部。
        """
        if conversation_id:
            self.conversation_mode.pop(conversation_id, None)
        else:
            self.conversation_mode.clear()


class BrowserSessionManager:
    """按 stream_id 管理任务级临时浏览器会话。

    用法：进程内单例。``get(stream_id)`` 取或建会话；``touch(stream_id)``
    刷新活动时间；后台任务定期关闭空闲会话；``close_all`` 关闭全部。
    """

    def __init__(
        self,
        *,
        profile_root: str,
        theme: str = "deepseek",
        url: str = "https://chat.deepseek.com/",
        idle_timeout_s: int = 600,
        headless: bool = True,
        browser_path: str = "",
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
            theme: 站点主题（当前仅 deepseek），用于定位登录态目录。
            url: 打开的目标网址。
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
        self._url = url
        self._idle_timeout_s = idle_timeout_s
        self._headless = headless
        self._browser_path = browser_path
        self._viewport_width = viewport_width
        self._viewport_height = viewport_height
        self._device_scale_factor = device_scale_factor
        self._max_screenshot_height = max_screenshot_height
        self._decoration_enabled = decoration_enabled
        self._decoration_theme = decoration_theme
        self._decoration_avatar_url = decoration_avatar_url
        self._sessions: dict[str, BrowserSession] = {}
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
    def decoration_theme(self) -> str:
        """浏览器外壳配色（auto/light/dark）。"""
        return self._decoration_theme

    @property
    def decoration_avatar_url(self) -> str:
        """自定义 Google 账号头像 URL。"""
        return self._decoration_avatar_url

    async def get(self, stream_id: str) -> BrowserSession:
        """获取（或创建）指定会话的浏览器会话。

        Args:
            stream_id: 聊天流 ID。

        Returns:
            BrowserSession: 该任务的浏览器会话。

        Raises:
            RuntimeError: Playwright 不可用。
        """
        session = self._sessions.get(stream_id)
        if session is not None:
            session.touch()
            return session

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - 依赖缺失时
            raise RuntimeError("Playwright 未安装，插件启动时会自动安装") from exc

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        p = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(self.profile_dir),
            "headless": self._headless,
            "viewport": self.viewport,
            "device_scale_factor": self._device_scale_factor,
            "permissions": ["clipboard-read", "clipboard-write"],
        }
        if self._browser_path:
            launch_kwargs["executable_path"] = self._browser_path
        context = await p.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(self._url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        session = BrowserSession(stream_id=stream_id, context=context, playwright=p, page=page)
        self._sessions[stream_id] = session
        self._ensure_cleanup_task()
        logger.info(f"浏览器会话已创建（stream={stream_id}）")
        return session

    def touch(self, stream_id: str) -> None:
        """刷新会话活动时间。

        Args:
            stream_id: 聊天流 ID。
        """
        session = self._sessions.get(stream_id)
        if session is not None:
            session.touch()

    def lock_mode(self, stream_id: str, mode: str) -> None:
        """锁定指定会话当前活跃对话的模式（历史会话进入等场景）。

        Args:
            stream_id: 聊天流 ID。
            mode: 要锁定的模式名。
        """
        session = self._sessions.get(stream_id)
        if session is not None:
            session.lock_mode(mode)

    def lock_conversation_mode(self, stream_id: str, conversation_id: str, mode: str, title: str = "") -> None:
        """锁定指定会话 ID 的模式，并切换当前活跃会话。

        Args:
            stream_id: 聊天流 ID。
            conversation_id: 会话稳定 ID（URL 中的 UUID）。
            mode: 要锁定的模式名。
            title: 会话标题（仅展示用，不作为锁 key）。
        """
        session = self._sessions.get(stream_id)
        if session is not None:
            session.lock_conversation_mode(conversation_id, mode, title)

    def set_active_conversation(self, stream_id: str, conversation_id: str, title: str = "") -> None:
        """记录指定会话当前活跃会话的 ID 与标题（ID 生成后迁移模式锁）。

        Args:
            stream_id: 聊天流 ID。
            conversation_id: 会话稳定 ID（URL 中的 UUID）。
            title: 会话标题（仅展示用，不作为锁 key）。
        """
        session = self._sessions.get(stream_id)
        if session is not None:
            session.set_active_conversation(conversation_id, title)

    def clear_mode(self, stream_id: str) -> None:
        """清除指定会话的全部对话模式锁。

        Args:
            stream_id: 聊天流 ID。
        """
        session = self._sessions.get(stream_id)
        if session is not None:
            session.clear_mode()

    def get_locked_mode(self, stream_id: str) -> str | None:
        """读取指定会话当前活跃对话已锁定的模式。

        Args:
            stream_id: 聊天流 ID。

        Returns:
            str | None: 已锁定模式；未锁定或会话不存在时返回 None。
        """
        session = self._sessions.get(stream_id)
        return session.get_locked_mode() if session is not None else None

    async def close(self, stream_id: str) -> None:
        """关闭指定会话。

        Args:
            stream_id: 聊天流 ID。
        """
        session = self._sessions.pop(stream_id, None)
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
        logger.info(f"浏览器会话已关闭（stream={stream_id}）")

    async def close_all(self) -> None:
        """关闭所有会话（插件卸载时调用）。"""
        for stream_id in list(self._sessions):
            await self.close(stream_id)
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
                    sid
                    for sid, s in self._sessions.items()
                    if s.busy <= 0 and now - s.last_active > self._idle_timeout_s
                ]
                for sid in stale:
                    logger.info(f"会话空闲超时自动关闭（stream={sid}）")
                    await self.close(sid)
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
    theme: str = "deepseek",
    url: str = "https://chat.deepseek.com/",
    idle_timeout_s: int = 600,
    headless: bool = True,
    browser_path: str = "",
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
        theme: 站点主题。
        url: 目标网址。
        idle_timeout_s: 空闲自动关闭秒数。
        headless: 是否无头。
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
        theme=theme,
        url=url,
        idle_timeout_s=idle_timeout_s,
        headless=headless,
        browser_path=browser_path,
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
