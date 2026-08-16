"""Gemini (Google AI) 专属浏览器动作。

组合通用页面操作（:class:`PageActions`）与 Gemini 站点语义（模型切换、提问、
等待回复、长截图、附件上传），供上层业务（snapshot_service）使用。站点专属
常量与脚本集中在 :mod:`gemini_constants`，选择器变动仅需在该处同步。
"""

from __future__ import annotations

import asyncio
import base64
import pathlib
import time
from typing import Any


from src.app.plugin_system.api.log_api import get_logger

from .browser_page import PageActions
from .deepseek_constants import BROWSER_CHROME_REMOVE_SCRIPT, BROWSER_CHROME_SCRIPT
from .gemini_constants import (
    ACTIVE_CONVERSATION_ID_SCRIPT,
    ACTIVE_CONVERSATION_TITLE_SCRIPT,
    CLICK_MAKE_IMAGE_SCRIPT,
    CLICK_SHARE_ITEM_SCRIPT,
    CLICK_SHARE_MENU_SCRIPT,
    CONVERSATION_CONTENT_SELECTOR,
    CONVERSATION_SELECTOR,
    CONVERSATION_TEXT_SCRIPT,
    DOWNLOAD_IMAGE_BUTTON,
    FILE_INPUT_SELECTOR,
    FINGERPRINT_SCRIPT,
    GEMINI_FULLPAGE_EXPAND_SCRIPT,
    GENERATING_SCRIPT,
    GET_MODEL_SCRIPT,
    GET_THEME_SCRIPT,
    HISTORY_LIST_SCRIPT,
    HISTORY_OPEN_SCRIPT,
    IMAGE_GENERATED_SCRIPT,
    INPUT_SELECTOR,
    MODEL_ITEM_SELECTED_SCRIPT,
    MODEL_MENU_ITEM_SELECTOR,
    NEW_CHAT_SCRIPT,
    OPEN_MODEL_MENU_SCRIPT,
    POLL_INTERVAL_S,
    SEND_BUTTON_SELECTOR,
    SET_MODEL_SCRIPT,
    SET_THEME_SCRIPT,
    SHARE_LINK_READY_SCRIPT,
    SUPPORTED_MODELS,
    RESTORE_SCRIPT,
    THINKING_ITEM,
    THINKING_SELECTED_SCRIPT,
    UPLOAD_BUTTON_SELECTOR,
    VISIBILITY_SCRIPT,
)

logger = get_logger("ai_ui_snapshot.gemini_actions")


def data_uri(data: bytes) -> str:
    """将 PNG 字节流转为 data URI。

    Args:
        data: PNG 字节流。

    Returns:
        str: data URI。
    """
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


class GeminiActions(PageActions):
    """Gemini 专属浏览器动作（组合通用页面操作）。

    在 :class:`PageActions` 通用能力之上，封装 Gemini 站点语义动作：模型读写
    与切换、提问与等待回复、长截图（分片）、附件上传。站点常量见
    :mod:`gemini_constants`。

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
        theme: str = "auto",
        decoration_enabled: bool = True,
        decoration_theme: str = "auto",
        decoration_avatar_url: str = "",
    ) -> None:
        """初始化。

        Args:
            page: Playwright 页面对象。
            max_screenshot_height: 长截图单张最大高度（像素），超出分片截取。
            touch_cb: 可选保活回调（刷新会话活动时间），长等待中调用。
            theme: 页面主题（auto 按本地时间自动切换 / light / dark）。
            decoration_enabled: 截图时是否叠加浏览器外壳装饰。
            decoration_theme: 外壳配色（auto/light/dark）。
            decoration_avatar_url: 自定义 Google 账号头像 URL。
        """
        super().__init__(page)
        self._max_screenshot_height = max_screenshot_height
        self._touch_cb = touch_cb
        self._theme = self._normalize_theme(theme)
        self._decoration_enabled = decoration_enabled
        self._decoration_theme = decoration_theme
        self._decoration_avatar_url = decoration_avatar_url

    @staticmethod
    def _normalize_theme(theme: str) -> str:
        """归一化主题为 auto/light/dark。

        Args:
            theme: 原始输入（auto/light/dark，大小写不敏感）。

        Returns:
            str: auto/light/dark 之一；无法识别时回退 auto。
        """
        value = (theme or "").strip().lower()
        return value if value in ("auto", "light", "dark") else "auto"

    @staticmethod
    def _resolve_auto_theme() -> str:
        """按本地时间解析 auto 主题：18:00-06:00 为深色，其余浅色。

        Returns:
            str: light / dark。
        """
        hour = time.localtime().tm_hour
        return "dark" if hour >= 18 or hour < 6 else "light"

    async def get_theme(self) -> str:
        """读取当前页面主题（body class）。

        Returns:
            str: light / dark。
        """
        try:
            return str(await self._page.evaluate(GET_THEME_SCRIPT) or "light")
        except Exception:  # noqa: BLE001 - 页面未就绪
            return "light"

    async def set_theme(self, theme: str | None = None) -> str:
        """设置 Gemini 页面主题（改 body class，真实切换渲染）。

        Args:
            theme: 目标主题（auto/light/dark）；None 用构造器配置。

        Returns:
            str: 实际应用的主题（light/dark）。
        """
        target = self._normalize_theme(theme) if theme is not None else self._theme
        resolved = self._resolve_auto_theme() if target == "auto" else target
        try:
            await self._page.evaluate(SET_THEME_SCRIPT, resolved)
            await self._page.wait_for_timeout(200)
        except Exception:  # noqa: BLE001 - 页面未就绪
            pass
        return resolved

    # ------------------------------------------------------------------
    # 模型（模式）读写
    # ------------------------------------------------------------------

    async def get_model(self) -> str | None:
        """读取当前对话的模型（模式选择器按钮 aria-label 中"当前模式为"）。

        Returns:
            str | None: 模型名（如 3.6 Flash）；无法识别时返回 None。
        """
        try:
            return str(await self._page.evaluate(GET_MODEL_SCRIPT) or "").strip() or None
        except Exception:  # noqa: BLE001 - 页面未就绪
            return None

    @staticmethod
    def _model_key(model: str) -> str:
        """归一化模型名为匹配 key（取核心词，忽略版本/后缀/扩展思考）。

        Args:
            model: 模型名（如 3.6 Flash / Flash / Flash 扩展）。

        Returns:
            str: 归一化 key（如 flash / pro / 扩展思考）。
        """
        s = (model or "").strip().lower()
        if "扩展思考" in s:
            return "扩展思考"
        for token in ("flash-lite", "flash", "pro"):
            if token in s:
                return token
        return s

    async def get_thinking(self) -> bool:
        """读取"扩展思考"（深度思考）开关是否开启。

        优先用模式选择器按钮 aria-label（"当前模式为 Flash 扩展" 含"扩展"）判断，
        菜单关闭后仍可读；菜单展开时回退菜单项选中态。

        Returns:
            bool: 是否开启扩展思考。
        """
        try:
            model = (await self.get_model()) or ""
            if "扩展" in model:
                return True
            return bool(await self._page.evaluate(THINKING_SELECTED_SCRIPT))
        except Exception:  # noqa: BLE001 - 页面未就绪
            return False

    async def set_thinking(self, enable: bool | None = None) -> tuple[bool, str]:
        """设置"扩展思考"（深度思考）开关状态。

        "扩展思考"是叠加在模型上的开关：点菜单项切换开/关，不影响当前模型。

        Args:
            enable: True 开启 / False 关闭 / None 不修改（仅返回当前状态）。

        Returns:
            tuple[bool, str]: (是否成功, 状态说明)。
        """
        page = self._page
        try:
            # 1. 展开模式选择器
            await page.evaluate(OPEN_MODEL_MENU_SCRIPT)
            await page.wait_for_timeout(800)
            # 2. 读取当前扩展思考状态（菜单展开时读选中态最准）
            current = bool(await page.evaluate(THINKING_SELECTED_SCRIPT))
            if enable is None:
                await page.keyboard.press("Escape")
                return True, f"扩展思考: {'开启' if current else '关闭'}"
            if current == enable:
                await page.keyboard.press("Escape")
                return True, f"扩展思考: 已是{'开启' if enable else '关闭'}状态"
            # 3. 点击"扩展思考"菜单项切换
            item = page.locator(MODEL_MENU_ITEM_SELECTOR).filter(has_text=THINKING_ITEM).first
            if await item.count() > 0:
                await item.click(timeout=5000)
            else:
                await page.evaluate(SET_MODEL_SCRIPT, THINKING_ITEM)
            await page.wait_for_timeout(1200)
            # 4. 校验：菜单可能已关闭，用 get_model 的"扩展"标记判断
            now_model = (await self.get_model()) or ""
            now = "扩展" in now_model
            await page.keyboard.press("Escape")
            if now == enable:
                return True, f"扩展思考: 已{'开启' if enable else '关闭'}"
            return False, f"扩展思考: 设置失败（当前{'开启' if now else '关闭'}）"
        except Exception as exc:  # noqa: BLE001 - 切换失败
            return False, f"设置扩展思考失败: {exc}"

    async def set_model(self, model: str) -> tuple[bool, str]:
        """切换 Gemini 对话模型（不改变"扩展思考"开关状态）。

        先点击模式选择器按钮展开菜单，再在菜单项中按文本匹配目标模型并点击；
        用菜单项的选中 class（selected）校验是否真正切换成功。

        Args:
            model: 目标模型名（支持 3.5 Flash-Lite / 3.6 Flash / 3.1 Pro，
                或缩写如 Flash / Pro；传"扩展思考"请改用 set_thinking）。

        Returns:
            tuple[bool, str]: (是否切换成功, 当前选中模型或错误信息)。
        """
        normalized = (model or "").strip()
        if not normalized:
            return False, "模型名不能为空"
        if self._model_key(normalized) == "扩展思考":
            return False, "扩展思考是独立开关，请用 set_thinking 控制"
        page = self._page
        try:
            # 1. 展开模式选择器
            await page.evaluate(OPEN_MODEL_MENU_SCRIPT)
            await page.wait_for_timeout(800)
            # 2. 先确认菜单项存在（避免点击落空），再点击
            state = await page.evaluate(MODEL_ITEM_SELECTED_SCRIPT, normalized)
            hit = isinstance(state, dict) and state.get("hit")
            if not hit:
                await page.keyboard.press("Escape")
                return False, f"未找到模型: {normalized}（可选: {', '.join(SUPPORTED_MODELS)}）"
            item = page.locator(MODEL_MENU_ITEM_SELECTOR).filter(has_text=normalized).first
            if await item.count() > 0:
                await item.click(timeout=5000)
            else:
                await page.evaluate(SET_MODEL_SCRIPT, normalized)
            await page.wait_for_timeout(1000)
            # 3. 用 get_model 短名归一化校验（菜单已关闭，用 aria-label 判断）
            current = await self.get_model() or ""
            if current and self._model_key(current) == self._model_key(normalized):
                await page.keyboard.press("Escape")
                return True, current
            await page.keyboard.press("Escape")
            return False, f"切换模型失败，当前为: {current or '未知'}"
        except Exception as exc:  # noqa: BLE001 - 切换失败
            return False, f"切换模型失败: {exc}"

    # ------------------------------------------------------------------
    # 提问与等待回复
    # ------------------------------------------------------------------

    async def ask(self, question: str) -> tuple[bool, str]:
        """向 Gemini 输入问题并发送。

        先隐藏可能拦截点击的浮层遮罩（如 Beta 提示 overlay），再点击输入框。

        Args:
            question: 用户问题。

        Returns:
            tuple[bool, str]: (是否成功, 说明或错误信息)。
        """
        page = self._page
        try:
            # 隐藏 cdk overlay 浮层（Beta 提示等），避免拦截输入框点击
            await page.evaluate(
                """() => {
                    const overlays = document.querySelectorAll('.cdk-overlay-container, .cdk-overlay-backdrop');
                    for (const el of overlays) {
                        el.style.setProperty('display', 'none', 'important');
                    }
                }"""
            )
            await page.wait_for_timeout(300)
            editor = page.locator(INPUT_SELECTOR).first
            if await editor.count() == 0:
                return False, "未找到 Gemini 输入框"
            await editor.click()
            await editor.fill(question.strip())
            await page.wait_for_timeout(500)
            # 点击发送按钮（输入后出现）
            send_btn = page.locator(SEND_BUTTON_SELECTOR).first
            if await send_btn.count() > 0 and await send_btn.is_visible():
                await send_btn.click(timeout=5000)
                return True, "已发送"
            # 回退 Enter
            await page.keyboard.press("Enter")
            return True, "已发送（Enter）"
        except Exception as exc:  # noqa: BLE001 - 发送失败
            return False, f"发送失败: {exc}"

    async def wait_reply_done(self, timeout_s: int = 240) -> tuple[bool, str]:
        """轮询等待 AI 回复完成，并返回干净的最新一条模型回复。

        以生成中指示器（"停止回答"按钮）作强信号：只要仍在生成绝不判完成；
        指示器消失后叠加"最新回复长度连续稳定"兜底判定。

        Args:
            timeout_s: 超时秒数。

        Returns:
            tuple[bool, str]: (是否完成, 最新一条模型回复正文)。
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
        """按作用域提取对话文本（模型回复正文）。

        Args:
            scope: last（默认，最新一条模型回复）/ full（全部模型回复）。

        Returns:
            str: 提取的对话文本；无消息时返回空字符串。
        """
        try:
            return str(await self._page.evaluate(CONVERSATION_TEXT_SCRIPT, scope) or "")
        except Exception:  # noqa: BLE001 - 页面未就绪
            return ""

    async def create_share_link(self, timeout_s: int = 30) -> str | None:
        """创建并获取当前对话的 Gemini 公开分享链接。

        打开对话操作菜单 → 点击"分享对话内容" → 等待"正在创建链接…"消失 →
        读取弹窗内公开链接（``a.link-url`` href，如 https://share.gemini.google/xxx）。

        Args:
            timeout_s: 等待链接生成的超时秒数。

        Returns:
            str | None: 分享链接 URL；失败返回 None。
        """
        page = self._page
        try:
            # 1. 打开对话操作菜单
            await page.evaluate(CLICK_SHARE_MENU_SCRIPT)
            await page.wait_for_timeout(1200)
            # 2. 点击"分享对话内容"
            if not await page.evaluate(CLICK_SHARE_ITEM_SCRIPT):
                await page.keyboard.press("Escape")
                return None
            await page.wait_for_timeout(1500)
            # 3. 轮询等待公开链接出现
            deadline = asyncio.get_running_loop().time() + timeout_s
            while asyncio.get_running_loop().time() < deadline:
                link = str(await page.evaluate(SHARE_LINK_READY_SCRIPT) or "").strip()
                if link and link.startswith("http"):
                    await page.keyboard.press("Escape")
                    return link
                await asyncio.sleep(1)
            await page.keyboard.press("Escape")
            return None
        except Exception:  # noqa: BLE001 - 分享失败
            return None

    # ------------------------------------------------------------------
    # 历史会话
    # ------------------------------------------------------------------

    async def get_active_conversation_title(self) -> str:
        """读取当前活跃对话的标题（侧边栏选中项首行）。

        Returns:
            str: 当前活跃对话标题；未取到时为空字符串。
        """
        try:
            return str(await self._page.evaluate(ACTIVE_CONVERSATION_TITLE_SCRIPT) or "").strip()
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
        """读取当前活跃对话的稳定 ID（URL 中 /app/<id> 的会话 UUID）。

        Returns:
            str: 当前会话稳定 ID；未取到时为空字符串。
        """
        try:
            return str(await self._page.evaluate(ACTIVE_CONVERSATION_ID_SCRIPT) or "").strip()
        except Exception:  # noqa: BLE001 - 页面未就绪
            return ""

    async def new_chat(self) -> bool:
        """开启一个新对话（点击侧边栏"发起新对话"）。

        Returns:
            bool: 是否成功点击。
        """
        try:
            return bool(await self._page.evaluate(NEW_CHAT_SCRIPT))
        except Exception:  # noqa: BLE001 - 页面未就绪
            return False

    async def list_conversations(self) -> list[str]:
        """列出侧边栏历史会话标题（去重，跳过固定操作项）。

        Returns:
            list[str]: 历史会话标题列表。
        """
        try:
            items = await self._page.evaluate(HISTORY_LIST_SCRIPT)
            return [str(t) for t in (items or []) if t]
        except Exception:  # noqa: BLE001 - 页面未就绪
            return []

    async def open_conversation(self, title: str) -> bool:
        """进入指定标题的历史会话。

        按侧边栏文本匹配（精确 > 前缀 > 包含）并点击；用会话指纹（消息数 +
        当前 URL）校验是否真正切换。

        Args:
            title: 历史会话标题（取自 list_conversations）。

        Returns:
            bool: 是否成功进入。
        """
        try:
            before = str(await self._page.evaluate(FINGERPRINT_SCRIPT) or "")
            ok = bool(await self._page.evaluate(HISTORY_OPEN_SCRIPT, title))
            if not ok:
                return False
            await self._page.wait_for_timeout(1500)
            after = str(await self._page.evaluate(FINGERPRINT_SCRIPT) or "")
            if before == after:
                logger.warning(f"进入历史会话 [{title}] 后指纹未变化，可能未生效")
                return False
            return True
        except Exception:  # noqa: BLE001 - 页面未就绪
            return False

    # ------------------------------------------------------------------
    # 附件上传（Gemini 专属：附件区在 ql-editor 输入区）
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        path: str,
        *,
        max_size_mb: float = 10.0,
        allowed_extensions: str = "png,jpg,jpeg,webp,gif,bmp,md,txt,pdf,doc,docx,xls,xlsx,csv,ppt,pptx,json,py",
        attach_timeout_s: float = 15.0,
    ) -> tuple[bool, str]:
        """上传本地文件到 Gemini 输入区（通过隐藏的 file input）。

        校验扩展名与大小后，经 ``input[type='file']`` 上传，轮询输入区
        （ql-editor 附件区）出现附件预览（图片 img 或文件名文本）确认挂载。

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
            # Gemini 的 file input 是点击"上传和工具"后动态挂载的；先点击确保
            # input[type=file] 存在（无头运行时点击仅用于挂载，不会弹出系统框）
            try:
                upload_btn = page.locator(UPLOAD_BUTTON_SELECTOR).first
                if await upload_btn.count() > 0 and await upload_btn.is_visible():
                    await upload_btn.click(timeout=5000)
                await page.wait_for_timeout(800)
            except Exception:  # noqa: BLE001 - 点击失败不阻塞后续定位
                pass
            file_input = page.locator(FILE_INPUT_SELECTOR).first
            if await file_input.count() == 0:
                return False, "上传失败（未找到网页文件输入）"
            await file_input.set_input_files(path)
            # 上传后关闭可能残留的上传菜单
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception as exc:  # noqa: BLE001 - 未找到文件输入
            logger.warning(f"上传失败，未找到 file input: {path}")
            return False, f"上传失败（未找到网页文件输入）: {exc}"
        # 轮询输入区出现附件预览：Gemini 附件显示为输入区内的缩略图
        # （blob src 或 112x112 小图 img），上传前后数量变化即视为已挂载。
        # 若新会话无历史附件，首传后 img 数从 0 变 1。
        page = self._page
        deadline = asyncio.get_running_loop().time() + attach_timeout_s
        attached = False
        while asyncio.get_running_loop().time() < deadline:
            attached = bool(
                await page.evaluate(
                    """() => {
                        const editor = document.querySelector('div.ql-editor[contenteditable="true"]');
                        if (!editor) return false;
                        // 向上找输入区容器（含附件缩略图）
                        let cur = editor.parentElement;
                        for (let i = 0; i < 10 && cur && cur !== document.body; i++) {
                            const imgs = cur.querySelectorAll('img[src^="blob:"], img[src^="data:"], img[class*="gem-attachment"], img[class*="file"], img[class*="attach"], img[class*="preview"]');
                            if (imgs.length > 0) return true;
                            cur = cur.parentElement;
                        }
                        return false;
                    }"""
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
    # 图片生成（Gemini 原生 Imagen 能力）
    # ------------------------------------------------------------------

    async def _open_make_image(self) -> bool:
        """打开加号菜单并点击"制作图片"按钮（进入图片生成模式）。

        Returns:
            bool: 是否成功进入图片生成模式。
        """
        try:
            # 1. 打开加号（上传和工具）菜单
            await self._page.evaluate(
                """() => {
                    const btns = Array.from(document.querySelectorAll('button[aria-label*="上传和工具"], button[aria-label*="上传"]'));
                    if (btns.length > 0) btns[0].click();
                }"""
            )
            await self._page.wait_for_timeout(1200)
            # 2. 点击"制作图片"
            ok = bool(await self._page.evaluate(CLICK_MAKE_IMAGE_SCRIPT))
            await self._page.wait_for_timeout(1200)
            return ok
        except Exception:  # noqa: BLE001 - 页面未就绪
            return False

    async def wait_image_generated(self, timeout_s: int = 180) -> bool:
        """轮询等待图片生成完成（出现 blob 生成图片或下载按钮）。

        Args:
            timeout_s: 超时秒数。

        Returns:
            bool: 是否生成完成。
        """
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if self._touch_cb is not None:
                try:
                    self._touch_cb()
                except Exception:  # noqa: BLE001
                    pass
            try:
                if bool(await self._page.evaluate(IMAGE_GENERATED_SCRIPT)):
                    await self._page.wait_for_timeout(1500)
                    return True
            except Exception:  # noqa: BLE001 - 页面未就绪
                pass
            await asyncio.sleep(POLL_INTERVAL_S)
        return False

    async def download_generated_image(self, save_dir: str) -> str | None:
        """下载生成图片（点击"下载完整尺寸的图片"按钮，经 Playwright 捕获文件）。

        Args:
            save_dir: 保存目录（自动创建）。

        Returns:
            str | None: 保存的文件路径；失败返回 None。
        """
        page = self._page
        try:
            dl_locator = page.locator(DOWNLOAD_IMAGE_BUTTON).first
            if await dl_locator.count() == 0:
                return None
            save_path = pathlib.Path(save_dir)
            save_path.mkdir(parents=True, exist_ok=True)
            async with page.expect_download(timeout=15000) as dl_info:
                await dl_locator.click(timeout=5000)
            download = await dl_info.value
            target = save_path / (download.suggested_filename or "gemini_image")
            await download.save_as(str(target))
            return str(target) if target.exists() else None
        except Exception as exc:  # noqa: BLE001 - 下载失败
            logger.warning(f"下载 Gemini 生成图片失败: {exc}")
            return None

    async def generate_image(
        self,
        prompt: str,
        save_dir: str,
        timeout_s: int = 180,
        reference_paths: list[str] | None = None,
        upload_allowed_extensions: str = "png,jpg,jpeg,webp,gif,bmp",
    ) -> tuple[bool, str]:
        """生成图片并下载到本地（Gemini 原生 Imagen 能力）。

        完整链路：可选上传参考图（支持多张）→ 打开加号菜单 → 点击"制作图片" →
        输入描述 → 发送 → 等待生成完成 → 点击下载按钮捕获文件。
        传 reference_paths 时先逐张上传参考图，Gemini 会基于这些参考图改图/生成。

        Args:
            prompt: 图片描述（传 reference_paths 时描述修改意图，如"把颜色改成蓝色"）。
            save_dir: 保存目录。
            timeout_s: 等待生成超时秒数。
            reference_paths: 参考图本地路径列表（可空；提供后先全部上传再生成）。
            upload_allowed_extensions: 参考图允许的扩展名（逗号分隔）。

        Returns:
            tuple[bool, str]: (是否成功, 保存路径或错误信息)。
        """
        try:
            # 1. 可选上传参考图（先全部上传再进入制作图片模式，附件会保留）
            for ref_path in reference_paths or []:
                ok, msg = await self.upload_file(
                    ref_path,
                    allowed_extensions=upload_allowed_extensions,
                )
                if not ok:
                    return False, f"上传参考图失败: {msg}"
            # 2. 进入图片生成模式
            if not await self._open_make_image():
                return False, "无法进入 Gemini 图片生成模式（未找到制作图片入口）"
            # 3. 输入描述并发送
            ok, msg = await self.ask(prompt)
            if not ok:
                return False, f"发送图片描述失败: {msg}"
            # 4. 等待生成完成
            if not await self.wait_image_generated(timeout_s=timeout_s):
                return False, "等待 Gemini 生成图片超时"
            # 5. 下载图片
            path = await self.download_generated_image(save_dir)
            if not path:
                return False, "下载 Gemini 生成图片失败"
            return True, path
        except Exception as exc:  # noqa: BLE001 - 生成失败
            return False, f"Gemini 图片生成失败: {exc}"

    # ------------------------------------------------------------------
    # 长截图
    # ------------------------------------------------------------------

    async def _conversation_visible(self) -> bool:
        """判断消息容器当前是否可见（未撑开状态下存在且非隐藏）。"""
        try:
            return bool(await self._page.evaluate(VISIBILITY_SCRIPT, CONVERSATION_SELECTOR))
        except Exception:  # noqa: BLE001 - 页面未就绪
            return False

    async def screenshot(self, region: str = "conversation", think: str = "auto") -> list[str]:
        """截取整页为单张长截图 data URI（含侧边栏，无重复拼接）。

        Gemini 为固定视口布局：``html`` 的 ``overflow:hidden`` 将整页
        ``scrollHeight`` 锁死为视口高度。方案（与 DeepSeek 一致）：
        先把中间滚动链高度赋为完整对话内容高度并放开 html/body overflow，
        使整页高度跟随内容增长，再 ``full_page`` 截出含侧边栏的单张真长图，
        截完还原。若撑开失败（如无内容容器）回退元素级截图。

        Args:
            region: conversation（对话区，默认）/ full（整页，等价对话区）。
            think: 扩展思考开关（auto 不修改；开启/关闭由提问侧 ``set_thinking``
                控制 AI 是否思考）。Gemini 的思考内容无独立折叠 UI，截图不处理展开。

        Returns:
            list[str]: PNG data URI 列表；失败返回空列表。
        """
        page = self._page
        # 截图前自动应用页面主题（auto 按本地时间白天/夜间切换）
        await self.set_theme()
        await page.wait_for_timeout(300)
        # 撑开整页 → full_page 单张真长截图；失败回退元素级分片
        saved = None
        try:
            result = await page.evaluate(GEMINI_FULLPAGE_EXPAND_SCRIPT)
            saved = result.get("saved") if isinstance(result, dict) else None
            await page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001 - 撑开失败
            saved = None
        if saved:
            try:
                shots = await self._fullpage_shots()
            except Exception:  # noqa: BLE001 - 整页截图失败
                shots = []
            if not shots:
                shots = await self._scroll_paged_shots()
        else:
            shots = await self._scroll_paged_shots()
        # 还原撑开样式
        if saved:
            try:
                await page.evaluate(RESTORE_SCRIPT, {"saved": saved})
            except Exception:  # noqa: BLE001 - 还原失败不阻塞
                pass
        # 首张截图顶部拼接浏览器外壳顶栏（标签页/地址栏/头像），与 DeepSeek 一致
        if shots:
            banner = await self._capture_chrome_banner(self._page.viewport_size["width"] if self._page.viewport_size else 1440)
            if banner:
                shots[0] = data_uri(self._prepend_chrome_banner(base64.b64decode(shots[0].split(",", 1)[1]), banner))
        return shots

    async def _fullpage_shots(self) -> list[str]:
        """整页 full_page 截图（撑开后 docH=内容高度）；超高分片兜底。

        Returns:
            list[str]: 单张（或超高分片）PNG data URI 列表；失败返回空列表。
        """
        try:
            height = int(
                await self._page.evaluate("() => document.documentElement.scrollHeight") or 0
            )
            if height <= 0:
                return []
            if height <= self._max_screenshot_height:
                data = await self._page.screenshot(type="png", full_page=True)
                return [data_uri(data)] if data else []
            # 超高分片：按 max_screenshot_height 逐段截取（每段整页宽度含侧边栏）
            doc_width = int(
                await self._page.evaluate("() => document.documentElement.scrollWidth") or 0
            )
            width = doc_width or 1440
            pieces: list[bytes] = []
            offset = 0
            while offset < height:
                piece_h = min(self._max_screenshot_height, height - offset)
                buf = await self._page.screenshot(
                    type="png", full_page=True,
                    clip={"x": 0, "y": offset, "width": width, "height": piece_h},
                )
                pieces.append(buf)
                offset += piece_h
            if not pieces:
                return []
            return [data_uri(p) for p in pieces]
        except Exception as exc:  # noqa: BLE001 - 截图失败
            logger.warning(f"整页截图失败：{exc}")
            return []

    async def _scroll_paged_shots(self) -> list[str]:
        """对对话内容容器滚动分片截图并拼接为长图。

        Gemini 为固定视口布局：``html`` 的 ``overflow:hidden`` 将整页
        ``scrollHeight`` 锁死为视口高度，无法 ``full_page`` 撑开。外层
        ``infinite-scroller`` 是虚拟滚动，其 ``scrollHeight`` 不承载完整内容。
        真实内容完整渲染在 ``.conversation-container``（内容高度 = 完整对话
        高度）。故将该容器临时设为可滚动（``overflow-y:auto`` + 视口高度），
        回滚到顶部后逐段 ``scrollTop`` 递增、对容器做元素截图，最后纵向拼接。

        Returns:
            list[str]: 单张拼接长图 data URI；失败返回空列表。
        """
        try:
            # 1. 把内容容器临时设为可滚动（记录原值供还原）
            setup = await self._page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return null;
                    const saved = {
                        h: el.style.height, oy: el.style.overflowY,
                        maxH: el.style.maxHeight, top: el.scrollTop
                    };
                    el.style.setProperty('overflow-y', 'auto', 'important');
                    el.style.setProperty('height', '900px', 'important');
                    el.style.setProperty('max-height', 'none', 'important');
                    return { saved, scrollH: el.scrollHeight, clientH: el.clientHeight };
                }""",
                CONVERSATION_CONTENT_SELECTOR,
            )
            if not setup:
                logger.warning("截图失败：未找到对话内容容器")
                return []
            saved = setup["saved"]
            # 回滚到顶部
            await self._page.evaluate(
                """(sel) => { const el = document.querySelector(sel); if (el) el.scrollTop = 0; }""",
                CONVERSATION_CONTENT_SELECTOR,
            )
            await self._page.wait_for_timeout(200)
            content_locator = self._page.locator(CONVERSATION_CONTENT_SELECTOR).first
            pieces: list[bytes] = []
            seen: set[int] = set()
            for _ in range(60):
                info = await self._page.evaluate(
                    """(sel) => {
                        const el = document.querySelector(sel);
                        if (!el) return null;
                        return { top: el.scrollTop, max: el.scrollHeight - el.clientHeight };
                    }""",
                    CONVERSATION_CONTENT_SELECTOR,
                )
                if info is None:
                    break
                if info["top"] in seen:
                    break
                seen.add(info["top"])
                buf = await content_locator.screenshot(type="png")
                pieces.append(buf)
                if info["top"] >= (info["max"] or 0) - 5:
                    break
                await self._page.evaluate(
                    """(sel) => { const el = document.querySelector(sel); el.scrollTop += 800; }""",
                    CONVERSATION_CONTENT_SELECTOR,
                )
                await self._page.wait_for_timeout(350)
            if not pieces:
                logger.warning("截图失败：未截取到任何分片")
                return []
            if len(pieces) == 1:
                return [data_uri(pieces[0])]
            return [data_uri(self._vstack_png(pieces))]
        except Exception as exc:  # noqa: BLE001 - 截图失败
            logger.warning(f"截图失败：{exc}")
            return []
        finally:
            # 还原内容容器样式
            try:
                await self._page.evaluate(
                    """(sel, saved) => {
                        const el = document.querySelector(sel);
                        if (!el || !saved) return;
                        el.style.height = saved.h;
                        el.style.overflowY = saved.oy;
                        el.style.maxHeight = saved.maxH;
                        el.scrollTop = saved.top;
                    }""",
                    CONVERSATION_CONTENT_SELECTOR,
                    saved,
                )
            except Exception:  # noqa: BLE001 - 还原失败不阻塞
                pass

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
        import base64 as _b64

        for p in (
            pathlib.Path("data/ai_ui_snapshot_profile/gemini/google_avatar.png"),
            pathlib.Path("data/ai_ui_snapshot_profile/deepseek/google_avatar.png"),
        ):
            if p.is_file():
                try:
                    data = p.read_bytes()
                    if data:
                        return "data:image/png;base64," + _b64.b64encode(data).decode("ascii")
                except Exception:  # noqa: BLE001
                    pass
        return ""

    async def _capture_chrome_banner(self, width: int) -> bytes | None:
        """独立渲染并截取浏览器外壳顶栏横幅（标签页/地址栏/头像）。

        复用 DeepSeek 的浏览器外壳脚本（站点无关）：注入临时
        ``#mofox_chrome_banner`` 节点并独立截图，截完立即销毁。

        Args:
            width: 顶栏宽度（像素），与截图视口宽度一致。

        Returns:
            bytes | None: 截取的 PNG 字节流；失败或未启用时返回 None。
        """
        if not self._decoration_enabled:
            return None
        try:
            # Gemini 页面启用 TrustedHTML（CSP trusted-types），内联 innerHTML 被拒。
            # 先注入默认 trustedTypes policy 允许 HTML 赋值，再渲染横幅。
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
        except Exception as exc:  # noqa: BLE001 - 渲染/截图横幅失败不阻塞
            logger.warning(f"渲染浏览器外壳横幅失败: {exc}")
            return None
        finally:
            try:
                await self._page.evaluate(BROWSER_CHROME_REMOVE_SCRIPT)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _prepend_chrome_banner(piece_bytes: bytes, banner_bytes: bytes) -> bytes:
        """使用 Pillow 将浏览器外壳横幅拼接到首张截图最顶端。

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
            if banner_img.width != piece_img.width:
                scale = piece_img.width / banner_img.width
                resample = Image.Resampling.LANCZOS
                banner_img = banner_img.resize(
                    (piece_img.width, max(1, round(banner_img.height * scale))),
                    resample,
                )
            canvas = Image.new(
                "RGB",
                (piece_img.width, banner_img.height + piece_img.height),
                "white",
            )
            canvas.paste(banner_img, (0, 0))
            canvas.paste(piece_img, (0, banner_img.height))
            buf = io.BytesIO()
            canvas.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:  # noqa: BLE001 - 拼接失败退回首张
            return piece_bytes

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
