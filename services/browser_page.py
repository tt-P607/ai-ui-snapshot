"""通用浏览器页面动作。

基于 Playwright 页面对象提供与站点无关的细粒度操作（读文本/读可点元素/点击/
输入/按键/滚动/上传文件），供 DeepSeek 专属动作层（BrowserActions）组合复用。
DeepSeek 使用哈希 class，因此定位主要依赖文本与可点击语义，辅以坐标兜底。
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import Any

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("ai_ui_snapshot.browser_page")


class PageActions:
    """封装对单个页面对象的通用细粒度操作。

    Attributes:
        page: 当前页面对象。
    """

    def __init__(self, page: Any) -> None:
        """初始化。

        Args:
            page: Playwright 页面对象。
        """
        self._page = page

    @property
    def page(self) -> Any:
        """当前页面对象。"""
        return self._page

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

        DeepSeek 页面里模式选项等可点元素是带自定义 class 的容器
        （如 ``div[class*='_9f2341b']``），而 ``get_by_text`` 会命中隐藏的
        辅助文本副本导致点击无效，因此先尝试命中可见容器。

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
        DeepSeek 上传为异步：``set_input_files`` 后轮询输入区内出现
        附件预览（图片显示为 img、文档显示为文件名+大小），确认附件
        挂载完成再返回。

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
