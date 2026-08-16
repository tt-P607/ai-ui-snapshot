"""DeepSeek 专属浏览器动作。

组合通用页面操作（:class:`PageActions`）与 DeepSeek 站点语义（模式/开关/历史
会话/长截图/分享链接），供上层业务（snapshot_service / tools）使用。站点专属
常量与脚本集中在 :mod:`constants`，选择器变动仅需在该处同步。
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
    MODE_OPTION_SELECTOR,
    MODE_TRIGGER_SELECTOR,
    POLL_INTERVAL_S,
    RESTORE_SCRIPT,
    SEARCH_TOGGLE_NAME,
    SET_THEME_SCRIPT,
    SIDEBAR_SCRIPT,
    SUPPORTED_MODES,
    THINK_SCRIPT,
    THINK_TOGGLE_NAME,
    TOGGLE_SELECTOR,
    VISIBILITY_SCRIPT,
    normalize_mode,
    normalize_sidebar,
    normalize_think,
)

logger = get_logger("ai_ui_snapshot.browser_actions")


class BrowserActions(PageActions):
    """DeepSeek 专属浏览器动作（组合通用页面操作）。

    在 :class:`PageActions` 通用能力之上，封装 DeepSeek 站点语义动作：模式与
    开关读写、历史会话进入、长截图（分片）与分享链接。站点常量见
    :mod:`constants`。
    """

    conversation_selector: str = CONVERSATION_SELECTOR
    visibility_script: str = VISIBILITY_SCRIPT
    generating_script: str = GENERATING_SCRIPT
    poll_interval_s: float = POLL_INTERVAL_S
    conversation_text_script: str = CONVERSATION_TEXT_SCRIPT
    active_title_script: str = ACTIVE_CONVERSATION_TITLE_SCRIPT
    active_id_script: str = ACTIVE_CONVERSATION_ID_SCRIPT

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
            decoration_avatar_url=self._resolve_avatar_url((decoration_avatar_url or "").strip()),
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

    async def set_mode(self, mode: str) -> tuple[bool, str]:
        """切换 DeepSeek 对话模式（快速/专家/识图）。

        先点击模式选择器触发按钮展开面板，再点击对应可点选项容器
        （``div[class*='_9f2341b']``，带 aria-checked）。

        Args:
            mode: 目标模式名（支持 快速模式/专家模式/识图模式，或 快速/专家/识图）。

        Returns:
            tuple[bool, str]: (是否切换成功, 当前选中模式或错误信息)。
        """
        normalized = normalize_mode(mode)
        if normalized is None:
            return False, f"不支持的对话模式: {mode}（可选: {', '.join(SUPPORTED_MODES)}）"
        page = self._page
        try:
            # 1. 展开模式选择器
            trigger = page.locator(MODE_TRIGGER_SELECTOR).first
            if await trigger.count() > 0 and await trigger.is_visible():
                await trigger.click(timeout=5000)
            await page.wait_for_timeout(600)
            # 2. 用 Playwright 真实点击选项容器（真实鼠标事件序列才能触发
            #    React 状态切换；JS 原生 element.click() 只派发 click 事件，
            #    DeepSeek 的 radio 组件不响应，实测无法切换）
            option = page.locator(MODE_OPTION_SELECTOR).filter(has_text=normalized).first
            if await option.count() > 0:
                await option.click(timeout=5000)
            else:
                # 面板可能未展开，尝试文本点击
                await self.click(normalized)
            await page.wait_for_timeout(1000)
            # 3. 校验结果
            current = await self.get_mode()
            if current == normalized:
                return True, current or normalized
            return False, f"切换模式失败，当前仍为: {current or '未知'}"
        except Exception as exc:  # noqa: BLE001 - 切换失败
            return False, f"切换模式失败: {exc}"

    async def set_toggle(self, name: str, enable: bool | None) -> tuple[bool, str]:
        """设置深度思考/智能搜索开关状态。

        开关无 aria-checked，靠 class 是否含 ``ds-toggle-button--selected``
        判断当前状态；仅当需要变更时才点击。

        Args:
            name: 开关名（深度思考 / 智能搜索）。
            enable: True 开启 / False 关闭 / None 不修改（仅返回当前状态）。

        Returns:
            tuple[bool, str]: (是否成功, 状态说明)。
        """
        page = self._page
        try:
            # 开关可能在模式切换后延迟渲染，先轮询等待出现
            toggle = None
            for _ in range(10):
                candidate = page.locator(f"{TOGGLE_SELECTOR}:has-text('{name}')").first
                if await candidate.count() > 0:
                    toggle = candidate
                    break
                await page.wait_for_timeout(400)
            if toggle is None:
                # 该模式不支持此开关（如专家模式无智能搜索）
                return False, f"当前模式不支持开关「{name}」"
            cls = str(await toggle.get_attribute("class") or "")
            is_selected = "ds-toggle-button--selected" in cls
            if enable is None:
                return True, f"{name}: {'开启' if is_selected else '关闭'}"
            if is_selected == enable:
                return True, f"{name}: 已是{'开启' if enable else '关闭'}状态"
            await toggle.click(timeout=5000)
            await page.wait_for_timeout(600)
            # 校验
            cls = str(await toggle.get_attribute("class") or "")
            changed = "ds-toggle-button--selected" in cls
            if changed == enable:
                return True, f"{name}: 已{'开启' if enable else '关闭'}"
            return False, f"{name}: 设置失败"
        except Exception as exc:  # noqa: BLE001 - 设置失败
            return False, f"设置开关「{name}」失败: {exc}"

    async def get_mode(self) -> str | None:
        """读取当前对话的模式。

        依次探测：①面板展开时 aria-checked=true 的选项容器（真实选中态，
        DeepSeek 的触发器 ``span[class*='321831d']`` 始终包含全部模式标题，
        不能作为选中态依据）；②历史会话页顶部 ``the-header`` 中的模式文本
        （历史会话无模式选择器，模式在顶部展示）；③触发器文本（面板收起时）。

        Returns:
            str | None: 模式名（快速模式/专家模式/识图模式）；无法识别时返回 None。
        """
        try:
            return await self._page.evaluate(
                """() => {
                    const modes = ['快速模式', '专家模式', '识图模式'];
                    // 1. 首选 aria-checked=true 的选项容器（真实选中态）
                    const el = document.querySelector("div[class*='_9f2341b'][aria-checked='true']");
                    if (el) {
                        const txt = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                        for (const m of modes) {
                            if (txt === m) return m;
                        }
                        return txt.split('\\n')[0] || null;
                    }
                    // 2. 历史会话页顶部 the-header 中的模式文本（无模式选择器）
                    const headers = document.querySelectorAll('[class*="the-header"], header');
                    for (const h of headers) {
                        const txt = (h.innerText || '').replace(/\\s+/g, ' ').trim();
                        for (const m of modes) {
                            if (txt.includes(m)) return m;
                        }
                    }
                    // 3. 触发器文本（仅面板收起且无选项容器时命中）
                    for (const t of document.querySelectorAll("span[class*='321831d']")) {
                        const txt = (t.innerText || '').replace(/\\s+/g, ' ').trim();
                        for (const m of modes) {
                            if (txt === m) return m;
                        }
                    }
                    return null;
                }"""
            )
        except Exception:  # noqa: BLE001 - 页面未就绪
            return None

    async def get_toggles(self) -> dict[str, bool]:
        """读取当前开关状态（深度思考/智能搜索是否开启）。

        Returns:
            dict[str, bool]: {深度思考: bool, 智能搜索: bool}；不存在的开关返回 False。
        """
        try:
            items = await self._page.evaluate(
                """() => {
                    const out = {};
                    const els = document.querySelectorAll("div[class*='f79352dc']");
                    for (const el of els) {
                        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim().split('\\n')[0];
                        if (!t) continue;
                        out[t] = el.classList.contains('ds-toggle-button--selected');
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

        健壮化流程：确保侧边栏可见（避免上次截图隐藏导致点不到）→ 循环滚动
        历史列表加载更多 → 按多级文本匹配选候选并点击可点容器 → 指纹校验
        （消息数/首条消息变化）确认确实进入。

        Args:
            title: 历史会话标题（取自 list_conversations）。

        Returns:
            bool: 是否成功进入。
        """
        try:
            # 1. 确保侧边栏可见（若上次截图用 sidebar=hide 隐藏）
            sidebar_mode = normalize_sidebar("show")
            if sidebar_mode:
                await self._page.evaluate(SIDEBAR_SCRIPT, sidebar_mode)
                await self._page.wait_for_timeout(400)

            # 2. 记录会话指纹（消息数 + 首条消息摘要）
            before = str(await self._page.evaluate(FINGERPRINT_SCRIPT) or "")

            # 3. 循环滚动历史列表加载更多，直到命中或无法再滚动
            ok = False
            for _ in range(10):
                ok = bool(await self._page.evaluate(HISTORY_OPEN_SCRIPT, title))
                if ok:
                    break
                if not await self._page.evaluate(HISTORY_SCROLL_SCRIPT):
                    break
                await self._page.wait_for_timeout(400)
            if not ok:
                return False

            # 4. 等待进入并做指纹校验
            await self._page.wait_for_timeout(1500)
            after = str(await self._page.evaluate(FINGERPRINT_SCRIPT) or "")
            if before == after:
                # 指纹未变：可能点击未生效或点击后仍是同会话
                logger.warning(f"进入历史会话 [{title}] 后指纹未变化，可能未生效")
                return False
            return True
        except Exception:  # noqa: BLE001 - 页面未就绪
            return False

    async def create_share_link(self) -> str | None:
        """创建并获取当前对话的 DeepSeek 官方分享链接。

        Returns:
            str | None: 分享链接 URL（如 https://chat.deepseek.com/share/xxx）；失败返回 None。
        """
        page = self._page
        try:
            # 1. 尝试点击顶部 Header 右侧分享按钮 (x=1400, y=21 附近)
            click_icon = await page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('div, button')).filter(el => {
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0 && rect.top < 60 && rect.left > window.innerWidth - 200;
                });
                if (els.length > 0) {
                    els[els.length - 1].click();
                    return true;
                }
                return false;
            }""")
            if not click_icon:
                await page.mouse.click(1400, 21)
            await page.wait_for_timeout(1000)

            # 2. 点击 "创建分享链接" 按钮
            await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('*')).filter(el => {
                    return el.children.length === 0 && el.innerText && el.innerText.includes('创建分享链接');
                });
                if (btns.length > 0) btns[0].click();
            }""")
            await page.wait_for_timeout(1000)

            # 3. 点击 "创建并复制" 按钮
            await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('*')).filter(el => {
                    return el.children.length === 0 && el.innerText && el.innerText.includes('创建并复制');
                });
                if (btns.length > 0) btns[0].click();
            }""")
            await page.wait_for_timeout(1000)

            # 4. 从剪贴板或 DOM 读取生成的链接
            link = await page.evaluate("""async () => {
                try {
                    const text = await navigator.clipboard.readText();
                    if (text && text.includes('http')) return text;
                } catch (e) {}
                const a = Array.from(document.querySelectorAll('a')).find(el => el.href && el.href.includes('share'));
                return a ? a.href : null;
            }""")
            if link and isinstance(link, str) and link.startswith("http"):
                return link.strip()
            return None
        except Exception:  # noqa: BLE001 - 分享失败
            return None

    async def screenshot(
        self,
        region: str = "conversation",
        think: str = "collapse",
        sidebar: str = "auto",
    ) -> list[str]:
        """截取页面区域为 data URI 列表（超长时分片）。

        支持长截图：先撑开消息容器到完整内容高度，再整页截图，截完恢复。
        若撑开后的整页高度超过 ``max_screenshot_height``，按上限高度分片
        逐段截取（每片为独立 PNG data URI），保证完整对话不被截断；
        单张不超限时返回仅含一张的列表。

        Args:
            region: conversation（完整对话长图，默认）/ full（整页）。
                当页面为 DeepSeek 时二者等价（都走撑开长截图）。
            think: 深度思考"思考过程"块展开方式：collapse（折叠隐藏，默认）/
                auto（保持现状）/ expand（强制展开）/ reveal（仅当被
                折叠时展开，用于保证截图能包含思考内容）。
            sidebar: 左侧边栏显示方式：auto（保持现状，默认）/ show（展开）/
                hide（收起隐藏）。

        Returns:
            list[str]: PNG data URI 列表（按文档自上而下顺序）；失败返回空列表。
        """
        page = self._page
        # 非 DeepSeek 页面或无消息容器时，直接整页长截图
        if not await self._conversation_visible():
            return await self._fullpage_shots()

        # 顺序关键：先折叠/展开思考块与侧边栏（影响 DOM 内容高度），
        # 再撑开消息容器——height:auto 会按折叠后的实际内容重算高度，
        # 避免截图长度停留在思考块展开时的完整高度（底部留白）。
        think_mode = normalize_think(think)
        think_saved: list[Any] = []
        if think_mode:
            think_saved = list(await page.evaluate(THINK_SCRIPT, think_mode) or [])
        sidebar_mode = normalize_sidebar(sidebar)
        sidebar_saved: list[Any] = []
        if sidebar_mode:
            sidebar_saved = list(await page.evaluate(SIDEBAR_SCRIPT, sidebar_mode) or [])
        saved = await page.evaluate(EXPAND_SCRIPT, CONVERSATION_SELECTOR)
        try:
            await page.wait_for_timeout(150)
            return await self._fullpage_shots()
        finally:
            try:
                await page.evaluate(RESTORE_SCRIPT, {"saved": saved + think_saved + sidebar_saved})
            except Exception:  # noqa: BLE001 - 恢复失败不阻塞
                pass
