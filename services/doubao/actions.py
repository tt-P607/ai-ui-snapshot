"""豆包专属浏览器动作。

组合通用页面操作（:class:`PageActions`）与豆包站点语义（模型档位/历史
会话/长截图/上传），供上层业务（snapshot_service / tools）使用。站点专属
常量与脚本集中在 :mod:`constants`（经四轮 DOM 探测校准），选择器变动
仅需在该处同步。

实测要点：
- 提问走 tiptap 富文本编辑器（click 聚焦 + fill + Enter）
- 模型菜单为 radix 弹层（role=menuitem），需真实鼠标点击
- 历史会话为 ``a`` 标签（href=/chat/<数字ID>，可 goto 直达）
- 联网搜索无独立开关（豆包自动决策），深度思考与"专家"档位合并
"""

from __future__ import annotations

import math
from typing import Any

from src.app.plugin_system.api.log_api import get_logger

from ..base.page_actions import PageActions
from ..base.utils import normalize_theme, resolve_auto_theme
from .constants import (
    ACTIVE_ID_SCRIPT,
    ACTIVE_TITLE_SCRIPT,
    ATTACHMENT_COUNT_SCRIPT,
    ATTACHMENT_DELETE_SCRIPT,
    CAPTCHA_SCRIPT,
    CONVERSATION_SELECTOR,
    CONVERSATION_TEXT_SCRIPT,
    GENERATING_SCRIPT,
    GET_MODEL_SCRIPT,
    GET_THEME_SCRIPT,
    HISTORY_ITEM_HIT_SCRIPT,
    IMAGE_READY_SCRIPT,
    INPUT_SELECTOR,
    NEW_CHAT_TEXT,
    PARAM_CONFIRM_SCRIPT,
    POLL_INTERVAL_S,
    SAFETY_CONFIRM_SCRIPT,
    SEND_BUTTON_SELECTOR,
    SET_THEME_SCRIPT,
    SITE_URL,
    SKILL_ACTIVE_SCRIPT,
    SKILL_IMAGE_BUTTON_ID,
    SUPPORTED_MODELS,
    VIDEO_CARD_SCRIPT,
    VIDEO_CARD_SELECTOR,
    VIDEO_DOWNLOAD_BUTTON_SCRIPT,
    VISIBILITY_SCRIPT,
    normalize_model,
)

logger = get_logger("ai_ui_snapshot.doubao_actions")


class DoubaoActions(PageActions):
    """豆包专属浏览器动作（组合通用页面操作）。

    在 :class:`PageActions` 通用能力之上，封装豆包站点语义动作：模型档位
    读写、历史会话进入（goto 直达）、长截图（分片）与附件上传。站点常量
    见 :mod:`constants`。
    """

    conversation_selector: str = CONVERSATION_SELECTOR
    visibility_script: str = VISIBILITY_SCRIPT
    generating_script: str = GENERATING_SCRIPT
    poll_interval_s: float = POLL_INTERVAL_S
    conversation_text_script: str = CONVERSATION_TEXT_SCRIPT
    active_title_script: str = ACTIVE_TITLE_SCRIPT
    active_id_script: str = ACTIVE_ID_SCRIPT
    blocker_script: str = CAPTCHA_SCRIPT

    async def set_theme(self, theme: str | None = None) -> str:
        """设置豆包页面主题（写 localStorage ``dbx-web-theme`` + reload）。

        Args:
            theme: 目标主题（auto/light/dark）；None 视为 auto。

        Returns:
            str: 实际应用的主题（light/dark）。
        """
        target = normalize_theme(theme) if theme is not None else "auto"
        resolved = resolve_auto_theme() if target == "auto" else target
        try:
            await self._page.evaluate(SET_THEME_SCRIPT, resolved)
            await self._page.reload(wait_until="domcontentloaded")
            await self._page.wait_for_timeout(4000)
        except Exception:  # noqa: BLE001 - 页面未就绪
            pass
        return resolved

    async def get_theme(self) -> str:
        """读取当前豆包主题偏好。

        Returns:
            str: light/dark。
        """
        try:
            return str(await self._page.evaluate(GET_THEME_SCRIPT) or "light")
        except Exception:  # noqa: BLE001 - 页面未就绪
            return "light"

    async def get_model(self) -> str:
        """读取当前模型档位（快速/专家）。

        Returns:
            str: 快速 / 专家；无法识别时返回空字符串。
        """
        try:
            return str(await self._page.evaluate(GET_MODEL_SCRIPT) or "")
        except Exception:  # noqa: BLE001 - 页面未就绪
            return ""

    async def set_model(self, model: str) -> tuple[bool, str]:
        """切换模型档位（快速/专家）。

        流程：定位输入区"豆包 快速/专家"档位按钮 → 点击展开 radix 菜单 →
        点击目标 ``role=menuitem`` 选项 → 校验按钮文本已切换。

        Args:
            model: 目标档位（快速/专家，支持深度思考等变体归一化）。

        Returns:
            tuple[bool, str]: (是否成功, 当前档位或错误信息)。
        """
        normalized = normalize_model(model)
        if normalized is None:
            return False, f"不支持的模型档位: {model}（可选: {'/'.join(SUPPORTED_MODELS)}）"
        page = self._page
        try:
            current = await self.get_model()
            if current == normalized:
                return True, f"已是 {normalized} 档位"
            # 1. 点击输入区模型档位按钮（展开 radix 菜单）
            trigger = page.locator("button").filter(has_text="豆包").filter(has_text=current or "快速").last
            if await trigger.count() == 0:
                # 兜底：任意含"豆包"的档位按钮
                trigger = page.locator("button").filter(has_text="豆包").last
            if await trigger.count() == 0:
                return False, "未找到模型档位按钮"
            await trigger.click(timeout=5000)
            await page.wait_for_timeout(800)
            # 2. 点击菜单目标项（快速档项文本为"豆包 快速"，专家档为"豆包 2.1 Turbo 专家"）
            want_text = "专家" if normalized == "专家" else "快速"
            option = page.locator('[role="menuitem"]').filter(has_text=want_text).first
            if await option.count() > 0:
                await option.click(timeout=5000)
            else:
                await self.click(want_text)
            await page.wait_for_timeout(1000)
            # 3. 校验
            after = await self.get_model()
            if after == normalized:
                return True, f"已切换为 {normalized} 档位"
            return False, f"切换失败，当前仍为: {after or '未知'}"
        except Exception as exc:  # noqa: BLE001 - 切换失败
            return False, f"切换模型档位失败: {exc}"

    async def new_chat(self) -> bool:
        """开启一个新对话（侧栏"新对话"入口，未命中时 goto 首页直达）。

        Returns:
            bool: 是否成功。
        """
        try:
            loc = self._page.get_by_text(NEW_CHAT_TEXT, exact=False)
            count = await loc.count()
            for i in range(count):
                item = loc.nth(i)
                try:
                    if await item.is_visible():
                        await item.click(timeout=5000)
                        await self._page.wait_for_timeout(1500)
                        return True
                except Exception:  # noqa: BLE001 - 尝试下一个
                    continue
            # 兜底：直接访问 /chat/ 首页即新对话
            await self._page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
            await self._page.wait_for_timeout(2500)
            return True
        except Exception:  # noqa: BLE001 - 新建失败
            return False

    async def list_conversations(self) -> list[str]:
        """列出侧栏历史会话标题。

        Returns:
            list[str]: 历史会话标题列表（侧栏当前可见项）。
        """
        try:
            items = await self._page.evaluate(
                """() => Array.from(document.querySelectorAll('a[class*="conversation-item"]'))
                    .filter(a => /\\/chat\\/\\d+/.test(a.getAttribute('href') || ''))
                    .map(a => (a.innerText || '').split('\\n')[0].trim())
                    .filter(t => t)"""
            )
            return [str(t) for t in (items or [])]
        except Exception:  # noqa: BLE001 - 页面未就绪
            return []

    async def open_conversation(self, title: str) -> bool:
        """进入指定标题的历史会话。

        成功判据只有一条：侧边栏里能按标题找到并点开该项。点击后轮询确认
        切换是否生效，未确认只记日志、不影响返回——页面未及时更新 URL/标题
        不代表没进去，据此报错会让上层放弃截图/提问，是更糟的结果。

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
            # 2. 侧边栏定位该项（返回会话 ID 与精确 href）
            hit = await page.evaluate(HISTORY_ITEM_HIT_SCRIPT, want)
            info = hit if isinstance(hit, dict) else {}
            target_href = str(info.get("href") or "").strip()
            if not target_href:
                return False
            # 3. 按精确 href 真实点击（Playwright 会把项滚动到可视区再点）
            await page.locator(f'a[class*="conversation-item"][href="{target_href}"]').first.click(
                timeout=5000
            )
            # 4. 等待切换生效（仅日志，不阻断）
            target_id = str(info.get("id") or "").strip()
            if not await self._wait_session_switch(target_id=target_id, target_title=want):
                logger.info(f"进入历史会话 [{want}] 后未观测到切换，按当前页面继续")
            return True
        except Exception:  # noqa: BLE001 - 进入失败
            return False

    async def count_attachments(self) -> int:
        """统计输入区已挂载的附件数量（跨模式通用）。

        Returns:
            int: 附件数量（普通对话/生图的 image-wrapper 卡片与生视频的
            thumb-card 参考图卡片取较大值）；页面未就绪时返回 0。
        """
        try:
            return int(await self._page.evaluate(ATTACHMENT_COUNT_SCRIPT) or 0)
        except Exception:  # noqa: BLE001 - 页面未就绪
            return 0

    async def clear_attachments(self, max_rounds: int = 12) -> int:
        """清空输入区已挂载的附件，返回清除数量。

        豆包切换"新对话"时会保留输入区草稿（实测：新对话后已挂载的参考图
        仍在），上一轮失败任务（传完附件但未发出）的残留附件会跟着下一条
        消息一起发出，故上传前/提问前需先清空。

        Args:
            max_rounds: 最大删除轮数（防异常时死循环）。

        Returns:
            int: 实际清除的附件数量。
        """
        page = self._page
        removed = 0
        for _ in range(max_rounds):
            before = await self.count_attachments()
            if before <= 0:
                break
            try:
                clicked = bool(await page.evaluate(ATTACHMENT_DELETE_SCRIPT))
            except Exception:  # noqa: BLE001 - 页面未就绪
                break
            await page.wait_for_timeout(500)
            after = await self.count_attachments()
            if after >= before and not clicked:
                break
            removed += max(before - after, 0)
        return removed

    async def upload_files(
        self,
        paths: list[str],
        *,
        max_size_mb: float = 20.0,
        allowed_extensions: str = "pdf,txt,csv,doc,docx,xls,xlsx,ppt,pptx,md,mobi,epub,png,jpeg,jpg,webp",
        attach_timeout_s: float = 25.0,
    ) -> tuple[bool, str]:
        """上传一批本地文件到豆包（隐藏 ``input[type=file]`` 一次性注入）。

        豆包的隐藏 file input 带 ``multiple``，单次 ``set_input_files``
        注入多个路径即形成一条多附件消息（聊天多图提问、图生图/图生视频的
        参考图都走此路径）。真实边界（probe7/8 实测 2026-08）：仅接受图片与
        文档，单文件 ≤20MB；音频/视频注入会弹 ``semi-toast-error`` 被拒绝。
        挂载信号按模式归一：普通对话/生图为 ``image-wrapper`` 卡片，生视频为
        ``thumb-card`` 参考图卡片（两者与上传数 1:1），文档按文件名文本判断；
        超量/超限文件会被站点静默丢弃，故按数量校验挂载结果如实上报。
        注入前会清空输入区残留草稿（豆包切新对话不清理草稿，实测）。

        Args:
            paths: 本地文件路径列表（至少一个）。
            max_size_mb: 允许的单文件最大大小（MB，豆包上限 20）。
            allowed_extensions: 允许的扩展名（逗号分隔，小写）。
            attach_timeout_s: 等待附件挂载的超时秒数。

        Returns:
            tuple[bool, str]: (是否成功, 说明或错误信息)。
        """
        page = self._page
        import pathlib as _pl

        targets = [str(p) for p in paths if str(p).strip()]
        if not targets:
            return False, "未提供要上传的文件"
        allowed = {e.strip().lower().lstrip(".") for e in allowed_extensions.split(",") if e.strip()}
        image_exts = {"png", "jpeg", "jpg", "webp", "gif", "bmp"}
        image_names: list[str] = []
        doc_names: list[str] = []
        for path in targets:
            file_path = _pl.Path(path)
            suffix = file_path.suffix.lower().lstrip(".")
            if allowed and suffix not in allowed:
                return False, (
                    f"文件类型 .{suffix} 不在豆包允许范围（{', '.join(sorted(allowed))}；"
                    "豆包不支持音频/视频）"
                )
            try:
                size_mb = file_path.stat().st_size / (1024 * 1024)
            except OSError:
                return False, f"无法读取文件: {path}"
            if size_mb > max_size_mb:
                return False, (
                    f"文件 {file_path.name} 大小 {size_mb:.1f}MB 超过限制 "
                    f"{max_size_mb:g}MB（豆包单文件上限）"
                )
            if suffix in image_exts:
                image_names.append(file_path.stem.lower())
            else:
                doc_names.append(file_path.stem.lower())

        # 清空残留草稿：否则上一轮失败任务遗留的附件会跟随本轮一起发出
        stale = await self.count_attachments()
        if stale > 0:
            removed = await self.clear_attachments()
            logger.warning(f"输入区存在 {stale} 个残留附件，已清除 {removed} 个")

        try:
            file_input = page.locator('input[type="file"]').first
            if await file_input.count() == 0:
                return False, "上传失败（未找到豆包文件输入）"
            await file_input.set_input_files(targets)
        except Exception as exc:  # noqa: BLE001 - 注入失败
            return False, f"上传失败: {exc}"
        # 轮询挂载结果：图片按卡片计数（image-wrapper / thumb-card），
        # 文档按文件名文本命中计数
        want_images = len(image_names)
        deadline = _deadline(attach_timeout_s)
        mounted_images = 0
        mounted_docs = 0
        while _now() < deadline:
            mounted_images = await self.count_attachments()
            try:
                mounted_docs = int(
                    await page.evaluate(
                        """(names) => names.filter(n => Array.from(
                                document.querySelectorAll('span, div'))
                            .some(el => el.children.length === 0
                                && (el.innerText || '').toLowerCase().includes(n))).length""",
                        doc_names,
                    )
                    or 0
                )
            except Exception:  # noqa: BLE001 - 页面未就绪
                mounted_docs = 0
            if mounted_images >= want_images and mounted_docs >= len(doc_names):
                await page.wait_for_timeout(800)
                summary = "、".join(_pl.Path(p).name for p in targets)
                return True, f"已上传 {len(targets)} 个附件: {summary}"
            try:
                error_text = str(
                    await page.evaluate(
                        """() => {
                            const t = document.querySelector(
                                '.semi-toast-error .semi-toast-content-text');
                            return t ? (t.innerText || '').trim() : '';
                        }"""
                    )
                    or ""
                )
            except Exception:  # noqa: BLE001 - 页面未就绪
                error_text = ""
            if error_text:
                return False, f"豆包拒绍上传: {error_text}"
            await page.wait_for_timeout(700)
        ok_count = mounted_images + mounted_docs
        if ok_count == 0:
            return False, "上传失败（未检测到附件挂载，可能被豆包拒绍）"
        return False, (
            f"仅 {ok_count}/{len(targets)} 个附件挂载成功（豆包对单条消息附件数量有限制，"
            "或部分文件被拒绍），请减少附件数量后重试"
        )

    async def ask(self, question: str) -> tuple[bool, str]:
        """向豆包输入框填入问题并发送。

        tiptap 富文本编辑器需先点击聚焦再 fill；Enter 发送，未生效时点发送
        按钮兜底。带参考图/附件时豆包会先弹"安全确认"授权弹窗拦住发送，
        此处会自动点"确认"并重试发送（仅命中授权文案时）。发送成功后输入框
        会被清空，以此校验发送结果——仅凭"未抛异常"判定会把静默失败当成
        成功，导致后续流程空等。

        Args:
            question: 要发送的问题文本。

        Returns:
            tuple[bool, str]: (是否成功, 说明或错误信息)。
        """
        page = self._page
        try:
            editor = page.locator(INPUT_SELECTOR).first
            if await editor.count() == 0:
                return False, "未找到豆包输入框（可能未登录或页面未就绪）"
            await editor.click(timeout=5000)
            await page.wait_for_timeout(200)
            await editor.fill(question)
            await page.wait_for_timeout(300)
            # 发送：Enter → （带附件时）安全确认弹窗 → 发送按钮兜底，最多三轮
            for _ in range(3):
                await page.keyboard.press("Enter")
                if await self._await_editor_cleared(editor):
                    return True, "已发送"
                if await self.confirm_safety_dialog():
                    # 弹窗只拦发送，确认后需重新触发发送
                    await page.wait_for_timeout(1200)
                    continue
                send_btn = page.locator(SEND_BUTTON_SELECTOR).first
                if await send_btn.count() > 0:
                    try:
                        await send_btn.click(timeout=5000)
                    except Exception:  # noqa: BLE001 - 弹窗拦截等，下一轮重试
                        continue
                    if await self._await_editor_cleared(editor):
                        return True, "已发送"
                    if await self.confirm_safety_dialog():
                        await page.wait_for_timeout(1200)
                        continue
                break
            return False, "消息未发出（输入框未清空，可能被弹窗拦截）"
        except Exception as exc:  # noqa: BLE001 - 发送失败
            return False, f"向豆包发送问题失败: {exc}"

    async def confirm_safety_dialog(self) -> bool:
        """点击"安全确认"授权弹窗的确认按钮（非该弹窗时不动作）。

        带参考图/附件的生成任务在发送时豆包弹出素材授权确认，不确认则
        消息发不出去（表现为发送按钮被遮罩拦截）。

        Returns:
            bool: 是否点击了确认。
        """
        try:
            return bool(await self._page.evaluate(SAFETY_CONFIRM_SCRIPT))
        except Exception:  # noqa: BLE001 - 页面未就绪
            return False

    async def _await_editor_cleared(self, editor: Any, timeout_s: float = 4.0) -> bool:
        """等待输入框被清空（豆包发送成功后清空输入框）。

        Args:
            editor: 输入框 locator。
            timeout_s: 等待超时秒数。

        Returns:
            bool: 输入框是否已清空。
        """
        import asyncio as _aio

        deadline = _aio.get_running_loop().time() + timeout_s
        while _aio.get_running_loop().time() < deadline:
            try:
                if not (await editor.inner_text() or "").strip():
                    return True
            except Exception:  # noqa: BLE001 - 节点重建中
                pass
            await _aio.sleep(0.4)
        return False

    async def screenshot(
        self,
        region: str = "conversation",
        scope: str = "viewport",
        rounds: int = 1,
    ) -> list[str]:
        """截取豆包对话区为 data URI 列表（超长分片）。

        豆包为虚拟列表（仅渲染可见行）：
        - scope="viewport"（默认）：截取当前人类可读的正常视口窗口（比例自然，聚焦最新回复）；
        - scope="rounds"：从后往前倒序完整截取最近 rounds 个回复及提问；
        - scope="full"：整页长截图。

        Args:
            region: conversation（默认）/ full。
            scope: 截图范围模式（viewport / rounds / full）。
            rounds: 当 scope="rounds" 时指定的回复轮数（默认 1）。

        Returns:
            list[str]: PNG data URI 列表；失败返回空列表。
        """
        if scope == "rounds" or rounds > 1:
            return await self._rounds_shot(rounds=rounds)
        if scope == "full":
            return await self._tall_viewport_shot()
        # 默认正常视口比例截图（滚动至最新回复）
        try:
            await self._page.evaluate(
                """() => {
                    const rows = document.querySelectorAll('[class*="v_list_row"]');
                    if (rows.length) {
                        rows[rows.length - 1].scrollIntoView({ block: 'end' });
                    }
                }"""
            )
            await self._page.wait_for_timeout(300)
        except Exception:  # noqa: BLE001
            pass
        return await self._viewport_shot()

    async def _tall_viewport_shot(self) -> list[str]:
        """用超高虚拟视口一次性渲染并截取完整豆包页面。

        豆包的对话与历史栏都是随视口高度伸展的虚拟列表。临时增高
        Playwright 视口后，左右列表会在同一个页面布局中同步展开，固定的
        输入区和账号区也会自然落在页面底部，无需滚动采片或图像拼接。

        Returns:
            list[str]: 单张 PNG data URI 列表。
        """
        marker = "data-ai-ui-snapshot-scroll"
        original_viewport = self._page.viewport_size
        original_tops = {"conversation": 0, "sidebar": 0}
        try:
            if not original_viewport:
                return await self._viewport_shot()
            info = await self._page.evaluate(
                """(attr) => {
                    document.querySelectorAll(`[${attr}]`).forEach(el => el.removeAttribute(attr));
                    const candidates = Array.from(
                        document.querySelectorAll('[class*="v_list_scroller"]')
                    ).filter(el => {
                        const rect = el.getBoundingClientRect();
                        return el.clientHeight >= 200
                            && rect.width >= Math.max(480, window.innerWidth * 0.45)
                            && el.querySelector('[class*="v_list_row"]');
                    });
                    if (!candidates.length) return null;
                    const score = (el) => {
                        const messages = el.querySelectorAll(
                            '[class*="md-box-root"], [class*="send-msg-bubble"], [class*="v_list_row"]'
                        ).length;
                        return messages * 1000000 + el.scrollHeight - el.clientHeight;
                    };
                    const target = candidates.sort((a, b) => score(b) - score(a))[0];
                    target.setAttribute(attr, 'conversation');
                    const rect = target.getBoundingClientRect();
                    const sidebar = Array.from(document.querySelectorAll('*'))
                        .filter(el => {
                            const style = getComputedStyle(el);
                            const candidateRect = el.getBoundingClientRect();
                            return ['auto', 'scroll'].includes(style.overflowY)
                                && el.scrollHeight > el.clientHeight + 20
                                && el.clientHeight >= 200
                                && candidateRect.width >= 180
                                && candidateRect.right <= rect.left + 4;
                        })
                        .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0];
                    if (sidebar) sidebar.setAttribute(attr, 'sidebar');
                    const sidebarRect = sidebar ? sidebar.getBoundingClientRect() : null;
                    return {
                        contentTop: Math.max(0, rect.top),
                        contentHeight: target.scrollHeight,
                        footerHeight: Math.max(0, window.innerHeight - rect.bottom),
                        conversationTop: target.scrollTop,
                        sidebarTop: sidebar && sidebarRect ? sidebar.scrollTop : 0,
                    };
                }""",
                marker,
            )
            if not info:
                logger.warning("未找到豆包对话滚动容器，回退视窗截图")
                return await self._viewport_shot()

            original_tops = {
                "conversation": int(info["conversationTop"]),
                "sidebar": int(info["sidebarTop"]),
            }
            target_height = math.ceil(
                float(info["contentTop"])
                + float(info["contentHeight"])
                + float(info["footerHeight"])
            )
            target_height = min(
                self._max_screenshot_height,
                max(int(original_viewport["height"]), target_height),
            )
            await self._page.set_viewport_size(
                {"width": int(original_viewport["width"]), "height": target_height}
            )
            await self._page.evaluate(
                """(attr) => {
                    const conversation = document.querySelector(`[${attr}="conversation"]`);
                    const sidebar = document.querySelector(`[${attr}="sidebar"]`);
                    if (conversation) conversation.scrollTop = 0;
                    if (sidebar) sidebar.scrollTop = 0;
                }""",
                marker,
            )
            await self._page.wait_for_timeout(800)
            return await self._viewport_shot()
        except Exception as exc:  # noqa: BLE001 - 站点 DOM 变化时回退
            logger.warning(f"豆包超高视口长截图失败，回退视窗截图: {exc}")
            return await self._viewport_shot()
        finally:
            try:
                if original_viewport:
                    await self._page.set_viewport_size(original_viewport)
                    await self._page.wait_for_timeout(100)
                await self._page.evaluate(
                    """([attr, tops]) => {
                        const conversation = document.querySelector(`[${attr}="conversation"]`);
                        const sidebar = document.querySelector(`[${attr}="sidebar"]`);
                        if (conversation) conversation.scrollTop = tops.conversation;
                        if (sidebar) sidebar.scrollTop = tops.sidebar;
                        document.querySelectorAll(`[${attr}]`).forEach(el => el.removeAttribute(attr));
                    }""",
                    [marker, original_tops],
                )
            except Exception:  # noqa: BLE001 - 页面关闭时无需恢复
                pass

    async def extract_chat_images(self) -> list[str]:
        """探测当前最新一条 AI 回复中是否存在模型生成的图片，返回直链列表。

        Returns:
            list[str]: 图片 CDN 直链列表；无生成图片时返回空列表。
        """
        try:
            urls = await self._page.evaluate(
                """() => {
                    const realSrc = (im) => im.currentSrc || im.src || '';
                    const rows = Array.from(document.querySelectorAll('[class*="v_list_row"]'));
                    const tailRows = rows.slice(-3);
                    const imgs = [];
                    tailRows.forEach(r => {
                        Array.from(r.querySelectorAll('img')).forEach(im => {
                            const src = realSrc(im);
                            if (im.complete && /^https?:/.test(src) && (src.includes('rc_gen_image') || im.naturalWidth >= 300)) {
                                imgs.push(src);
                            }
                        });
                    });
                    return Array.from(new Set(imgs));
                }"""
            )
            return [str(u) for u in (urls or []) if str(u).startswith("http")]
        except Exception:  # noqa: BLE001
            return []

    # ------------------------------------------------------------------
    # 技能：图像生成 / 视频生成（probe5 实测）
    # ------------------------------------------------------------------

    async def ensure_logged_in(self, timeout_s: float = 20.0) -> bool:
        """校验当前页面登录态（防在登出态继续自动化加深风控）。

        豆包未登录页面仍渲染输入区（不能只看输入框），需"无精确'登录'
        按钮 + 正面文案"双条件判定；未登录时调用方应立即停止操作并提示
        重新登录（2026-08-22 掉登录事故教训）。

        Args:
            timeout_s: 等待页面就绪的超时秒数。

        Returns:
            bool: 是否已登录。
        """
        import asyncio as _aio

        from .constants import (
            LOGIN_BUTTON_TEXT,
            READY_CHECK_SCRIPT,
            READY_INPUT_SELECTOR,
            READY_TEXT_MARKERS,
        )

        args = [READY_INPUT_SELECTOR, list(READY_TEXT_MARKERS), LOGIN_BUTTON_TEXT]
        deadline = _aio.get_running_loop().time() + timeout_s
        while _aio.get_running_loop().time() < deadline:
            try:
                if bool(await self._page.evaluate(READY_CHECK_SCRIPT, args)):
                    return True
            except Exception:  # noqa: BLE001 - 页面未就绪
                pass
            await _aio.sleep(2.0)
        return False

    async def activate_skill(self, skill_id: str, placeholder: str) -> tuple[bool, str]:
        """激活输入区技能（点击技能按钮并确认受理）。

        点击底部技能栏按钮（data-skill-id 稳定定位），受理确认信号为
        输入框 placeholder 变为技能专属文案。

        Args:
            skill_id: 技能按钮 data-skill-id（constants 中定义）。
            placeholder: 受理后的 placeholder 文案（constants 中定义）。

        Returns:
            tuple[bool, str]: (是否激活, 说明)。
        """
        page = self._page
        try:
            btn = page.locator(f'button[data-skill-id="{skill_id}"]').first
            if await btn.count() == 0:
                # 兜底：data-skill-id 未命中时按文本定位（按钮文本=技能名）
                text = "图像生成" if skill_id == SKILL_IMAGE_BUTTON_ID else "视频生成"
                btn = page.locator("button").filter(has_text=text).last
            if await btn.count() == 0:
                return False, f"未找到技能按钮 {skill_id}"
            await btn.click(timeout=5000)
            # 确认受理（placeholder 变化）
            for _ in range(8):
                if bool(await page.evaluate(SKILL_ACTIVE_SCRIPT, placeholder)):
                    return True, "技能已激活"
                await page.wait_for_timeout(400)
            return False, "技能点击后未检测到受理信号（placeholder 未变化）"
        except Exception as exc:  # noqa: BLE001 - 激活失败
            return False, f"激活技能失败: {exc}"

    async def submit_prompt(self, prompt: str) -> tuple[bool, str]:
        """向已激活技能的输入框提交描述（与 ask 相同的 tiptap 发送）。

        Args:
            prompt: 描述文本。

        Returns:
            tuple[bool, str]: (是否发送, 说明)。
        """
        return await self.ask(prompt)

    async def set_video_params(
        self,
        model: str = "",
        aspect_ratio: str = "",
        duration: str = "",
    ) -> tuple[bool, str]:
        """设置视频生成的原生 UI 参数（模型、画面比例、时长）。

        直接操控豆包底栏原生控件：
        - 仅限免费模型（Seedance 2.0 Fast / Seedance 2.0 Mini；付费模型需 VIP 不向模型暴露）；
        - 比例通过下拉菜单点击选中；
        - 时长通过 Radix Slider 滑块键控（4s/10s/15s）。

        Args:
            model: 视频生成模型名（Seedance 2.0 Fast / Seedance 2.0 Mini）。
            aspect_ratio: 比例（自动/16:9/9:16/3:4/4:3/1:1/21:9）。
            duration: 时长（4s/10s/15s）。

        Returns:
            tuple[bool, str]: (是否成功, 说明)。
        """
        page = self._page
        try:
            # 1. 切换模型
            if model:
                model_btn = page.locator('button:has-text("Seedance")').first
                if await model_btn.count() > 0:
                    curr_txt = await model_btn.inner_text()
                    if model not in curr_txt:
                        await model_btn.click()
                        await page.wait_for_timeout(500)
                        opt = page.locator(
                            '[role="menu"] [role="menuitem"], [data-slot="dropdown-menu-content"] [role="menuitem"], [data-slot="dropdown-menu-content"] *'
                        ).filter(has_text=model).first
                        if await opt.count() > 0:
                            await opt.click()
                            await page.wait_for_timeout(600)
                            # 确认切换弹窗（如有）
                            confirm = page.locator('button:has-text("确认切换")')
                            if await confirm.count() > 0 and await confirm.is_visible():
                                await confirm.click()
                                await page.wait_for_timeout(500)
                            # VIP 订阅拦截（如有）
                            vip_close = page.locator('[role="dialog"] svg path[d*="19.4801"]').first
                            if await vip_close.count() > 0 and await vip_close.is_visible():
                                await vip_close.click()
                                await page.wait_for_timeout(500)
                                return False, f"模型 {model} 需要 VIP 订阅，已自动取消"

            # 2. 切换比例与时长
            if aspect_ratio or duration:
                btn = page.locator('button:has-text("·")').first
                if await btn.count() == 0:
                    btn = page.locator('button:has-text("s")').first
                if await btn.count() > 0:
                    await btn.click()
                    await page.wait_for_timeout(500)
                    if aspect_ratio:
                        r_btn = page.locator(
                            '[data-slot="dropdown-menu-content"] button'
                        ).filter(has_text=aspect_ratio).first
                        if await r_btn.count() > 0:
                            await r_btn.click()
                            await page.wait_for_timeout(400)
                    if duration:
                        slider = page.locator('[role="slider"]').first
                        if await slider.count() > 0:
                            await slider.focus()
                            if duration == "4s":
                                await page.keyboard.press("Home")
                            elif duration == "15s":
                                await page.keyboard.press("End")
                            else:  # 默认或 10s
                                await page.keyboard.press("Home")
                                for _ in range(6):
                                    await page.keyboard.press("ArrowRight")
                            await page.wait_for_timeout(300)
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(400)
            return True, "视频原生参数已配置"
        except Exception as exc:  # noqa: BLE001 - 设置失败
            return False, f"设置视频参数失败: {exc}"

    async def set_image_params(
        self,
        model: str = "",
        aspect_ratio: str = "",
        style: str = "",
    ) -> tuple[bool, str]:
        """设置生图的原生 UI 参数（模型、画面比例、风格）。

        直接操控豆包底栏原生控件：
        - 仅限免费模型（Seedream 4.5 / Seedream 4.0；付费模型需 VIP 不向模型暴露）；
        - 比例通过原生下拉菜单按钮精确点击；
        - 风格通过 32 种官方原生风格菜单精确点击。

        Args:
            model: 生图模型名（Seedream 4.5 / Seedream 4.0）。
            aspect_ratio: 比例（自动/1:1/16:9/9:16/3:4/4:3/2:3/3:2）。
            style: 风格（如 动漫/电影写真/水彩画 等 32 种，或'自动'）。

        Returns:
            tuple[bool, str]: (是否成功, 说明)。
        """
        page = self._page
        try:
            # 1. 切换模型
            if model:
                model_btn = page.locator('button:has-text("Seedream")').first
                if await model_btn.count() > 0:
                    curr_txt = await model_btn.inner_text()
                    if model not in curr_txt:
                        await model_btn.click()
                        await page.wait_for_timeout(500)
                        opt = page.locator(
                            '[role="menu"] [role="menuitem"], [data-slot="dropdown-menu-content"] [role="menuitem"], [data-slot="dropdown-menu-content"] *'
                        ).filter(has_text=model).first
                        if await opt.count() > 0:
                            await opt.click()
                            await page.wait_for_timeout(600)
                            confirm = page.locator('button:has-text("确认切换")')
                            if await confirm.count() > 0 and await confirm.is_visible():
                                await confirm.click()
                                await page.wait_for_timeout(500)

            # 2. 切换比例
            if aspect_ratio:
                ratio_btn = page.locator('button:has-text("比例")').first
                if await ratio_btn.count() > 0:
                    await ratio_btn.click()
                    await page.wait_for_timeout(500)
                    r_btn = page.locator(
                        '[data-slot="dropdown-menu-content"] button'
                    ).filter(has_text=aspect_ratio).first
                    if await r_btn.count() > 0:
                        await r_btn.click()
                        await page.wait_for_timeout(400)
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(400)

            # 3. 切换风格
            if style:
                style_btn = page.locator('button:has-text("风格")').first
                if await style_btn.count() > 0:
                    await style_btn.click()
                    await page.wait_for_timeout(500)
                    s_btn = page.locator(
                        '[data-slot="dropdown-menu-content"] button, [data-slot="dropdown-menu-content"] *'
                    ).filter(has_text=style).first
                    if await s_btn.count() > 0:
                        await s_btn.click()
                        await page.wait_for_timeout(400)
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(400)
            return True, "图像原生参数已配置"
        except Exception as exc:  # noqa: BLE001 - 设置失败
            return False, f"设置图像参数失败: {exc}"

    async def await_param_confirm(
        self,
        wait_s: float = 15.0,
        auto_confirm: bool = True,
        confirm_text: str = "",
    ) -> tuple[bool, str, str]:
        """等待并处理豆包的生成参数卡片。

        豆包在生图/生视频前可能先用自然语言列出参数（模型/时长/比例/创作方向）并问
        "确认后我再开始生成"，该提示是普通对话消息（无可点按钮），回复确认或调整
        指令后才会真正开始生成。未出现该提示时（描述已足够明确）直接进入生成。

        Args:
            wait_s: 等待参数卡片出现的最长秒数。
            auto_confirm: 是否自动确认（默认 True）。
            confirm_text: 若确认，自定义回复的确认或调整指令（空则回复"确认"）。

        Returns:
            tuple[bool, str, str]: (是否可继续生成, 状态说明, 豆包提出的参数建议文本)。
        """
        import asyncio as _aio

        deadline = _aio.get_running_loop().time() + wait_s
        while _aio.get_running_loop().time() < deadline:
            if await self.check_blocker():
                # 弹了人机验证：确认无法继续，立即中断
                return False, "触发了人机验证，无法继续", ""
            try:
                # 等 AI 把参数写完再确认（生成中发送会被当成打断）
                generating = bool(await self._page.evaluate(GENERATING_SCRIPT))
                if not generating and bool(await self._page.evaluate(PARAM_CONFIRM_SCRIPT)):
                    proposal = str(await self._page.evaluate(CONVERSATION_TEXT_SCRIPT, "last") or "").strip()
                    if not auto_confirm:
                        return False, "检测到豆包参数调整建议，等待确认", proposal
                    send_msg = (confirm_text or "").strip() or "确认"
                    ok, msg = await self.ask(send_msg)
                    if not ok:
                        return False, f"发送确认失败: {msg}", proposal
                    return True, f"已确认生成参数（{send_msg}）", proposal
            except Exception:  # noqa: BLE001 - 页面未就绪
                pass
            await _aio.sleep(2.5)
        return True, "无需确认（描述已足够明确）", ""

    async def wait_image_ready(self, timeout_s: int = 150) -> list[str]:
        """等待生成图片完成，返回全部候选大图的 http 直链列表。

        轮询对话区出现完成加载的生成图（一次生成多张候选，缩略/大图
        按 src 主干去重），返回大图 CDN 直链列表（按面积降序），超时
        返回空列表。

        Args:
            timeout_s: 超时秒数。

        Returns:
            list[str]: 图片 http 直链列表；超时/失败返回空列表。
        """
        import asyncio as _aio

        # 两阶段等待：①出现生成图（缩略图先加载）；②大图稳定——最大宽度
        # ≥1000（大图 2048，缩略 384）且（数量, 最大宽）连续 3 轮不变。
        # 大图 lazy 加载慢于缩略图，首命中即返回会只抓到缩略图（实测坑）。
        deadline = _aio.get_running_loop().time() + timeout_s
        last_sig: tuple[int, int] | None = None
        stable = 0
        while _aio.get_running_loop().time() < deadline:
            if self._touch_cb is not None:
                try:
                    self._touch_cb()
                except Exception:  # noqa: BLE001 - 保活失败不阻塞
                    pass
            if await self.check_blocker():
                # 弹了人机验证：生成无法继续，立即中断（避免空等超时）
                return []
            try:
                result = await self._page.evaluate(IMAGE_READY_SCRIPT)
                urls = [str(u) for u in (result or []) if str(u).startswith("http")]
                if urls:
                    # 读取当前最大宽度作大图就绪信号
                    max_w = int(
                        await self._page.evaluate(
                            """(urls) => {
                                const imgs = Array.from(document.querySelectorAll('img'));
                                let m = 0;
                                for (const im of imgs) {
                                    if (urls.includes(im.src) && im.complete) {
                                        m = Math.max(m, im.naturalWidth);
                                    }
                                }
                                return m;
                            }""",
                            urls,
                        )
                    )
                    sig = (len(urls), max_w)
                    stable = stable + 1 if sig == last_sig else 0
                    last_sig = sig
                    if max_w >= 1000 and stable >= 3:
                        return urls
            except Exception:  # noqa: BLE001 - 页面未就绪
                pass
            await _aio.sleep(2.5)
        # 超时兜底：只要有过命中就返回当前结果（哪怕只是缩略图）
        if last_sig is not None:
            try:
                result = await self._page.evaluate(IMAGE_READY_SCRIPT)
                urls = [str(u) for u in (result or []) if str(u).startswith("http")]
                if urls:
                    return urls
            except Exception:  # noqa: BLE001
                pass
        return []

    async def wait_video_done(self, timeout_s: int = 600) -> tuple[bool, dict]:
        """等待生成视频完成，返回完成标记与源信息。

        完成信号是**视频卡片**（``block-video`` + 封面图加载完成），不是
        ``<video>``：豆包默认只渲染卡片与封面，``<video>`` 需鼠标悬停才挂载。
        命中卡片后主动 hover 一次以挂载播放器，从而取到真实视频地址。
        长任务：轮询期间持续保活（touch_cb），供后台挂起任务复用。

        Args:
            timeout_s: 超时秒数（视频生成约 2 分钟，默认 600s）。

        Returns:
            tuple[bool, dict]: (是否完成, {src: 源URL或空, duration: 秒})。
        """
        import asyncio as _aio

        deadline = _aio.get_running_loop().time() + timeout_s
        while _aio.get_running_loop().time() < deadline:
            if self._touch_cb is not None:
                try:
                    self._touch_cb()
                except Exception:  # noqa: BLE001 - 保活失败不阻塞
                    pass
            if await self.check_blocker():
                # 弹了人机验证：生成无法继续，立即中断（避免空等超时）
                return False, {}
            try:
                card = await self._page.evaluate(VIDEO_CARD_SCRIPT)
            except Exception:  # noqa: BLE001 - 页面未就绪
                card = {}
            if isinstance(card, dict) and card.get("found") and card.get("coverReady"):
                # 卡片已出：hover 挂载 <video> 以拿到真实地址（失败不阻塞完成判定）
                info = await self._mount_video_card()
                return True, info
            await _aio.sleep(5.0)
        return False, {}

    async def _mount_video_card(self) -> dict:
        """悬停视频卡片以挂载 ``<video>``，返回 {src, duration}。

        卡片默认只有封面图，鼠标悬停后才挂载播放器与操作栏；本方法负责
        触发挂载并读回真实视频地址，供下载与时长展示使用。

        Returns:
            dict: {src: 视频地址或空字符串, duration: 秒}。
        """
        page = self._page
        try:
            card = page.locator(VIDEO_CARD_SELECTOR).last
            if await card.count() > 0:
                await card.scroll_into_view_if_needed()
                await card.hover(timeout=5000)
                await page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001 - 悬停失败不影响完成判定
            pass
        for _ in range(6):
            try:
                state = await page.evaluate(VIDEO_CARD_SCRIPT)
            except Exception:  # noqa: BLE001 - 页面未就绪
                state = {}
            if isinstance(state, dict) and state.get("mounted") and state.get("src"):
                return {"src": str(state["src"]), "duration": int(state.get("duration") or 0)}
            await page.wait_for_timeout(1000)
        return {"src": "", "duration": 0}

    async def download_generated_video(self, save_dir: str, timeout_s: int = 90) -> str | None:
        """下载已完成的生成视频到本地。

        实现：先 hover 视频卡片（悬停才挂载播放器与操作栏）→ 探测下载图标
        并按其中心坐标点击（图标无 aria/文本，只能按坐标点）→ ``expect_download``
        捕获保存；探测或点击失败时退回直接 fetch 视频地址（该兑底实测可靠）。

        Args:
            save_dir: 保存目录（自动创建）。
            timeout_s: 等待下载完成的超时秒数。

        Returns:
            str | None: 保存的本地文件路径；失败返回 None。
        """
        import pathlib as _pl

        page = self._page
        out_dir = _pl.Path(save_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            # 1. hover 卡片使播放器与操作栏挂载/出现
            card = page.locator(VIDEO_CARD_SELECTOR).last
            if await card.count() > 0:
                await card.scroll_into_view_if_needed()
                await card.hover(timeout=5000)
                await page.wait_for_timeout(1200)
            # 2. 探测下载图标（无文本，按结构定位后取坐标）
            info = await page.evaluate(VIDEO_DOWNLOAD_BUTTON_SCRIPT)
            if not info:
                logger.info("未探测到视频下载图标，改用直接 fetch 视频地址")
                return await self._fetch_video_src(out_dir)
            # 3. 按坐标点击图标并捕获下载（点击后下载应立即开始，故等待窗口取小值，
            #    未触发时快速转 fetch 兜底，不让调用方干等满 timeout_s）
            async with page.expect_download(timeout=min(timeout_s, 25) * 1000) as dl_info:
                try:
                    await page.mouse.click(int(info["x"]), int(info["y"]))
                except Exception:  # noqa: BLE001 - 点击失败转 fetch
                    return await self._fetch_video_src(out_dir)
            download = await dl_info.value
            dest = out_dir / f"doubao_video_{_now():.0f}.mp4"
            await download.save_as(str(dest))
            return str(dest)
        except Exception as exc:  # noqa: BLE001 - 下载失败
            logger.warning(f"视频下载失败: {exc}")
            # 4. 最终兜底：fetch src
            try:
                return await self._fetch_video_src(out_dir)
            except Exception:  # noqa: BLE001
                return None

    async def _fetch_video_src(self, out_dir) -> str | None:  # noqa: ANN001 - pathlib.Path
        """直接 fetch video 元素 src 保存（下载按钮不可用时的兜底）。

        Args:
            out_dir: 保存目录。

        Returns:
            str | None: 保存的文件路径；失败返回 None。
        """
        import urllib.request

        # 视频地址只在卡片被 hover 挂载后才存在，先确保已挂载
        info = await self._mount_video_card()
        src = str(info.get("src") or "")
        if not src:
            return None
        if not src.startswith("http"):
            # blob: URL 无法站外 fetch，走页面内 fetch 转 base64
            data_b64 = await self._page.evaluate(
                """async (url) => {
                    try {
                        const resp = await fetch(url);
                        const buf = await resp.arrayBuffer();
                        let binary = '';
                        const bytes = new Uint8Array(buf);
                        const chunk = 0x8000;
                        for (let i = 0; i < bytes.length; i += chunk) {
                            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                        }
                        return btoa(binary);
                    } catch (e) { return ''; }
                }""",
                src,
            )
            if not data_b64:
                return None
            import base64

            dest = out_dir / f"doubao_video_{_now():.0f}.mp4"
            dest.write_bytes(base64.b64decode(data_b64))
            return str(dest)
        # http 直链：页面上下文 fetch（带站点 Cookie/签名）后经 JS 取 bytes
        data_b64 = await self._page.evaluate(
            """async (url) => {
                try {
                    const resp = await fetch(url);
                    const buf = await resp.arrayBuffer();
                    let binary = '';
                    const bytes = new Uint8Array(buf);
                    const chunk = 0x8000;
                    for (let i = 0; i < bytes.length; i += chunk) {
                        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                    }
                    return btoa(binary);
                } catch (e) { return ''; }
            }""",
            src,
        )
        if not data_b64:
            # 最后兜底：Python 侧直下（CDN 签名 URL 一般可直接访问）
            try:
                dest = out_dir / f"doubao_video_{_now():.0f}.mp4"
                urllib.request.urlretrieve(src, dest)  # noqa: S310
                return str(dest)
            except Exception:  # noqa: BLE001
                return None
        import base64

        dest2 = out_dir / f"doubao_video_{_now():.0f}.mp4"
        dest2.write_bytes(base64.b64decode(data_b64))
        return str(dest2)

    async def download_generated_images(self, image_urls: list[str], save_dir: str) -> list[str]:
        """批量下载生成的图片（CDN 直链）到本地。

        经页面上下文 fetch（带站点签名参数）转 base64 落盘，避免 Python
        侧直连被 CDN 拒绝；单张失败跳过不影响其余。

        Args:
            image_urls: 图片 http 直链列表（wait_image_ready 返回）。
            save_dir: 保存目录（自动创建）。

        Returns:
            list[str]: 成功保存的本地文件路径列表（可能少于入参）。
        """
        import base64
        import pathlib as _pl

        out_dir = _pl.Path(save_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        stamp = _now()
        for idx, url in enumerate(image_urls):
            try:
                data_b64 = await self._page.evaluate(
                    """async (url) => {
                        try {
                            const resp = await fetch(url);
                            const buf = await resp.arrayBuffer();
                            let binary = '';
                            const bytes = new Uint8Array(buf);
                            const chunk = 0x8000;
                            for (let i = 0; i < bytes.length; i += chunk) {
                                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                            }
                            return btoa(binary);
                        } catch (e) { return ''; }
                    }""",
                    url,
                )
                if not data_b64:
                    continue
                # 从 URL 猜扩展名（默认 png）
                ext = "png"
                for cand in (".png", ".jpg", ".jpeg", ".webp"):
                    if cand in url.lower():
                        ext = cand.lstrip(".")
                        break
                dest = out_dir / f"doubao_image_{stamp:.0f}_{idx + 1}.{ext}"
                dest.write_bytes(base64.b64decode(data_b64))
                saved.append(str(dest))
            except Exception as exc:  # noqa: BLE001 - 单张失败跳过
                logger.warning(f"图片 {idx + 1} 下载失败: {exc}")
        return saved


def _now() -> float:
    """当前事件循环时间（秒）。"""
    import asyncio

    return asyncio.get_running_loop().time()


def _deadline(timeout_s: float) -> float:
    """相对当前时间 timeout_s 秒后的时限。"""
    return _now() + timeout_s
