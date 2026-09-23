"""DeepSeek 专属浏览器动作。

组合通用页面操作（:class:`PageActions`）与 DeepSeek 站点语义（开关/历史
会话/长截图/分享链接），供上层业务（snapshot_service / tools）使用。站点
专属常量与脚本集中在 :mod:`constants`，选择器变动仅需在该处同步。

DeepSeek 网页已下线对话模式选择，本类不再提供模式读写动作。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger

from ..base.page_actions import PageActions
from ..base.utils import normalize_theme, resolve_auto_theme
from .constants import (
    ACTIVE_CONVERSATION_ID_SCRIPT,
    ACTIVE_CONVERSATION_TITLE_SCRIPT,
    CONVERSATION_SELECTOR,
    CONVERSATION_TEXT_SCRIPT,
    EXPAND_SCRIPT,
    FINGERPRINT_SCRIPT,
    GENERATING_SCRIPT,
    GET_THEME_SCRIPT,
    HISTORY_LIST_SCRIPT,
    HISTORY_OPEN_SCRIPT,
    HISTORY_SCROLL_SCRIPT,
    POLL_INTERVAL_S,
    RESTORE_SCRIPT,
    SEARCH_TOGGLE_NAME,
    SET_THEME_SCRIPT,
    SHARE_BUTTON_SCRIPT,
    SHARE_LINK_SCRIPT,
    SHARE_MODAL_CLOSE_SCRIPT,
    SIDEBAR_SCRIPT,
    THINK_SCRIPT,
    THINK_TOGGLE_NAME,
    TOGGLE_SELECTOR,
    VISIBILITY_SCRIPT,
    normalize_sidebar,
    normalize_think,
)

logger = get_logger("ai_ui_snapshot.browser_actions")


class BrowserActions(PageActions):
    """DeepSeek 专属浏览器动作（组合通用页面操作）。

    在 :class:`PageActions` 通用能力之上，封装 DeepSeek 站点语义动作：开关
    读写、历史会话进入、长截图（分片）与分享链接。站点常量见
    :mod:`constants`。
    """

    conversation_selector: str = CONVERSATION_SELECTOR
    visibility_script: str = VISIBILITY_SCRIPT
    generating_script: str = GENERATING_SCRIPT
    poll_interval_s: float = POLL_INTERVAL_S
    conversation_text_script: str = CONVERSATION_TEXT_SCRIPT
    active_title_script: str = ACTIVE_CONVERSATION_TITLE_SCRIPT
    active_id_script: str = ACTIVE_CONVERSATION_ID_SCRIPT
    fingerprint_script: str = FINGERPRINT_SCRIPT
    expand_script: str = EXPAND_SCRIPT
    restore_script: str = RESTORE_SCRIPT
    expand_arg: Any = CONVERSATION_SELECTOR
    expand_wait_ms: int = 150

    def __init__(
        self,
        page: Any,
        *,
        max_screenshot_height: int = 8000,
        touch_cb: Any | None = None,
        decoration_enabled: bool = True,
        decoration_theme: str = "auto",
        decoration_avatar_url: str = "",
    ) -> None:
        """初始化。

        Args:
            page: Playwright 页面对象。
            max_screenshot_height: 长截图单张最大高度（像素），超出分片截取。
            touch_cb: 可选保活回调（刷新会话活动时间），长等待中调用。
            decoration_enabled: 截图时是否在顶部叠加浏览器外壳装饰。
            decoration_theme: 外壳配色（auto/light/dark）。
            decoration_avatar_url: 自定义 Google 账号头像 URL。
        """
        super().__init__(
            page,
            touch_cb=touch_cb,
            max_screenshot_height=max_screenshot_height,
            decoration_enabled=decoration_enabled,
            decoration_theme=normalize_theme(decoration_theme),
            decoration_avatar_url=(decoration_avatar_url or "").strip(),
        )

    async def set_theme(self, theme: str | None = None) -> str:
        """设置 DeepSeek 页面主题（写 localStorage 主题偏好）。

        DeepSeek 主题由 localStorage ``chat_themePreference`` 控制，改后需
        reload 使 React 重新读取生效。auto 按本地时间自动切换白天/夜间。

        Args:
            theme: 目标主题（auto/light/dark）；None 用构造器配置。

        Returns:
            str: 实际应用的主题（light/dark/system）。
        """
        target = normalize_theme(theme) if theme is not None else self._decoration_theme
        resolved = resolve_auto_theme() if target == "auto" else target
        try:
            await self._page.evaluate(SET_THEME_SCRIPT, resolved)
            await self._page.reload(wait_until="domcontentloaded")
            await self._page.wait_for_timeout(4000)
        except Exception:  # noqa: BLE001 - 页面未就绪
            pass
        return resolved

    async def get_theme(self) -> str:
        """读取当前 DeepSeek 主题偏好（system/light/dark）。

        Returns:
            str: system/light/dark。
        """
        try:
            return str(await self._page.evaluate(GET_THEME_SCRIPT) or "system")
        except Exception:  # noqa: BLE001 - 页面未就绪
            return "system"

    async def set_toggle(self, name: str, enable: bool | None) -> tuple[bool, str]:
        """设置深度思考/智能搜索开关状态。

        开关选中态由 aria-pressed 布尔属性表达，仅当需要变更时才点击。

        Args:
            name: 开关名（深度思考 / 智能搜索）。
            enable: True 开启 / False 关闭 / None 不修改（仅返回当前状态）。

        Returns:
            tuple[bool, str]: (是否成功, 状态说明)。
        """
        page = self._page
        try:
            # 开关可能在页面导航后延迟渲染，先轮询等待出现
            toggle = None
            for _ in range(10):
                candidate = page.locator(f"{TOGGLE_SELECTOR}:has-text('{name}')").first
                if await candidate.count() > 0:
                    toggle = candidate
                    break
                await page.wait_for_timeout(400)
            if toggle is None:
                return False, f"未找到开关「{name}」"
            is_selected = str(await toggle.get_attribute("aria-pressed") or "") == "true"
            if enable is None:
                return True, f"{name}: {'开启' if is_selected else '关闭'}"
            if is_selected == enable:
                return True, f"{name}: 已是{'开启' if enable else '关闭'}状态"
            await toggle.click(timeout=5000)
            await page.wait_for_timeout(600)
            # 校验
            changed = str(await toggle.get_attribute("aria-pressed") or "") == "true"
            if changed == enable:
                return True, f"{name}: 已{'开启' if enable else '关闭'}"
            return False, f"{name}: 设置失败"
        except Exception as exc:  # noqa: BLE001 - 设置失败
            return False, f"设置开关「{name}」失败: {exc}"

    async def get_toggles(self) -> dict[str, bool]:
        """读取当前开关状态（深度思考/智能搜索是否开启）。

        Returns:
            dict[str, bool]: {深度思考: bool, 智能搜索: bool}；不存在的开关返回 False。
        """
        try:
            items = await self._page.evaluate(
                """() => {
                    const out = {};
                    const els = document.querySelectorAll('div.ds-toggle-button[aria-pressed]');
                    for (const el of els) {
                        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                        if (!t) continue;
                        out[t] = el.getAttribute('aria-pressed') === 'true';
                    }
                    return out;
                }"""
            )
            return {
                THINK_TOGGLE_NAME: bool(items.get(THINK_TOGGLE_NAME)),
                SEARCH_TOGGLE_NAME: bool(items.get(SEARCH_TOGGLE_NAME)),
            }
        except Exception:  # noqa: BLE001 - 页面未就绪
            return {THINK_TOGGLE_NAME: False, SEARCH_TOGGLE_NAME: False}

    async def new_chat(self) -> bool:
        """开启一个新对话（点击侧栏"开启新对话"入口）。

        Returns:
            bool: 是否成功点击。
        """
        return await self.click("开启新对话")

    async def list_conversations(self) -> list[str]:
        """列出侧边栏历史会话标题（去重）。

        Returns:
            list[str]: 历史会话标题列表。
        """
        try:
            items = await self._page.evaluate(HISTORY_LIST_SCRIPT)
            # 脚本返回 [{title, clickable}, ...]，仅取 title
            if items and isinstance(items, list) and isinstance(items[0], dict):
                return [str(t.get("title", "")) for t in items if t.get("title")]
            return [str(t) for t in (items or [])]
        except Exception:  # noqa: BLE001 - 页面未就绪
            return []

    async def open_conversation(self, title: str) -> bool:
        """进入指定标题的历史会话。

        成功判据只有一条：侧边栏里能按标题找到并点开该项（列表为虚拟滚动，
        未命中时先滚动加载更多）。点击后轮询确认切换是否生效，未确认只记
        日志、不影响返回——页面未及时更新 URL/标题不代表没进去，据此报错
        会让上层放弃截图/提问，是更糟的结果。

        Args:
            title: 历史会话标题（取自 list_conversations）。

        Returns:
            bool: 是否找到并打开了目标会话；标题不在侧边栏时为 False。
        """
        page = self._page
        want = (title or "").strip()
        if not want:
            return False
        try:
            # 1. 已在目标会话：直接成功（重复点击同一项没有意义）
            if (await self.get_active_conversation_title()) == want:
                return True

            # 2. 确保侧边栏可见（若上次截图用 sidebar=hide 隐藏）
            sidebar_mode = normalize_sidebar("show")
            if sidebar_mode:
                await page.evaluate(SIDEBAR_SCRIPT, sidebar_mode)
                await page.wait_for_timeout(400)
            before_fp = str(await page.evaluate(FINGERPRINT_SCRIPT) or "")

            # 3. 侧边栏定位并点击（列表未加载完时先滚动加载更多）
            hit: dict[str, Any] = {}
            for _ in range(10):
                result = await page.evaluate(HISTORY_OPEN_SCRIPT, want)
                if isinstance(result, dict) and result.get("ok"):
                    hit = result
                    break
                if not await page.evaluate(HISTORY_SCROLL_SCRIPT):
                    break
                await page.wait_for_timeout(400)
            if not hit:
                return False

            # 4. 等待切换生效（仅日志，不阻断）
            target_id = str(hit.get("id") or "").strip().lower()
            if not await self._wait_session_switch(
                target_id=target_id, target_title=want, before_fingerprint=before_fp
            ):
                logger.info(f"进入历史会话 [{want}] 后未观测到切换，按当前页面继续")
            return True
        except Exception:  # noqa: BLE001 - 页面未就绪
            return False

    async def _click_button_exact(self, text: str) -> bool:
        """按按钮文本精确匹配并真实点击。

        用 ``getBoundingClientRect`` 取按钮中心坐标后走鼠标事件，避免 JS
        ``element.click()`` 只派发 click 事件、React 状态不响应的问题；同时
        避免坐标硬编码，视口尺寸变化后仍然可用。

        Args:
            text: 按钮文本（须与按钮内容完全一致，忽略空白差异）。

        Returns:
            bool: 是否找到并点击了按钮。
        """
        center = await self._page.evaluate(
            """(want) => {
                const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                for (const b of document.querySelectorAll('div[role="button"], button')) {
                    if (norm(b.innerText) !== want) continue;
                    const r = b.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
                }
                return null;
            }""",
            text,
        )
        if not center:
            return False
        await self._page.mouse.click(center["x"], center["y"])
        return True

    async def _close_share_modal(self) -> None:
        """关闭分享弹窗（失败不阻塞，避免遮挡后续截图）。"""
        page = self._page
        try:
            center = await page.evaluate(SHARE_MODAL_CLOSE_SCRIPT)
            if center:
                await page.mouse.click(center["x"], center["y"])
                await page.wait_for_timeout(600)
            if await page.evaluate("() => document.querySelectorAll('.ds-modal').length") > 0:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(600)
        except Exception:  # noqa: BLE001 - 关闭失败不阻塞
            pass

    async def create_share_link(self) -> str | None:
        """创建并获取当前对话的 DeepSeek 官方分享链接。

        流程：顶栏分享按钮 → 底部确认条「创建分享链接」→ 弹窗「创建并复制」
        → 从弹窗读出链接 → 关闭弹窗。链接从弹窗文本读取，不依赖剪贴板
        （剪贴板读取需页面焦点与授权，后台/无头环境下会阻塞）。

        Returns:
            str | None: 分享链接 URL（如 https://chat.deepseek.com/share/xxx）；失败返回 None。
        """
        page = self._page
        try:
            # 1. 点击顶栏右上角分享按钮（坐标由页面实时计算，非硬编码）
            center = await page.evaluate(SHARE_BUTTON_SCRIPT)
            if not center:
                logger.warning("未找到 DeepSeek 顶栏分享按钮（当前页面可能不是会话页）")
                return None
            await page.mouse.click(center["x"], center["y"])
            await page.wait_for_timeout(1000)

            # 2. 点击底部确认条的 "创建分享链接"
            if not await self._click_button_exact("创建分享链接"):
                logger.warning("未找到「创建分享链接」按钮")
                return None
            await page.wait_for_timeout(1200)

            # 3. 点击分享弹窗的 "创建并复制"
            if not await self._click_button_exact("创建并复制"):
                logger.warning("未找到「创建并复制」按钮")
                return None
            await page.wait_for_timeout(1500)

            # 4. 从分享弹窗读取生成的链接
            link = str(await page.evaluate(SHARE_LINK_SCRIPT) or "").strip()
            if not link.startswith("http"):
                return None
            return link
        except Exception as exc:  # noqa: BLE001 - 分享失败
            logger.warning(f"生成分享链接失败: {exc}")
            return None
        finally:
            # 5. 关闭分享弹窗，避免遮挡后续截图
            await self._close_share_modal()

    async def screenshot(
        self,
        region: str = "conversation",
        think: str = "collapse",
        sidebar: str = "auto",
        scope: str = "viewport",
        rounds: int = 1,
    ) -> list[str]:
        """截取页面区域为 data URI 列表（超长时分片）。

        - scope="viewport"（默认）：截取当前人类可读的正常桌面视窗（16:9/16:10，聚焦最新回复）；
        - scope="rounds"：从后往前倒序完整截取最近 rounds 个回复及对应提问；
        - scope="full"：撑开整页长截图。

        Args:
            region: conversation / full。
            think: 深度思考展开方式（collapse/auto/expand/reveal）。
            sidebar: 左侧边栏显示方式（auto/show/hide）。
            scope: 截图范围（viewport / rounds / full）。
            rounds: 当 scope="rounds" 时截取的回复轮数。

        Returns:
            list[str]: PNG data URI 列表。
        """
        page = self._page
        think_mode = normalize_think(think)
        think_saved: list[Any] = []
        if think_mode:
            think_saved = list(await page.evaluate(THINK_SCRIPT, think_mode) or [])
        sidebar_mode = normalize_sidebar(sidebar)
        sidebar_saved: list[Any] = []
        if sidebar_mode:
            sidebar_saved = list(await page.evaluate(SIDEBAR_SCRIPT, sidebar_mode) or [])

        try:
            if scope == "rounds" or rounds > 1:
                return await self._rounds_shot(rounds=rounds)
            if scope == "full":
                if not await self._conversation_visible():
                    return await self._fullpage_shots()
                return await self._expanded_fullpage_shots(
                    EXPAND_SCRIPT,
                    RESTORE_SCRIPT,
                    expand_arg=CONVERSATION_SELECTOR,
                    wait_ms=150,
                )
            # 默认正常视窗截图（滚动到最新消息位置）
            try:
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(200)
            except Exception:  # noqa: BLE001
                pass
            return await self._viewport_shot()
        finally:
            try:
                await page.evaluate(RESTORE_SCRIPT, {"saved": think_saved + sidebar_saved})
            except Exception:  # noqa: BLE001
                pass
