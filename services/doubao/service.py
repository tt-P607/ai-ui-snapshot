"""豆包站点的提问、截图及媒体生成服务。"""

from __future__ import annotations

import asyncio
from typing import Any

from src.app.plugin_system.api.log_api import get_logger

from ..base.browser_session import get_manager
from ..base.shared import AskResult, _locate_conversation, _upload_notice_many
from .actions import DoubaoActions

logger = get_logger("ai_ui_snapshot.service")

DOUBAO_ALLOWED_EXTENSIONS = (
    "pdf,txt,csv,doc,docx,xls,xlsx,ppt,pptx,md,mobi,epub,png,jpeg,jpg,webp"
)
DOUBAO_UPLOAD_MAX_MB = 20.0
DOUBAO_IMAGE_SAVE_DIR = "data/ai_ui_snapshot_profile/doubao/images"
DOUBAO_VIDEO_SAVE_DIR = "data/ai_ui_snapshot_profile/doubao/videos"
_doubao_media_lock = asyncio.Lock()


def _release_media_lock() -> None:
    """释放豆包媒体互斥锁（仅在确实持有时释放）。

    重复释放会抛 ``RuntimeError`` 并顶掉函数真实返回值（表现为工具报
    "Lock is not acquired"，掩盖真正的失败原因），故所有释放路径统一经
    本函数；锁未被持有时记录告警而非抛错。
    """
    if not _doubao_media_lock.locked():
        logger.warning("豆包媒体锁未被持有，跳过释放（调用方未取得锁）")
        return
    _doubao_media_lock.release()


async def _open_doubao_media_page():
    """在豆包共享浏览器中打开媒体生成专用页面。

    Returns:
        tuple: (浏览器会话管理器, page)；启动失败抛异常。
    """
    manager = get_manager()
    page = await manager.open_page("doubao")
    return manager, page


async def ask_doubao(
    question: str,
    *,
    stream_id: str = "",
    timeout_s: int = 240,
    model: str = "",
    new_chat: bool = False,
    conversation: str = "",
    local_paths: list[str] | None = None,
    output_format: str = "auto",
    return_scope: str = "last",
    scope: str = "viewport",
    rounds: int = 1,
    multimodal: bool = False,
    upload_max_size_mb: float = DOUBAO_UPLOAD_MAX_MB,
    upload_allowed_extensions: str = DOUBAO_ALLOWED_EXTENSIONS,
) -> AskResult:
    """统一入口：向豆包真实提问，按输出形式返回结果。

    完整链路：获取（或创建）豆包浏览器会话（theme=doubao）→（带附件时
    先取媒体锁防同 profile 双开）→（可选）设置模型档位 → conversation
    路由（空沿用/标题进入/新建）→（可选）批量上传附件 → tiptap 编辑器
    提问 → 等待回复 → 按 output_format 处理 → release 解锁。豆包无独立
    联网开关（模型自动决策），专家档即深度思考。

    附件与对话延续：多张图片作为一条消息一次注入（形成多图提问），
    且与纯文本提问共用同一浏览器会话，因此 conversation 路由完全生效
    （空沿用当前对话可连续追问，不会每张图都另开新对话）。

    Args:
        question: 用户问题。
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        timeout_s: 等待 AI 回复超时秒数。
        model: 模型档位（快速/专家；空沿用当前档位）。
        new_chat: 是否先开新对话再提问（等价 conversation="__new__"）。
        conversation: 对话定位：空沿用当前；精确标题进入（未命中新建）；
            "__new__" 强制新建。
        local_paths: 已解析好的上传文件路径列表（None/空表示不上传；
            多个文件作为一条消息的多附件）。
        output_format: 输出形式（auto 纯文本 / snapshot 截图）。
        return_scope: 信息返回范围（last 最新回复 / full 整段对话）。
        upload_max_size_mb: 上传文件大小上限（MB）。
        upload_allowed_extensions: 允许上传的扩展名（逗号分隔，小写）。

    Returns:
        AskResult: 结构化结果（成功时 ok=True，含对应输出字段）。
    """
    if not question or not question.strip():
        return AskResult(ok=False, error="问题不能为空")

    attachments = [str(p) for p in (local_paths or []) if str(p).strip()]
    return await _ask_doubao_in_shared_session(
        question.strip(),
        stream_id=stream_id,
        timeout_s=timeout_s,
        model=model,
        new_chat=new_chat,
        conversation=conversation,
        local_paths=attachments,
        output_format=output_format,
        return_scope=return_scope,
        scope=scope,
        rounds=rounds,
        multimodal=multimodal,
        upload_max_size_mb=upload_max_size_mb,
        upload_allowed_extensions=upload_allowed_extensions,
    )


async def _ask_doubao_in_shared_session(
    question: str,
    *,
    stream_id: str = "",
    timeout_s: int = 240,
    model: str = "",
    new_chat: bool = False,
    conversation: str = "",
    local_paths: list[str] | None = None,
    output_format: str = "auto",
    return_scope: str = "last",
    scope: str = "viewport",
    rounds: int = 1,
    multimodal: bool = False,
    upload_max_size_mb: float = DOUBAO_UPLOAD_MAX_MB,
    upload_allowed_extensions: str = DOUBAO_ALLOWED_EXTENSIONS,
) -> AskResult:
    """共享会话内完成一次豆包提问（含可选多附件上传）。

    Args:
        question: 用户问题。
        stream_id: 聊天流 ID。
        timeout_s: 等待回复超时秒数。
        model: 模型档位（空沿用当前）。
        new_chat: 是否先开新对话。
        conversation: 对话定位（空沿用 / 精确标题 / __new__）。
        local_paths: 附件本地路径列表（空表示不上传）。
        output_format: 输出形式（auto/snapshot）。
        return_scope: 信息返回范围（last/full）。
        upload_max_size_mb: 上传大小上限（MB）。
        upload_allowed_extensions: 允许的扩展名。

    Returns:
        AskResult: 结构化结果。
    """
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key, theme="doubao")
        session.hold()
        manager.touch(stream_key, theme="doubao")
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = DoubaoActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key, theme="doubao"),
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )

        # 0. 登录预检：豆包共享会话（无头）登录态可能被服务端吊销，
        # 未登录时立即报错（绝不硬用登出态操作，防加深风控）
        if not await actions.ensure_logged_in():
            return AskResult(
                ok=False,
                error="豆包登录态失效：请运行 scripts/login_doubao.py 重新登录后重试",
            )

        # 1. 设置模型档位（空沿用当前）
        want_model = (model or "").strip()
        if want_model:
            ok, msg = await actions.set_model(want_model)
            if not ok:
                return AskResult(ok=False, error=f"设置豆包档位失败: {msg}")

        # 2. conversation 路由：未显式指定对话标题时默认新建；显式传精确标题进入继续
        err = await _locate_conversation(
            actions,
            conversation,
            new_chat=new_chat,
            new_chat_on_miss=True,
            default_new=True,
            new_page_cb=(
                (lambda: manager.new_session_page(stream_key, theme="doubao"))
                if session.active_conversation
                else None
            ),
        )
        if err:
            return AskResult(ok=False, error=err)

        # 3. 上传附件（可选，多附件一次注入为同一条消息）
        upload_notice = ""
        if local_paths:
            ok, msg = await actions.upload_files(
                local_paths,
                max_size_mb=upload_max_size_mb,
                allowed_extensions=upload_allowed_extensions,
            )
            if not ok:
                return AskResult(ok=False, error=msg)
            upload_notice = _upload_notice_many(local_paths)
        else:
            # 无附件提问：清理输入区残留草稿（上一轮上传成功但未发出的附件
            # 会跟随本轮问题一起发出，豆包切新对话不清理草稿）
            stale = await actions.count_attachments()
            if stale > 0:
                removed = await actions.clear_attachments()
                logger.warning(f"提问前清理输入区残留附件 {removed}/{stale} 个")

        # 4. 提问并等待回复
        try:
            previous_reply = await actions.get_conversation_text(scope="last")
            ok, msg = await actions.ask(question)
            if not ok:
                return AskResult(ok=False, error=msg)
            done, last_reply = await actions.wait_reply_done(
                timeout_s=timeout_s,
                previous_reply=previous_reply,
            )
        except Exception as exc:  # noqa: BLE001 - 网页提问失败
            logger.error(f"豆包网页提问失败: {exc}", exc_info=True)
            return AskResult(ok=False, error=f"豆包网页提问失败: {exc}")
        if not done:
            if actions.last_blocker:
                return AskResult(
                    ok=False,
                    error=(
                        f"豆包触发了人机验证（{actions.last_blocker}），自动化无法完成。"
                        "请手动在浏览器中完成验证后重试。"
                    ),
                )
            return AskResult(ok=False, error="等待豆包回复超时")

        # 5. 按 return_scope 取信息返回文本
        if return_scope == "full":
            content = await actions.get_conversation_text(scope="full")
        else:
            content = last_reply

        # 5.5 探测聊天中是否生成了图片（如插画、生图等），有则自动下载落盘并按配置处理视觉感知
        saved_images: list[str] = []
        image_descs: list[str] = []
        images_b64: list[str] = []
        try:
            chat_img_urls = await actions.extract_chat_images()
            if chat_img_urls:
                saved_images = await actions.download_generated_images(
                    chat_img_urls, DOUBAO_IMAGE_SAVE_DIR
                )
                if saved_images:
                    logger.info(f"在豆包对话中检测并自动下载了 {len(saved_images)} 张生成图片")
                    import base64
                    from src.app.plugin_system.api import media_api
                    total_imgs = len(saved_images)
                    for idx, img_p in enumerate(saved_images, 1):
                        try:
                            with open(img_p, "rb") as f:  # noqa: PTH123
                                raw_b64 = base64.b64encode(f.read()).decode("utf-8")
                            if multimodal:
                                # 开关开启：多模态直传模式，直接返回 Base64 供多模态模型看图
                                images_b64.append(raw_b64)
                            else:
                                # 开关默认关闭：走框架内置 VLM 提炼简短画面文字描述，对纯文本模型安全且省 Token
                                desc = await media_api.recognize_media(raw_b64, "image")
                                if desc:
                                    prefix = f"【图{idx}】" if total_imgs > 1 else ""
                                    image_descs.append(f"{prefix}{desc.strip()}")
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(f"处理图片感知失败 {img_p}: {exc}")
        except Exception as exc:  # noqa: BLE001 - 提取生图失败不阻断对话
            logger.warning(f"检测或下载对话生图失败: {exc}")

        # 6. 读取当前活跃会话 ID 与标题并同步到会话
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.wait_conversation_title()
        session.set_active_conversation(current_id, current_title)

        # 7. 仅 snapshot 输出形式截图
        if output_format == "snapshot":
            data_uris = await actions.screenshot("conversation", scope=scope, rounds=rounds)
            if not data_uris:
                return AskResult(ok=False, error="对话区截图失败", reply=content,
                                 conversation=current_title)
            meta = await actions.get_snapshot_meta(scope=scope, rounds=rounds)
            return AskResult(
                ok=True,
                reply=content,
                data_uri=data_uris,
                conversation=current_title,
                model_name="doubao.com",
                upload=upload_notice,
                images=saved_images,
                image_descriptions=image_descs,
                images_base64=images_b64,
                snapshot_meta=meta,
            )
        return AskResult(
            ok=True,
            reply=content,
            conversation=current_title,
            model_name="doubao.com",
            upload=upload_notice,
            images=saved_images,
            image_descriptions=image_descs,
            images_base64=images_b64,
        )
    finally:
        session.release()


async def capture_doubao_snapshot(
    *,
    stream_id: str = "",
    conversation: str = "",
    scope: str = "viewport",
    rounds: int = 1,
) -> AskResult:
    """直接截取当前/指定豆包对话界面，不提问、不改设置。

    定位会话（空=沿用当前 / 精确标题=进入该历史会话）后直接截图；
    与 DeepSeek 的 :func:`capture_snapshot` 对应，供豆包侧
    ``doubao_snapshot`` 工具调用。

    Args:
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        conversation: 对话定位：空（默认）沿用当前对话；精确标题进入
            该历史会话（未命中报错）。
        scope: 截图范围模式（viewport 正常视窗 / rounds 倒序轮次 / full 整页长图）。
        rounds: 截取回复轮数（scope=rounds 时生效）。

    Returns:
        AskResult: 成功时 ok=True 且 data_uri 含截图；失败时 ok=False。
    """
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key, theme="doubao")
        session.hold()
        manager.touch(stream_key, theme="doubao")
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = DoubaoActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key, theme="doubao"),
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )
        # 定位会话：空=沿用当前 / 精确标题=进入 / __new__=新建
        # （未命中报错：无可截取内容，不截空会话）
        err = await _locate_conversation(actions, conversation)
        if err:
            return AskResult(ok=False, error=err)
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.get_active_conversation_title()
        session.set_active_conversation(current_id, current_title)
        data_uris = await actions.screenshot(
            "conversation", scope=scope, rounds=rounds
        )
        if not data_uris:
            return AskResult(ok=False, error="对话区截图失败", conversation=current_title)
        meta = await actions.get_snapshot_meta(scope=scope, rounds=rounds)
        return AskResult(
            ok=True,
            data_uri=data_uris,
            conversation=current_title,
            model_name="doubao.com",
            snapshot_meta=meta,
        )
    finally:
        session.release()


async def download_doubao_chat_images(
    *,
    stream_id: str = "",
    conversation: str = "",
    save_dir: str = DOUBAO_IMAGE_SAVE_DIR,
) -> AskResult:
    """下载当前或指定豆包对话末尾回复中的生成图片。

    Args:
        stream_id: 聊天流 ID。
        conversation: 对话标题；为空使用当前页面。
        save_dir: 图片保存目录。

    Returns:
        AskResult: 成功时 images 包含全部本地文件路径。
    """
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key, theme="doubao")
        session.hold()
        manager.touch(stream_key, theme="doubao")
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = DoubaoActions(
            session.page,
            touch_cb=lambda: manager.touch(stream_key, theme="doubao"),
        )
        err = await _locate_conversation(actions, conversation)
        if err:
            return AskResult(ok=False, error=err)
        image_urls = await actions.extract_chat_images()
        if not image_urls:
            return AskResult(ok=False, error="当前对话末尾未找到豆包生成图片")
        paths = await actions.download_generated_images(image_urls, save_dir)
        if not paths:
            return AskResult(ok=False, error="找到豆包生成图片，但下载失败")
        current_title = await actions.get_active_conversation_title()
        return AskResult(
            ok=True,
            conversation=current_title,
            image_path=paths[0],
            images=paths,
            model_name="doubao.com",
        )
    finally:
        session.release()


async def generate_doubao_image(
    prompt: str,
    *,
    model: str = "",
    aspect_ratio: str = "",
    style: str = "",
    auto_confirm: bool = True,
    confirm_text: str = "",
    local_paths: list[str] | None = None,
    stream_id: str = "",
    timeout_s: int = 150,
    save_dir: str = DOUBAO_IMAGE_SAVE_DIR,
) -> tuple[bool, list[str], str, str]:
    """用豆包"图像生成"技能生成图片并保存到本地（同步等待完成）。

    豆包一次生成多张候选（通常 4 张），全部下载返回。流程：共享浏览器的
    独立页面 → 新对话 → 激活技能 → （可选）上传
    参考图（图生图/改图，可多张）→ 设置原生 UI 参数（模型/比例/风格）→
    提交描述 → 处理参数确认/建议 → 等 CDN 大图（全量候选）→ 批量 fetch 落盘 → 关闭。

    Args:
        prompt: 图片描述。
        model: 生图模型（仅限免费模型 Seedream 4.5 / Seedream 4.0）。
        aspect_ratio: 画面比例（自动/1:1/16:9/9:16/3:4/4:3/2:3/3:2）。
        style: 风格（自动/动漫/电影写真 等 32 种原生风格）。
        auto_confirm: 是否自动确认豆包给出的参数调整建议。
        confirm_text: 自定义确认/调整文案（空且 auto_confirm=True 则默认发"确认"）。
        local_paths: 参考图本地路径列表（图生图/改图，可多张；空表示纯文生图）。
        stream_id: 聊天流 ID（日志与保活标识）。
        timeout_s: 等待生成超时秒数（生图通常 <90s）。
        save_dir: 生成图片保存目录（自动创建）。

    Returns:
        tuple[bool, list[str], str, str]: (是否成功, 本地路径列表, 错误信息, 参数建议文本)。
    """
    from .actions import DoubaoActions
    from .constants import (
        DEFAULT_IMAGE_MODEL,
        DEFAULT_IMAGE_RATIO,
        DEFAULT_IMAGE_STYLE,
        SKILL_IMAGE_BUTTON_ID,
        SKILL_IMAGE_PLACEHOLDER,
        SITE_URL,
    )

    want_model = (model or "").strip() or DEFAULT_IMAGE_MODEL
    want_ratio = (aspect_ratio or "").strip() or DEFAULT_IMAGE_RATIO
    want_style = (style or "").strip() or DEFAULT_IMAGE_STYLE

    async with _doubao_media_lock:
        try:
            manager, page = await _open_doubao_media_page()
        except Exception as exc:  # noqa: BLE001 - 启动失败
            logger.error(f"打开豆包媒体页面失败: {exc}", exc_info=True)
            return False, [], f"打开豆包媒体页面失败: {exc}", ""
        try:
            await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            actions = DoubaoActions(page)
            # 登录预检：未登录绝不继续自动化（防加深风控）
            if not await actions.ensure_logged_in():
                return False, [], "豆包登录态失效：请运行 scripts/login_doubao.py 重新登录后重试", ""
            if not await actions.new_chat():
                return False, [], "新建豆包对话失败", ""
            await page.wait_for_timeout(2000)
            ok, msg = await actions.activate_skill(SKILL_IMAGE_BUTTON_ID, SKILL_IMAGE_PLACEHOLDER)
            if not ok:
                return False, [], f"激活图像生成技能失败: {msg}", ""

            # 参考图（图生图/改图）：技能受理后注入图片（可多张）
            ref_paths = [str(x) for x in (local_paths or []) if str(x).strip()]
            if ref_paths:
                ok, msg = await actions.upload_files(
                    ref_paths,
                    max_size_mb=DOUBAO_UPLOAD_MAX_MB,
                    allowed_extensions=DOUBAO_ALLOWED_EXTENSIONS,
                )
                if not ok:
                    return False, [], f"上传参考图失败: {msg}", ""
                await page.wait_for_timeout(1500)

            # 设置原生 UI 参数
            param_ok, param_msg = await actions.set_image_params(
                model=want_model,
                aspect_ratio=want_ratio,
                style=want_style,
            )
            if not param_ok:
                logger.warning(f"设置生图原生参数提示: {param_msg}")

            ok, msg = await actions.submit_prompt(prompt.strip())
            if not ok:
                return False, [], msg, ""
            # 豆包可能先返回参数确认消息（需回复"确认"才开始生成）
            confirm_ok, confirm_msg, proposal = await actions.await_param_confirm(
                auto_confirm=auto_confirm,
                confirm_text=confirm_text,
            )
            if not confirm_ok:
                if actions.last_blocker:
                    return False, [], (
                        f"豆包触发了人机验证（{actions.last_blocker}），自动化无法完成。"
                        "请手动在浏览器中完成验证后重试。"
                    ), ""
                if not auto_confirm and proposal:
                    return True, [], "", proposal
                return False, [], confirm_msg, proposal

            image_urls = await actions.wait_image_ready(timeout_s=timeout_s)
            if not image_urls:
                if actions.last_blocker:
                    return False, [], (
                        f"豆包触发了人机验证（{actions.last_blocker}），自动化无法完成。"
                        "请手动在浏览器中完成验证后重试（必要时降低调用频率）。"
                    ), ""
                page_text = (await actions.read_text(max_chars=400)) or ""
                return False, [], (
                    "等待图片生成超时（页面状态: "
                    f"{page_text[-200:]}）。若页面无生成动作，可能触发了豆包生成频控，"
                    "请间隔几分钟后再试。"
                ), ""
            paths = await actions.download_generated_images(image_urls, save_dir)
            if not paths:
                return False, [], "图片已生成但下载失败", ""
            return True, paths, "", proposal
        finally:
            await manager.close_page(page)


async def submit_doubao_video(
    prompt: str,
    *,
    model: str = "",
    aspect_ratio: str = "",
    duration: str = "",
    auto_confirm: bool = True,
    confirm_text: str = "",
    local_paths: list[str] | None = None,
    stream_id: str = "",
) -> tuple[bool, str, str]:
    """提交豆包"视频生成"任务并转后台等待（立即返回，不阻塞）。

    流程：独立浏览器（媒体生成需独占 media profile）→ 新对话 → 激活视频
    生成技能 → 设置原生 UI 参数（模型/比例/时长）→ （可选）上传参考图
    （图生视频，可多张）→ 提交描述 → 处理参数建议卡片 → 启动后台守护任务
    （持有该浏览器等待完成 → 下载 → 注入系统未读消息唤醒 bot 决策是否
    发送）→ 本调用立即返回。

    Args:
        prompt: 视频描述。
        model: 视频模型（仅限免费模型 Seedance 2.0 Fast / Seedance 2.0 Mini）。
        aspect_ratio: 画面比例（自动/16:9/9:16/3:4/4:3/1:1/21:9）。
        duration: 视频时长（4s/10s/15s）。
        auto_confirm: 是否自动确认豆包给出的参数调整建议。
        confirm_text: 自定义确认/调整文案（空且 auto_confirm=True 则默认发"确认"）。
        local_paths: 参考图本地路径列表（图生视频，可多张；空表示纯文生视频）。
        stream_id: 聊天流 ID（唤醒目标）。

    Returns:
        tuple[bool, str, str]: (是否提交成功, 说明, 参数建议文本)。
    """
    from .actions import DoubaoActions
    from .constants import (
        DEFAULT_VIDEO_DURATION,
        DEFAULT_VIDEO_MODEL,
        DEFAULT_VIDEO_RATIO,
        SKILL_VIDEO_BUTTON_ID,
        SKILL_VIDEO_PLACEHOLDER,
        SITE_URL,
    )
    from .video_wake import start_video_background_wait

    want_model = (model or "").strip() or DEFAULT_VIDEO_MODEL
    want_ratio = (aspect_ratio or "").strip() or DEFAULT_VIDEO_RATIO
    want_duration = (duration or "").strip() or DEFAULT_VIDEO_DURATION

    # 手动持有媒体锁（不用 async with）：提交成功后锁与媒体页面一并移交
    # 后台任务；其余路径统一在 finally 关闭页面并释放锁。
    await _doubao_media_lock.acquire()
    lock_transferred = False
    media_page: tuple[Any, Any] | None = None
    try:
        try:
            manager, page = await _open_doubao_media_page()
            media_page = (manager, page)
        except Exception as exc:  # noqa: BLE001 - 启动失败
            logger.error(f"打开豆包媒体页面失败: {exc}", exc_info=True)
            return False, f"打开豆包媒体页面失败: {exc}", ""
        try:
            await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            actions = DoubaoActions(page)
            # 登录预检：未登录绝不继续自动化（防加深风控）
            if not await actions.ensure_logged_in():
                return False, "豆包登录态失效：请运行 scripts/login_doubao.py 重新登录后重试", ""
            if not await actions.new_chat():
                return False, "新建豆包对话失败", ""
            await page.wait_for_timeout(2000)
            ok, msg = await actions.activate_skill(SKILL_VIDEO_BUTTON_ID, SKILL_VIDEO_PLACEHOLDER)
            if not ok:
                return False, f"激活视频生成技能失败: {msg}", ""

            # 参考图（图生视频）：视频技能受理后注入图片，豆包以其作参考帧（可多张）
            ref_paths = [str(x) for x in (local_paths or []) if str(x).strip()]
            if ref_paths:
                ok, msg = await actions.upload_files(
                    ref_paths,
                    max_size_mb=DOUBAO_UPLOAD_MAX_MB,
                    allowed_extensions=DOUBAO_ALLOWED_EXTENSIONS,
                )
                if not ok:
                    return False, f"上传参考图失败: {msg}", ""
                await page.wait_for_timeout(1500)

            # 设置原生 UI 参数
            param_ok, param_msg = await actions.set_video_params(
                model=want_model,
                aspect_ratio=want_ratio,
                duration=want_duration,
            )
            if not param_ok:
                logger.warning(f"设置视频原生参数提示: {param_msg}")

            ok, msg = await actions.submit_prompt(prompt.strip())
            if not ok:
                return False, msg, ""
            # 豆包会先返回参数确认消息（需回复"确认"才真正开始生成）
            confirm_ok, confirm_msg, proposal = await actions.await_param_confirm(
                auto_confirm=auto_confirm,
                confirm_text=confirm_text,
            )
            if not confirm_ok:
                if actions.last_blocker:
                    return False, (
                        f"豆包触发了人机验证（{actions.last_blocker}），自动化无法完成。"
                        "请手动在浏览器中完成验证后重试。"
                    ), ""
                if not auto_confirm and proposal:
                    return True, "检测到豆包参数建议，等待大模型决策", proposal
                return False, f"确认生成参数失败: {confirm_msg}", proposal

            # 提交成功：后台守护任务接管浏览器与媒体锁（任务结束后关闭
            # 浏览器并释放锁），本调用立即返回
            started = await start_video_background_wait(
                stream_id,
                actions,
                prompt.strip()[:60],
                closer=lambda: manager.close_page(page),
                lock=_doubao_media_lock,
            )
            if started:
                lock_transferred = True
                # 页面与锁一并移交后台任务，由其在任务结束时关闭/释放
                media_page = None
                return True, (
                    "视频生成任务已提交并转入后台等待（豆包生成约需 2-5 分钟）。"
                    "完成后会收到系统通知，届时再决定是否把视频发给用户。"
                ), proposal
            return True, "视频生成任务已提交（后台等待任务启动失败，结果需稍后手动查看豆包页面）", proposal
        except Exception as exc:  # noqa: BLE001 - 提交异常
            logger.error(f"提交豆包视频任务异常: {exc}", exc_info=True)
            return False, f"提交视频任务异常: {exc}", ""
    finally:
        if media_page is not None:
            await media_page[0].close_page(media_page[1])
        if not lock_transferred:
            _release_media_lock()
