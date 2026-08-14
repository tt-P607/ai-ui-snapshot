"""DeepSeek 专属浏览器动作。

组合通用页面操作（:class:`PageActions`）与 DeepSeek 站点语义（模式/开关/历史
会话/长截图/分享链接），供上层业务（snapshot_service / tools）使用。站点专属
常量与脚本集中在 :mod:`deepseek_constants`，选择器变动仅需在该处同步。
"""

from __future__ import annotations

import asyncio
import base64
import io
import pathlib
from typing import Any

from PIL import Image

from src.app.plugin_system.api.log_api import get_logger

from .browser_page import PageActions
from .deepseek_constants import (
    ACTIVE_CONVERSATION_ID_SCRIPT,
    ACTIVE_CONVERSATION_TITLE_SCRIPT,
    BROWSER_CHROME_REMOVE_SCRIPT,
    BROWSER_CHROME_SCRIPT,
    CONVERSATION_SELECTOR,
    CONVERSATION_TEXT_SCRIPT,
    EXPAND_SCRIPT,
    FINGERPRINT_SCRIPT,
    GENERATING_SCRIPT,
    HISTORY_LIST_SCRIPT,
    HISTORY_OPEN_SCRIPT,
    HISTORY_SCROLL_SCRIPT,
    MODE_TRIGGER_SELECTOR,
    POLL_INTERVAL_S,
    RESTORE_SCRIPT,
    SEARCH_TOGGLE_NAME,
    SIDEBAR_SCRIPT,
    SUPPORTED_MODES,
    THINK_SCRIPT,
    THINK_TOGGLE_NAME,
    TOGGLE_SELECTOR,
    VISIBILITY_SCRIPT,
    normalize_decoration_theme,
    normalize_mode,
    normalize_sidebar,
    normalize_think,
)

logger = get_logger("ai_ui_snapshot.browser_actions")


def data_uri(data: bytes) -> str:
    """将 PNG 字节流转为 data URI。

    Args:
        data: PNG 字节流。

    Returns:
        str: data URI。
    """
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


class BrowserActions(PageActions):
    """DeepSeek 专属浏览器动作（组合通用页面操作）。

    在 :class:`PageActions` 通用能力之上，封装 DeepSeek 站点语义动作：模式与
    开关读写、历史会话进入、长截图（分片）与分享链接。站点常量见
    :mod:`deepseek_constants`。

    Attributes:
        page: 当前页面对象。
        max_screenshot_height: 长截图单张最大高度（像素），超出分片。
    """

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
        super().__init__(page)
        self._max_screenshot_height = max_screenshot_height
        self._touch_cb = touch_cb
        self._decoration_enabled = decoration_enabled
        self._decoration_theme = normalize_decoration_theme(decoration_theme)
        self._decoration_avatar_url = self._resolve_avatar_url((decoration_avatar_url or "").strip())

    @staticmethod
    def _resolve_avatar_url(avatar_url: str) -> str:
        """解析头像 URL，若为空则尝试自动加载已持久化的真实 Google 头像。

        Args:
            avatar_url: 配置或传入的头像地址。

        Returns:
            str: 头像 URL 或包含真实头像数据的 base64 data URI。
        """
        if avatar_url:
            return avatar_url
        candidate_paths = [
            pathlib.Path("data/ai_ui_snapshot_profile/gemini/google_avatar.png"),
            pathlib.Path("data/ai_ui_snapshot_profile/deepseek/google_avatar.png"),
        ]
        for p in candidate_paths:
            if p.is_file():
                try:
                    data = p.read_bytes()
                    if data:
                        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
                except Exception:  # noqa: BLE001
                    pass
        return ""

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
            # 2. 用 JS 原生 click 点击可点选项容器（role=radio，Playwright
            #    locator.click 会命中辅助文本层导致不触发 React 切换）
            clicked = await page.evaluate(
                """(text) => {
                    const els = Array.from(document.querySelectorAll("div[class*='_9f2341b']"));
                    const target = els.find(el => {
                        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                        return t === text && el.getAttribute('role') === 'radio';
                    });
                    if (!target) return false;
                    target.click();
                    return true;
                }""",
                normalized,
            )
            if not clicked:
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

        依次探测：①模式触发器（``span[class*='321831d']``，新对话可见）；
        ②面板展开时的 aria-checked 选项；③历史会话页顶部 ``the-header``
        中的模式文本（历史会话无模式选择器，模式在顶部展示）。

        Returns:
            str | None: 模式名（快速模式/专家模式/识图模式）；无法识别时返回 None。
        """
        try:
            return await self._page.evaluate(
                """() => {
                    const modes = ['快速模式', '专家模式', '识图模式'];
                    // 1. 优先读触发器文本（新对话：span[class*='321831d']）
                    for (const t of document.querySelectorAll("span[class*='321831d']")) {
                        const txt = (t.innerText || '').replace(/\\s+/g, ' ').trim();
                        for (const m of modes) {
                            if (txt === m) return m;
                        }
                    }
                    // 2. 面板展开时读 aria-checked 的选项容器
                    const el = document.querySelector("div[class*='_9f2341b'][aria-checked='true']");
                    if (el) {
                        const txt = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                        for (const m of modes) {
                            if (txt === m) return m;
                        }
                        return txt.split('\\n')[0] || null;
                    }
                    // 3. 历史会话页顶部 the-header 中的模式文本（无模式选择器）
                    const headers = document.querySelectorAll('[class*="the-header"], header');
                    for (const h of headers) {
                        const txt = (h.innerText || '').replace(/\\s+/g, ' ').trim();
                        for (const m of modes) {
                            if (txt.includes(m)) return m;
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

    async def get_active_conversation_title(self) -> str:
        """读取当前活跃对话的标题（侧边栏 active 项）。

        新建对话提问后 DeepSeek 会自动生成标题，读取后供上层返回给
        LLM 记住对话身份。无活跃项或页面未就绪时返回空字符串。

        Returns:
            str: 当前活跃对话标题；未取到时为空字符串。
        """
        try:
            return str(await self._page.evaluate(ACTIVE_CONVERSATION_TITLE_SCRIPT) or "").strip()
        except Exception:  # noqa: BLE001 - 页面未就绪
            return ""

    async def get_active_conversation_id(self) -> str:
        """读取当前活跃对话的稳定 ID（URL 中的会话 UUID）。

        会话切换时标题可能变化（DeepSeek 自动命名/规范后缀），而 URL 的
        UUID 段稳定，模式锁以此 ID 为 key。无 UUID 或页面未就绪时返回空。

        Returns:
            str: 当前会话稳定 ID；未取到时为空字符串。
        """
        try:
            return str(await self._page.evaluate(ACTIVE_CONVERSATION_ID_SCRIPT) or "").strip()
        except Exception:  # noqa: BLE001 - 页面未就绪
            return ""

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

    async def wait_reply_done(self, timeout_s: int = 240) -> tuple[bool, str]:
        """轮询等待 AI 回复完成，并返回干净的最新一条 AI 回复。

        以生成中指示器（"停止生成"按钮）作强信号：只要仍在生成绝不判完成；
        指示器消失后叠加"最新回复长度连续稳定"兜底判定。轮询期间调用保活
        回调刷新会话活动时间，避免长等待被空闲清理。返回正文不含思考块。

        Args:
            timeout_s: 超时秒数。

        Returns:
            tuple[bool, str]: (是否完成, 最新一条 AI 回复正文)。
        """
        deadline = asyncio.get_running_loop().time() + timeout_s
        last_len = 0
        stable = 0
        text = ""
        while asyncio.get_running_loop().time() < deadline:
            if self._touch_cb is not None:
                try:
                    self._touch_cb()
                except Exception:  # noqa: BLE001 - 保活失败不影响等待
                    pass
            try:
                generating = bool(await self._page.evaluate(GENERATING_SCRIPT))
                text = await self.get_conversation_text(scope="last")
            except Exception:  # noqa: BLE001 - 页面未就绪
                generating = False
                text = ""
            if generating:
                # 仍在生成：重置稳定计数，绝不提前判完成
                stable = 0
                last_len = len(text)
                await asyncio.sleep(POLL_INTERVAL_S)
                continue
            if len(text) > last_len:
                last_len = len(text)
                stable = 0
            else:
                stable += 1
            if stable >= 4 and last_len > 10:
                return True, text
            await asyncio.sleep(POLL_INTERVAL_S)
        return False, text

    async def get_conversation_text(self, scope: str = "last") -> str:
        """按作用域提取对话文本。

        依据真实 DOM 探测：DeepSeek 所有消息均在 ``.ds-message`` 中可枚举，
        虚拟列表不卸载历史；AI 回复正文在 ``.ds-markdown``，思考块在
        ``.ds-think-content``（提取 markdown 时自然排除）。

        Args:
            scope: last（默认，最新一条 AI 回复）/ full（整段对话，
                用户消息 + AI 回复，均去思考块）。

        Returns:
            str: 提取的对话文本；无消息时返回空字符串。
        """
        try:
            return str(await self._page.evaluate(CONVERSATION_TEXT_SCRIPT, scope) or "")
        except Exception:  # noqa: BLE001 - 页面未就绪
            return ""

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

    async def _conversation_visible(self) -> bool:
        """判断消息容器当前是否可见（未撑开状态下存在且非隐藏）。"""
        try:
            return bool(await self._page.evaluate(VISIBILITY_SCRIPT, CONVERSATION_SELECTOR))
        except Exception:  # noqa: BLE001 - 页面未就绪
            return False

    async def _full_page_height(self) -> int:
        """读取整页滚动高度（像素）。"""
        try:
            return int(await self._page.evaluate("() => document.documentElement.scrollHeight")) or 0
        except Exception:  # noqa: BLE001 - 页面未就绪
            return 0

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

    async def _capture_chrome_banner(self, width: int) -> bytes | None:
        """独立渲染并截取 1:1 官方 Chromium 标准浏览器顶栏横幅。

        包含动态对话标题同步、明暗主题精准自适应、真实 Google 账号头像、
        官方 SVG 弧形裙边 Tab 与 Windows 原生窗口按钮，通过 2x 高清矢量
        渲染后独立截图，并在截完后立即销毁，绝不遮挡网页正文。

        Args:
            width: 顶栏宽度（像素），与截图视口宽度一致。

        Returns:
            bytes | None: 截取的 PNG 字节流；失败或未启用时返回 None。
        """
        if not self._decoration_enabled:
            return None

        try:
            await self._page.evaluate(
                BROWSER_CHROME_SCRIPT,
                {
                    "width": width,
                    "theme": self._decoration_theme,
                    "avatar_url": self._decoration_avatar_url,
                },
            )
            locator = self._page.locator("#mofox_chrome_banner")
            if await locator.count() > 0:
                banner_bytes: bytes = await locator.screenshot(type="png")
                return banner_bytes
            return None
        except Exception:  # noqa: BLE001 - 渲染/截图横幅失败不阻塞
            logger.warning("独立渲染浏览器外壳横幅失败，跳过外壳装饰")
            return None
        finally:
            try:
                await self._page.evaluate(BROWSER_CHROME_REMOVE_SCRIPT)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _prepend_chrome_banner(piece_bytes: bytes, banner_bytes: bytes) -> bytes:
        """使用 Pillow 将浏览器外壳横幅无缝拼接到首张截图最顶端。

        此操作为真正“在上方新增一段顶栏”，使网页内容完整顺延下移，
        绝不遮挡网页顶部的任何按钮、模式标签或 Header 元素。

        Args:
            piece_bytes: 首张截图 PNG 字节流。
            banner_bytes: 浏览器外壳横幅 PNG 字节流。

        Returns:
            bytes: 拼接后的 PNG 字节流。
        """
        try:
            piece_img = Image.open(io.BytesIO(piece_bytes))
            banner_img = Image.open(io.BytesIO(banner_bytes))

            # 确保宽度完全对齐（处理 DPI 缩放微差）
            if banner_img.width != piece_img.width:
                scale = piece_img.width / banner_img.width
                new_h = max(1, int(banner_img.height * scale))
                banner_img = banner_img.resize((piece_img.width, new_h), Image.Resampling.LANCZOS)

            total_h = banner_img.height + piece_img.height
            combined = Image.new("RGBA", (piece_img.width, total_h), (0, 0, 0, 0))
            combined.paste(banner_img, (0, 0))
            combined.paste(piece_img, (0, banner_img.height))

            out = io.BytesIO()
            combined.save(out, format="PNG")
            return out.getvalue()
        except Exception:  # noqa: BLE001 - 拼接失败回退原图
            logger.warning("合并浏览器外壳图像失败，使用原始截图")
            return piece_bytes

    async def _fullpage_shots(self) -> list[str]:
        """整页长截图；超长时按 ``max_screenshot_height`` 分片截取。

        分片以文档坐标（CSS 像素）为基准，每片高度不超过上限，输出按
        ``device_scale_factor`` 缩放为 PNG。若启用了外壳装饰，在首张截图
        最顶端无缝拼接浏览器外壳横幅，绝不遮挡网页正文内容。

        Returns:
            list[str]: PNG data URI 列表；失败返回空列表。
        """
        try:
            height = await self._full_page_height()
            if height <= 0:
                return []
            # 分片宽度取整页文档宽度（撑开后侧边栏展开会改变文档宽度）
            doc_width = int(
                await self._page.evaluate("() => document.documentElement.scrollWidth")
            ) or 0
            width = doc_width or 1440

            raw_pieces: list[bytes] = []
            if height <= self._max_screenshot_height:
                data = await self._page.screenshot(type="png", full_page=True)
                raw_pieces.append(data)
            else:
                offset = 0
                while offset < height:
                    piece_h = min(self._max_screenshot_height, height - offset)
                    clip = {"x": 0, "y": offset, "width": width, "height": piece_h}
                    data = await self._page.screenshot(type="png", full_page=True, clip=clip)
                    raw_pieces.append(data)
                    offset += piece_h

            if not raw_pieces:
                return []

            # 首张截图顶部新增拼接浏览器外壳横幅
            if self._decoration_enabled:
                banner_bytes = await self._capture_chrome_banner(width)
                if banner_bytes:
                    raw_pieces[0] = self._prepend_chrome_banner(raw_pieces[0], banner_bytes)

            return [data_uri(p) for p in raw_pieces]
        except Exception:  # noqa: BLE001 - 截图失败
            return []


