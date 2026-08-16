"""Gemini (Google AI) 专属浏览器动作。

组合通用页面操作（:class:`PageActions`）与 Gemini 站点语义（模型切换、提问、
等待回复、长截图、附件上传），供上层业务（snapshot_service）使用。站点专属
常量与脚本集中在 :mod:`constants`，选择器变动仅需在该处同步。
"""

from __future__ import annotations

import asyncio
import base64
import pathlib
from typing import Any

from src.app.plugin_system.api.log_api import get_logger

from ..base.page_actions import PageActions
from ..base.utils import data_uri, normalize_theme, resolve_auto_theme
from .constants import (
    ACTIVE_CONVERSATION_ID_SCRIPT,
    ACTIVE_CONVERSATION_TITLE_SCRIPT,
    CLICK_MAKE_IMAGE_SCRIPT,
    CLICK_SHARE_ITEM_SCRIPT,
    CLICK_SHARE_MENU_SCRIPT,
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


class GeminiActions(PageActions):
    """Gemini 专属浏览器动作（组合通用页面操作）。

    在 :class:`PageActions` 通用能力之上，封装 Gemini 站点语义动作：模型读写
    与切换、提问与等待回复、长截图（分片）、附件上传。站点常量见
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
        super().__init__(
            page,
            touch_cb=touch_cb,
            max_screenshot_height=max_screenshot_height,
            decoration_enabled=decoration_enabled,
            decoration_theme=normalize_theme(decoration_theme),
            decoration_avatar_url=decoration_avatar_url,
        )
        self._theme = normalize_theme(theme)

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
        target = normalize_theme(theme) if theme is not None else self._theme
        resolved = resolve_auto_theme() if target == "auto" else target
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

    async def try_download_generated_image(
        self, save_dir: str, wait_s: int = 30
    ) -> str | None:
        """短轮询检测对话中是否生成了图片，有则下载（供 ask_gemini 用）。

        ask_gemini 提问后 Gemini 可能在对话中直接出图（非纯文本回复），
        此方法在回复完成后短暂轮询生成图信号，命中即下载。检测不到不报错，
        返回 None，保证纯文本提问不受影响。

        Args:
            save_dir: 保存目录（自动创建）。
            wait_s: 等待生成图出现的最大秒数。

        Returns:
            str | None: 保存的本地路径；未生成图或下载失败返回 None。
        """
        deadline = asyncio.get_running_loop().time() + wait_s
        while asyncio.get_running_loop().time() < deadline:
            try:
                if bool(await self._page.evaluate(IMAGE_GENERATED_SCRIPT)):
                    path = await self.download_generated_image(save_dir)
                    return path
            except Exception:  # noqa: BLE001 - 检测失败不阻塞
                pass
            await asyncio.sleep(POLL_INTERVAL_S)
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
        """对对话滚动容器滚动分片截图并拼接为长图（整页撑开的兜底）。

        Gemini 为固定视口布局：``html`` 的 ``overflow:hidden`` 将整页
        ``scrollHeight`` 锁死为视口高度。对话内容完整渲染在
        ``.conversation-container``，实际滚动发生在最近的祖先滚动容器
        （``infinite-scroller.chat-history``，虚拟滚动层）。故取该滚动容器，
        回滚到顶部后逐段 ``scrollTop`` 递增、对容器做元素截图，最后纵向拼接。

        Returns:
            list[str]: 单张拼接长图 data URI；失败返回空列表。
        """
        saved: dict[str, Any] | None = None
        try:
            # 1. 定位对话滚动容器：.conversation-container 的最近可滚动祖先，
            #    找不到回退 .conversation-container 自身
            setup = await self._page.evaluate(
                """() => {
                    const cc = document.querySelector('.conversation-container');
                    if (!cc) return null;
                    let el = cc;
                    while (el) {
                        const cs = getComputedStyle(el);
                        const ov = cs.overflowY || cs.overflow || '';
                        if (ov.includes('auto') || ov.includes('scroll')) break;
                        el = el.parentElement;
                    }
                    if (!el) el = cc;
                    const saved = {
                        h: el.style.height, oy: el.style.overflowY,
                        maxH: el.style.maxHeight, top: el.scrollTop
                    };
                    return { saved, scrollH: el.scrollHeight, clientH: el.clientHeight };
                }"""
            )
            if not setup:
                logger.warning("截图失败：未找到对话内容容器")
                return []
            saved = setup["saved"]
            # 2. 给滚动容器打临时标记（Python 侧截图定位），并回滚到顶部
            marked = await self._page.evaluate(
                """() => {
                    let el = document.querySelector('.conversation-container');
                    while (el) {
                        const cs = getComputedStyle(el);
                        const ov = cs.overflowY || cs.overflow || '';
                        if (ov.includes('auto') || ov.includes('scroll')) break;
                        el = el.parentElement;
                    }
                    if (!el) return false;
                    el.setAttribute('data-mofox-scroll', '1');
                    el.scrollTop = 0;
                    return true;
                }"""
            )
            if not marked:
                logger.warning("截图失败：未定位到对话滚动容器")
                return []
            await self._page.wait_for_timeout(200)
            content_locator = self._page.locator("[data-mofox-scroll]").first
            pieces: list[bytes] = []
            seen: set[int] = set()
            for _ in range(60):
                info = await self._page.evaluate(
                    """() => {
                        const el = document.querySelector('[data-mofox-scroll]');
                        if (!el) return null;
                        return { top: el.scrollTop, max: el.scrollHeight - el.clientHeight };
                    }"""
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
                    """() => {
                        const el = document.querySelector('[data-mofox-scroll]');
                        if (el) el.scrollTop += 800;
                    }"""
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
            # 还原滚动容器样式并清除临时标记
            try:
                await self._page.evaluate(
                    """(saved) => {
                        const el = document.querySelector('[data-mofox-scroll]');
                        if (!el || !saved) return;
                        el.removeAttribute('data-mofox-scroll');
                        el.style.height = saved.h;
                        el.style.overflowY = saved.oy;
                        el.style.maxHeight = saved.maxH;
                        el.scrollTop = saved.top;
                    }""",
                    saved,
                )
            except Exception:  # noqa: BLE001 - 还原失败不阻塞
                pass


