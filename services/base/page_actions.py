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
    """

    conversation_selector: str = ""
    visibility_script: str = ""
    generating_script: str = ""
    poll_interval_s: float = 2.0
    conversation_text_script: str = ""
    active_title_script: str = ""
    active_id_script: str = ""

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

    @property
    def page(self) -> Any:
        """当前页面对象。"""
        return self._page

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

    async def upload_file(
        self,
        path: str,
        *,
        max_size_mb: float = 10.0,
        allowed_extensions: str = "png,jpg,jpeg,webp,gif,bmp,md,txt,pdf,doc,docx,xls,xlsx,csv,ppt,pptx,json,py",
        attach_timeout_s: float = 15.0,
    ) -> tuple[bool, str]:
        """上传本地文件到当前网页（通过隐藏的 file input）。

        上传前校验扩展名与大小，避免把网页不支持的内容塞给输入框。
        网页上传为异步：``set_input_files`` 后轮询输入区内出现附件预览
        （图片显示为 img、文档显示为文件名+大小），确认附件挂载完成再返回。

        Args:
            path: 本地文件路径。
            max_size_mb: 允许的最大大小（MB）。
            allowed_extensions: 允许的扩展名（逗号分隔，小写）。
            attach_timeout_s: 等待附件渲染的超时秒数。

        Returns:
            tuple[bool, str]: (是否成功, 说明或错误信息)。
        """
        page = self._page
        suffix = pathlib.Path(path).suffix.lower().lstrip(".")
        allowed = {e.strip().lower().lstrip(".") for e in allowed_extensions.split(",") if e.strip()}
        if allowed and suffix not in allowed:
            return False, f"文件类型 .{suffix} 不在允许范围（{', '.join(sorted(allowed))}）"
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
        return True, f"已上传（附件检测超时，可能上传较慢）: {path}"

    # ------------------------------------------------------------------
    # 站点共享会话动作（站点脚本经类属性注入）
    # ------------------------------------------------------------------

    async def _conversation_visible(self) -> bool:
        """判断消息容器当前是否可见（未撑开状态下存在且非隐藏）。"""
        try:
            return bool(await self._page.evaluate(self.visibility_script, self.conversation_selector))
        except Exception:  # noqa: BLE001 - 页面未就绪
            return False

    async def wait_reply_done(self, timeout_s: int = 240) -> tuple[bool, str]:
        """轮询等待 AI 回复完成，并返回干净的最新一条 AI 回复。

        以生成中指示器（"停止生成/停止回答"按钮）作强信号：只要仍在生成绝不判
        完成；指示器消失后叠加"最新回复长度连续稳定"兜底判定。轮询期间调用保活
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
                generating = bool(await self._page.evaluate(self.generating_script))
                text = await self.get_conversation_text(scope="last")
            except Exception:  # noqa: BLE001 - 页面未就绪
                generating = False
                text = ""
            if generating:
                # 仍在生成：重置稳定计数，绝不提前判完成
                stable = 0
                last_len = len(text)
                await asyncio.sleep(self.poll_interval_s)
                continue
            if len(text) > last_len:
                last_len = len(text)
                stable = 0
            else:
                stable += 1
            if stable >= 4 and last_len > 10:
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

    async def get_active_conversation_id(self) -> str:
        """读取当前活跃对话的稳定 ID（URL 中的会话 UUID）。

        Returns:
            str: 当前会话稳定 ID；未取到时为空字符串。
        """
        try:
            return str(await self._page.evaluate(self.active_id_script) or "").strip()
        except Exception:  # noqa: BLE001 - 页面未就绪
            return ""

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
                        return data_uri(data)
                except Exception:  # noqa: BLE001
                    pass
        return ""

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
            from ..base.chrome_banner import BROWSER_CHROME_SCRIPT

            await self._page.evaluate(
                BROWSER_CHROME_SCRIPT,
                {
                    "width": width,
                    "theme": self._decoration_theme,
                    "avatar_url": self._resolve_avatar_url(self._decoration_avatar_url),
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
