"""通用浏览器页面动作。

基于 Playwright 页面对象提供与站点无关的细粒度操作（读文本/读可点元素/点击/
输入/按键/滚动/上传文件），以及各站点共用的会话动作（等待回复、对话文本提取、
活跃会话标题/ID 读取、消息可见性检测、头像解析）。

站点间有差异的脚本（生成中指示器、对话文本提取、会话标题/ID、消息容器选择器）
以**类属性**形式声明，站点专属动作类继承后仅需覆盖对应类属性即可复用本类方法，
无需复制方法体。
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import Any

from src.app.plugin_system.api.log_api import get_logger

from .utils import data_uri

logger = get_logger("ai_ui_snapshot.page_actions")


class PageActions:
    """封装对单个页面对象的通用细粒度操作与站点共享会话动作。

    Attributes:
        page: 当前页面对象。
        conversation_selector: 消息容器选择器（站点差异，子类覆盖）。
        visibility_script: 消息容器可见性检测脚本（站点差异，子类覆盖）。
        generating_script: 生成中指示器探测脚本（站点差异，子类覆盖）。
        poll_interval_s: 等待回复轮询间隔秒数（站点差异，子类覆盖）。
        conversation_text_script: 对话文本提取脚本（站点差异，子类覆盖）。
        active_title_script: 活跃会话标题提取脚本（站点差异，子类覆盖）。
        active_id_script: 活跃会话稳定 ID 提取脚本（站点差异，子类覆盖）。
        fingerprint_script: 会话切换指纹脚本（站点差异，子类覆盖；可选）。
    """

    conversation_selector: str = ""
    visibility_script: str = ""
    generating_script: str = ""
    poll_interval_s: float = 2.0
    conversation_text_script: str = ""
    active_title_script: str = ""
    active_id_script: str = ""
    fingerprint_script: str = ""
    # 站点风控拦截提示检测脚本（站点差异，子类覆盖）：返回拦截提示文本，
    # 无拦截返回空字符串。站点无人机校验时留空即不检测。
    blocker_script: str = ""

    def __init__(
        self,
        page: Any,
        *,
        touch_cb: Any | None = None,
        max_screenshot_height: int = 8000,
        decoration_enabled: bool = True,
        decoration_theme: str = "auto",
        decoration_avatar_url: str = "",
    ) -> None:
        """初始化。

        Args:
            page: Playwright 页面对象。
            touch_cb: 可选保活回调（刷新会话活动时间），长等待中调用。
            max_screenshot_height: 长截图单张最大高度（像素），超出分片截取。
            decoration_enabled: 截图时是否在顶部叠加浏览器外壳装饰。
            decoration_theme: 外壳配色（auto/light/dark）。
            decoration_avatar_url: 自定义 Google 账号头像 URL。
        """
        self._page = page
        self._touch_cb = touch_cb
        self._max_screenshot_height = max_screenshot_height
        self._decoration_enabled = decoration_enabled
        self._decoration_theme = decoration_theme
        self._decoration_avatar_url = decoration_avatar_url
        self.last_blocker: str = ""

    @property
    def page(self) -> Any:
        """当前页面对象。"""
        return self._page

    def use_page(self, page: Any) -> None:
        """切换后续动作使用的页面。

        Args:
            page: 新的 Playwright 页面对象。
        """
        self._page = page

    async def check_blocker(self) -> str:
        """检测站点风控拦截（人机验证等），命中时记录供上层给出明确提示。

        站点触发人机校验时会弹出无法自动完成的验证层，此时继续轮询只会
        等到超时并给出模糊错误；提前识别可立即中断并告知人工介入。

        Returns:
            str: 拦截提示文本；无拦截或站点未配置检测脚本时为空字符串。
        """
        if not self.blocker_script:
            return ""
        try:
            hint = str(await self._page.evaluate(self.blocker_script) or "")
        except Exception:  # noqa: BLE001 - 页面未就绪
            return ""
        if hint:
            self.last_blocker = hint
        return hint

    # ------------------------------------------------------------------
    # 通用细粒度页面操作
    # ------------------------------------------------------------------

    async def read_text(self, *, max_chars: int = 6000) -> str:
        """读取页面可访问性文本（过滤脚本/样式）。

        Args:
            max_chars: 返回文本最大字符数。

        Returns:
            str: 页面文本摘要。
        """
        text = await self._page.evaluate(
            """() => {
                const clone = document.body.cloneNode(true);
                clone.querySelectorAll('script, style, noscript, svg').forEach(n => n.remove());
                const t = (clone.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim();
                return t;
            }"""
        )
        return str(text)[:max_chars]

    async def read_clickables(self, *, max_items: int = 60) -> list[dict[str, str]]:
        """读取页面可点击元素列表（角色 + 文本）。

        Args:
            max_items: 返回元素数量上限。

        Returns:
            list[dict[str, str]]: 元素列表（{role, text}）。
        """
        items = await self._page.evaluate(
            """(limit) => {
                const out = [];
                const els = document.querySelectorAll('button, a, [role="button"], [role="tab"], input[type="submit"]');
                for (const el of els) {
                    if (out.length >= limit) break;
                    const text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '')
                        .replace(/\\s+/g, ' ').trim();
                    if (!text || text.length > 40) continue;
                    out.push({ role: el.getAttribute('role') || el.tagName.toLowerCase(), text });
                }
                return out;
            }""",
            max_items,
        )
        return list(items)

    async def click(self, target: str) -> bool:
        """点击目标元素（可点容器优先，回退文本/选择器）。

        部分页面中可点元素是带自定义 class 的容器，而 ``get_by_text`` 可能命中
        隐藏的辅助文本副本导致点击无效，因此先尝试命中可见元素。

        Args:
            target: 元素文本或 CSS 选择器。

        Returns:
            bool: 是否成功点击。
        """
        page = self._page
        # 1. 文本匹配，但优先挑可见元素（跳过隐藏副本）
        try:
            loc = page.get_by_text(target, exact=False)
            count = await loc.count()
            for i in range(count):
                item = loc.nth(i)
                try:
                    if await item.is_visible():
                        await item.click(timeout=5000)
                        return True
                except Exception:  # noqa: BLE001 - 尝试下一个
                    continue
        except Exception:  # noqa: BLE001 - 继续尝试选择器
            pass
        # 2. 作为 CSS 选择器尝试
        try:
            loc = page.locator(target)
            if await loc.count() > 0:
                await loc.first.click(timeout=5000)
                return True
        except Exception:  # noqa: BLE001 - 点击失败
            pass
        return False

    async def type_text(self, text: str) -> bool:
        """向当前焦点/输入框输入文本。

        Args:
            text: 要输入的文本。

        Returns:
            bool: 是否成功输入。
        """
        page = self._page
        try:
            box = page.locator("textarea").last
            if await box.count() > 0:
                await box.click()
                await box.fill(text)
                return True
        except Exception:  # noqa: BLE001 - 尝试 contenteditable
            pass
        try:
            await page.keyboard.type(text)
            return True
        except Exception:  # noqa: BLE001 - 输入失败
            return False

    async def press(self, key: str) -> bool:
        """按下按键（Enter / Escape 等）。

        Args:
            key: 按键名（Playwright 支持）。

        Returns:
            bool: 是否成功。
        """
        try:
            await self._page.keyboard.press(key)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def scroll(self, direction: str) -> bool:
        """滚动页面。

        Args:
            direction: up / down / top / bottom。

        Returns:
            bool: 是否成功。
        """
        script = {
            "up": "window.scrollBy(0, -600)",
            "down": "window.scrollBy(0, 600)",
            "top": "window.scrollTo(0, 0)",
            "bottom": "window.scrollTo(0, document.body.scrollHeight)",
        }.get(direction)
        if not script:
            return False
        try:
            await self._page.evaluate(script)
            await self._page.wait_for_timeout(300)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def page_allowed_extensions(self) -> set[str]:
        """读取网页文件输入的 accept 扩展名集合（以网页为白名单来源）。

        站点自己维护可用类型且会随时扩充，插件内手写白名单必然滞后；直接读
        网页声明的 accept 可与之保持一致。

        Returns:
            set[str]: 扩展名集合（小写、不含点）；网页未逐个声明时返回空集合。
        """
        try:
            raw = str(
                await self._page.evaluate(
                    """() => {
                        for (const f of document.querySelectorAll('input[type=file]')) {
                            if (f.accept) return f.accept;
                        }
                        return '';
                    }"""
                )
                or ""
            )
        except Exception:  # noqa: BLE001 - 页面未就绪
            return set()
        exts: set[str] = set()
        for token in raw.split(","):
            token = token.strip().lower()
            if token.startswith("."):
                exts.add(token.lstrip("."))
        return exts

    async def upload_file(
        self,
        path: str,
        *,
        max_size_mb: float = 10.0,
        allowed_extensions: str | None = None,
        attach_timeout_s: float = 15.0,
    ) -> tuple[bool, str]:
        """上传本地文件到当前网页（通过隐藏的 file input）。

        上传前校验扩展名与大小，避免把网页不支持的内容塞给输入框。
        网页上传为异步：``set_input_files`` 后轮询输入区内出现附件预览
        （图片显示为 img、文档显示为文件名+大小），确认附件挂载完成再返回；
        网页拒绝该文件时预览不会出现，此时返回失败（而非静默当作成功）。

        Args:
            path: 本地文件路径。
            max_size_mb: 允许的最大大小（MB）。
            allowed_extensions: 允许的扩展名（逗号分隔，小写）；None 时按
                网页 accept 声明校验（以站点为准）。
            attach_timeout_s: 等待附件渲染的超时秒数。

        Returns:
            tuple[bool, str]: (是否成功, 说明或错误信息)。
        """
        page = self._page
        suffix = pathlib.Path(path).suffix.lower().lstrip(".")
        if allowed_extensions is None:
            allowed = await self.page_allowed_extensions()
        else:
            allowed = {
                e.strip().lower().lstrip(".") for e in allowed_extensions.split(",") if e.strip()
            }
        if allowed and suffix not in allowed:
            # 网页可接受类型可达数百种，全量列出无意义，仅给出数量
            supported = ", ".join(sorted(allowed))
            if len(allowed) > 20:
                supported = f"网页共支持 {len(allowed)} 种"
            return False, f"文件类型 .{suffix} 不在允许范围（{supported}）"
        try:
            if pathlib.Path(path).stat().st_size > max_size_mb * 1024 * 1024:
                return False, f"文件大小超过限制 {max_size_mb:g}MB"
        except OSError:
            return False, f"无法读取文件: {path}"
        try:
            file_input = page.locator('input[type="file"]').first
            if await file_input.count() == 0:
                return False, "上传失败（未找到网页文件输入）"
            await file_input.set_input_files(path)
        except Exception as exc:  # noqa: BLE001 - 未找到文件输入
            logger.warning(f"上传失败，未找到 file input: {path}")
            return False, f"上传失败（未找到网页文件输入）: {exc}"
        # 轮询输入区内出现附件预览（图片 img 或文档文件名文本）
        file_base = pathlib.Path(path).stem.lower()
        deadline = asyncio.get_running_loop().time() + attach_timeout_s
        attached = False
        while asyncio.get_running_loop().time() < deadline:
            attached = bool(
                await page.evaluate(
                    """(name) => {
                        const ta = document.querySelector('textarea');
                        if (!ta) return false;
                        let cur = ta.closest('form, div') || ta.parentElement;
                        // 向上最多 6 层：找输入区内的 img，或包含附件文件名的文本
                        for (let i = 0; i < 6 && cur; i++) {
                            if (cur.querySelectorAll('img').length > 0) return true;
                            if (name && (cur.innerText || '').toLowerCase().includes(name)) return true;
                            cur = cur.parentElement;
                        }
                        return false;
                    }""",
                    file_base,
                )
            )
            if attached:
                break
            await asyncio.sleep(0.5)
        if attached:
            await page.wait_for_timeout(800)
            return True, f"已上传并等待附件就绪: {path}"
        # 网页拒绝该文件时输入区不会出现附件预览，必须报失败：
        # 否则提问会在无附件的情况下发出，得到与预期不符的回复。
        return False, f"上传失败（网页未接受该附件，可能类型或大小不支持）: {path}"

    # ------------------------------------------------------------------
    # 站点共享会话动作（站点脚本经类属性注入）
    # ------------------------------------------------------------------

    async def _conversation_visible(self) -> bool:
        """判断消息容器当前是否可见（未撑开状态下存在且非隐藏）。"""
        try:
            return bool(await self._page.evaluate(self.visibility_script, self.conversation_selector))
        except Exception:  # noqa: BLE001 - 页面未就绪
            return False

    async def wait_reply_done(
        self,
        timeout_s: int = 240,
        *,
        previous_reply: str | None = None,
    ) -> tuple[bool, str]:
        """轮询等待 AI 回复完成，并返回干净的最新一条 AI 回复。

        以生成中指示器（站点自定：停止按钮/停止文案）作强信号：只要仍在生成绝不
        判完成；指示器消失后叠加"最新回复内容连续稳定"兜底判定。轮询期间调用
        保活回调刷新会话活动时间，避免长等待被空闲清理。返回正文不含思考块。

        Args:
            timeout_s: 超时秒数。
            previous_reply: 发送前的上一条 AI 回复；提供时必须等到正文发生变化。

        Returns:
            tuple[bool, str]: (是否完成, 最新一条 AI 回复正文)。
        """
        deadline = asyncio.get_running_loop().time() + timeout_s
        last_text = ""
        stable = 0
        text = ""
        while asyncio.get_running_loop().time() < deadline:
            if self._touch_cb is not None:
                try:
                    self._touch_cb()
                except Exception:  # noqa: BLE001 - 保活失败不影响等待
                    pass
            try:
                generating = bool(await self._page.evaluate(self.generating_script))
                text = await self.get_conversation_text(scope="last")
            except Exception:  # noqa: BLE001 - 页面未就绪
                generating = False
                text = ""
            if await self.check_blocker():
                # 站点弹了人机验证：提问无法继续，立即中断等待
                return False, text
            if generating:
                # 仍在生成：重置稳定计数，绝不提前判完成
                stable = 0
                last_text = text
                await asyncio.sleep(self.poll_interval_s)
                continue
            if previous_reply is not None and text == previous_reply:
                # 思考结束到正文开始之间，页面仍保留上一轮回复；不能将其当作本轮结果。
                stable = 0
                last_text = text
            elif not text:
                stable = 0
                last_text = text
            elif text != last_text:
                last_text = text
                stable = 0
            else:
                stable += 1
            # 正文已开始且内容连续稳定；不用更大阈值，否则短回复会被误判为超时。
            if stable >= 4 and text:
                return True, text
            await asyncio.sleep(self.poll_interval_s)
        return False, text

    async def get_conversation_text(self, scope: str = "last") -> str:
        """按作用域提取对话文本（模型/AI 回复正文）。

        Args:
            scope: last（默认，最新一条 AI 回复）/ full（整段对话）。

        Returns:
            str: 提取的对话文本；无消息时返回空字符串。
        """
        try:
            return str(await self._page.evaluate(self.conversation_text_script, scope) or "")
        except Exception:  # noqa: BLE001 - 页面未就绪
            return ""

    async def get_active_conversation_title(self) -> str:
        """读取当前活跃对话的标题（侧边栏选中项首行）。

        Returns:
            str: 当前活跃对话标题；未取到时为空字符串。
        """
        try:
            return str(await self._page.evaluate(self.active_title_script) or "").strip()
        except Exception:  # noqa: BLE001 - 页面未就绪
            return ""

    async def wait_conversation_title(self, timeout_s: int = 8) -> str:
        """等待新对话标题由 AI 生成后返回（首条提问时标题异步生成）。

        Args:
            timeout_s: 等待超时秒数。

        Returns:
            str: 生成的对话标题；超时仍未生成时返回空字符串。
        """
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            title = await self.get_active_conversation_title()
            if title:
                return title
            await asyncio.sleep(1)
        return ""

    async def new_chat(self) -> bool:
        """新建对话（站点子类覆盖）。"""
        return False

    async def list_conversations(self) -> list[str]:
        """列出历史会话标题（站点子类覆盖）。"""
        return []

    async def open_conversation(self, title: str) -> bool:
        """打开指定标题的历史会话（站点子类覆盖）。"""
        return False

    async def get_active_conversation_id(self) -> str:
        """读取当前活跃对话的稳定 ID（URL 中的会话 UUID）。

        Returns:
            str: 当前会话稳定 ID；未取到时为空字符串。
        """
        try:
            return str(await self._page.evaluate(self.active_id_script) or "").strip()
        except Exception:  # noqa: BLE001 - 页面未就绪
            return ""

    async def _wait_session_switch(
        self,
        *,
        target_id: str = "",
        target_title: str = "",
        before_fingerprint: str = "",
        timeout_s: float = 3.0,
        interval_s: float = 0.25,
    ) -> bool:
        """等待会话切换到目标（仅用于观察切换是否生效）。

        三个判据任一命中即视为已确认：活跃会话 ID 等于目标 ID、活跃标题等于
        目标标题、会话指纹与切换前不同。先立即探测一次（已在目标会话或页面
        已即时更新时秒回），再按 interval_s 轮询至超时。

        侧边栏点击是前端路由跳转，实测三站点 URL/标题均在 0.5 秒内更新
        （gemini 0.06s / doubao 0.31s / deepseek 0.05s），故超时只需留足
        余量；未命中时只多花这么点时间。

        Args:
            target_id: 目标会话稳定 ID（为空时跳过该判据）。
            target_title: 目标会话标题（为空时跳过该判据）。
            before_fingerprint: 切换前会话指纹（站点未配置指纹脚本时忽略）。
            timeout_s: 轮询超时秒数。
            interval_s: 轮询间隔秒数。

        Returns:
            bool: 是否观测到切换生效；超时未观测到时返回 False。
        """
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            if target_id and (await self.get_active_conversation_id()) == target_id:
                return True
            if target_title and (await self.get_active_conversation_title()) == target_title:
                return True
            if before_fingerprint and self.fingerprint_script:
                now = str(await self._page.evaluate(self.fingerprint_script) or "")
                if now != before_fingerprint:
                    return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(interval_s)
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            if target_id and (await self.get_active_conversation_id()) == target_id:
                return True
            if target_title and (await self.get_active_conversation_title()) == target_title:
                return True
            if before_fingerprint and self.fingerprint_script:
                now = str(await self._page.evaluate(self.fingerprint_script) or "")
                if now != before_fingerprint:
                    return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(1)

    # ------------------------------------------------------------------
    # 浏览器外壳装饰（站点无关：Chrome 顶栏横幅）
    # ------------------------------------------------------------------

    async def _capture_chrome_banner(self, width: int) -> bytes | None:
        """独立渲染并截取浏览器外壳顶栏横幅（标签页/地址栏/头像）。

        复用站点无关的浏览器外壳脚本（:mod:`chrome_banner`）：注入临时
        ``#mofox_chrome_banner`` 节点并独立截图，截完立即销毁。

        Args:
            width: 顶栏宽度（像素），与截图视口宽度一致。

        Returns:
            bytes | None: 截取的 PNG 字节流；失败或未启用时返回 None。
        """
        if not self._decoration_enabled:
            return None
        try:
            # 部分页面启用 TrustedHTML（CSP trusted-types），内联 innerHTML 被拒。
            # 先注入默认 trustedTypes policy 允许 HTML 赋值，再渲染横幅（无则跳过）。
            await self._page.evaluate(
                """() => {
                    if (window.trustedTypes && window.trustedTypes.createPolicy) {
                        try {
                            window.trustedTypes.createPolicy('default', { createHTML: (s) => s });
                        } catch (e) { /* policy 已存在则忽略 */ }
                    }
                    return true;
                }"""
            )
            from ..base.chrome_banner import BROWSER_CHROME_SCRIPT

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
                return await locator.screenshot(type="png")
            return None
        except Exception:  # noqa: BLE001 - 渲染/截图横幅失败不阻塞
            logger.warning("渲染浏览器外壳横幅失败，跳过外壳装饰")
            return None
        finally:
            try:
                from ..base.chrome_banner import BROWSER_CHROME_REMOVE_SCRIPT

                await self._page.evaluate(BROWSER_CHROME_REMOVE_SCRIPT)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _prepend_chrome_banner(piece_bytes: bytes, banner_bytes: bytes) -> bytes:
        """使用 Pillow 将浏览器外壳横幅拼接到首张截图最顶端。

        此操作为真正"在上方新增一段顶栏"，使网页内容完整顺延下移，
        绝不遮挡网页顶部的任何按钮、模式标签或 Header 元素。

        Args:
            piece_bytes: 首张截图 PNG 字节流。
            banner_bytes: 浏览器外壳横幅 PNG 字节流。

        Returns:
            bytes: 拼接后的 PNG 字节流。
        """
        try:
            from PIL import Image

            import io

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

    async def _encode_png_pieces(
        self,
        pieces: list[bytes],
        *,
        width: int,
    ) -> list[str]:
        """统一装饰并编码截图 PNG 分片。

        Args:
            pieces: 按页面顺序排列的 PNG 字节列表。
            width: 截图宽度，用于渲染浏览器外壳。

        Returns:
            list[str]: PNG data URI 列表；空输入返回空列表。
        """
        if not pieces:
            return []
        if self._decoration_enabled:
            banner = await self._capture_chrome_banner(width)
            if banner:
                pieces[0] = self._prepend_chrome_banner(pieces[0], banner)
        return [data_uri(piece) for piece in pieces]

    # ------------------------------------------------------------------
    # 整页长截图（站点无关：分片 + 外壳横幅）
    # ------------------------------------------------------------------

    async def _full_page_height(self) -> int:
        """读取整页滚动高度（像素）。"""
        try:
            return int(await self._page.evaluate("() => document.documentElement.scrollHeight")) or 0
        except Exception:  # noqa: BLE001 - 页面未就绪
            return 0

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

            return await self._encode_png_pieces(raw_pieces, width=width)
        except Exception:  # noqa: BLE001 - 截图失败
            return []

    async def _expanded_fullpage_shots(
        self,
        expand_script: str,
        restore_script: str,
        *,
        expand_arg: Any | None = None,
        saved_key: str | None = None,
        require_saved: bool = False,
        wait_ms: int = 200,
    ) -> list[str]:
        """在站点临时展开页面后截图，并保证恢复页面状态。

        Args:
            expand_script: 站点专属页面展开脚本。
            restore_script: 站点专属页面恢复脚本。
            expand_arg: 传给展开脚本的可选参数。
            saved_key: 展开结果中保存恢复数据的键；为空时结果本身即恢复数据。
            require_saved: 未取得恢复数据时是否放弃截图。
            wait_ms: 展开后等待页面重排的毫秒数。

        Returns:
            list[str]: 整页截图 data URI 列表；未成功展开时返回空列表。
        """
        result = (
            await self._page.evaluate(expand_script)
            if expand_arg is None
            else await self._page.evaluate(expand_script, expand_arg)
        )
        saved = result.get(saved_key) if saved_key and isinstance(result, dict) else result
        if require_saved and not saved:
            return []
        try:
            await self._page.wait_for_timeout(wait_ms)
            return await self._fullpage_shots()
        finally:
            if saved is not None:
                try:
                    await self._page.evaluate(restore_script, {"saved": saved})
                except Exception:  # noqa: BLE001 - 恢复失败不阻塞截图结果
                    pass

    async def _viewport_shot(self) -> list[str]:
        """截取当前正常视口窗口（人类可读的标准桌面浏览器比例），带外壳装饰。

        直接捕获当前可视区域（full_page=False），宽高比与真实桌面浏览器一致
        （例如 1280x800 或 1440x900）。首张截图顶部拼接 Chrome 外壳横幅，
        形成逼真、可读、未拉伸畸变的正常窗口截图。

        Returns:
            list[str]: PNG data URI 列表（通常仅含 1 张）；失败返回空列表。
        """
        try:
            viewport = self._page.viewport_size or {"width": 1280, "height": 800}
            width = int(viewport.get("width", 1280))
            data = await self._page.screenshot(type="png", full_page=False)
            return await self._encode_png_pieces([data], width=width)
        except Exception:  # noqa: BLE001 - 截图失败
            return []

    async def _rounds_shot(self, rounds: int = 1) -> list[str]:
        """从后往前倒序截取最近 N 轮完整回复及提问的对话区域。

        定位末尾 N 个回复与其对应的提问行，计算该区域在文档中的真实坐标
        进行精准裁切截图。若高度在 max_screenshot_height 范围内则完整
        单张输出（带浏览器外壳）；超出则按高度分片。

        Args:
            rounds: 截取的回复轮数（默认 1，即截取最后一个完整问答）。

        Returns:
            list[str]: PNG data URI 列表。
        """
        try:
            num = max(1, rounds)
            clip_info = await self._page.evaluate(
                """(r) => {
                    const aiSelectors = '[class*="md-box-root"], .ds-markdown, [class*="model-response"], [data-role="assistant"]';
                    let aiReplies = Array.from(document.querySelectorAll(aiSelectors));
                    if (!aiReplies.length) {
                        aiReplies = Array.from(document.querySelectorAll('[class*="v_list_row"], [class*="message"]'));
                    }
                    if (!aiReplies.length) return null;

                    const targets = aiReplies.slice(-r);
                    const firstTarget = targets[0];
                    const lastTarget = targets[targets.length - 1];

                    let startEl = firstTarget.closest('[class*="v_list_row"], [class*="chat-message"]') || firstTarget;
                    if (startEl && startEl.previousElementSibling) {
                        const prev = startEl.previousElementSibling;
                        if (prev.querySelector('[class*="send-msg-bubble"], [class*="user"], [data-role="user"]')
                            || (prev.innerText || '').length > 0) {
                            startEl = prev;
                        }
                    }
                    let endEl = lastTarget.closest('[class*="v_list_row"], [class*="chat-message"]') || lastTarget;

                    try { startEl.scrollIntoView({ block: 'start' }); } catch(e) {}

                    const startRect = startEl.getBoundingClientRect();
                    const endRect = endEl.getBoundingClientRect();

                    const scrollY = window.scrollY || document.documentElement.scrollTop || 0;
                    const top = Math.max(0, startRect.top + scrollY);
                    const bottom = endRect.bottom + scrollY;
                    const height = Math.max(120, bottom - top);
                    const width = document.documentElement.scrollWidth || window.innerWidth || 1280;

                    return { x: 0, y: top, width: width, height: height };
                }""",
                num,
            )
            if not clip_info:
                # 无法按元素定位轮次时退回视口截图
                return await self._viewport_shot()

            width = int(clip_info.get("width") or 1280)
            height = int(clip_info.get("height") or 800)
            base_y = float(clip_info.get("y") or 0)

            raw_pieces: list[bytes] = []
            if height <= self._max_screenshot_height:
                clip = {"x": 0, "y": base_y, "width": width, "height": height}
                data = await self._page.screenshot(type="png", clip=clip)
                raw_pieces.append(data)
            else:
                offset = 0
                while offset < height:
                    piece_h = min(self._max_screenshot_height, height - offset)
                    clip = {"x": 0, "y": base_y + offset, "width": width, "height": piece_h}
                    data = await self._page.screenshot(type="png", clip=clip)
                    raw_pieces.append(data)
                    offset += piece_h

            if not raw_pieces:
                return await self._viewport_shot()
            return await self._encode_png_pieces(raw_pieces, width=width)
        except Exception:  # noqa: BLE001 - 异常退回视口截图
            return await self._viewport_shot()

    async def get_snapshot_meta(self, scope: str = "viewport", rounds: int = 1) -> dict[str, Any]:
        """提取当前截图画面的内容摘要与截断感知状态。

        为 Bot 提供结构化反馈，使其清晰获知：
        - 画面中展示了哪些内容、包含了几轮回复；
        - 是否有更早的历史消息在上方被截断；
        - 底部是否有未完全展示的内容；
        - 截图中是否包含了模型生成的图片。

        Args:
            scope: 截图范围模式（viewport/rounds/full）。
            rounds: 截取的轮数。

        Returns:
            dict[str, Any]: 元数据字典。
        """
        try:
            meta = await self._page.evaluate(
                """([sc, rd]) => {
                    const scrollY = window.scrollY || document.documentElement.scrollTop || 0;
                    const maxScroll = Math.max(0, (document.documentElement.scrollHeight || 0) - (window.innerHeight || 0));
                    const hasEarlier = scrollY > 80;
                    const hasLater = (maxScroll - scrollY) > 80;

                    const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
                    const snippet = text.slice(0, 150);

                    const imgs = Array.from(document.querySelectorAll('img'))
                        .filter(im => im.naturalWidth >= 200 && im.naturalHeight >= 200);

                    return {
                        scope: sc,
                        rounds: rd,
                        has_earlier_history: hasEarlier,
                        has_later_content: hasLater,
                        visible_images_count: imgs.length,
                        visible_snippet: snippet,
                    };
                }""",
                [scope, rounds],
            )
            return meta if isinstance(meta, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _vstack_png(pieces: list[bytes]) -> bytes:
        """纵向拼接多张 PNG（Pillow 依赖可选，缺失时退回首张）。

        Args:
            pieces: PNG 字节列表（自上而下顺序）。

        Returns:
            bytes: 拼接后的 PNG 字节。
        """
        try:
            from PIL import Image

            import io

            imgs = [Image.open(io.BytesIO(b)).convert("RGB") for b in pieces]
            width = max(im.width for im in imgs)
            total_h = sum(im.height for im in imgs)
            canvas = Image.new("RGB", (width, total_h), "white")
            y = 0
            for im in imgs:
                canvas.paste(im, (0, y))
                y += im.height
            buf = io.BytesIO()
            canvas.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:  # noqa: BLE001 - 拼接失败退回首张
            return pieces[0]
