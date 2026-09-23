"""Gemini 站点的提问、截图、图片及分享服务。"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger

from ..base.browser_session import get_manager
from ..base.shared import AskResult, _locate_conversation, _upload_notice
from .actions import GeminiActions

logger = get_logger("ai_ui_snapshot.service")

GEMINI_ALLOWED_EXTENSIONS = (
    "png,jpg,jpeg,webp,gif,bmp,svg,ico,tif,tiff,avif,apng,jfif,psd,eps,"
    "md,txt,pdf,doc,docx,xls,xlsx,csv,ppt,pptx,json,py,zip,"
    # 常见音频
    "mp3,wav,ogg,flac,aac,aiff,m4a,opus,wma,amr,mid,oga,mp2,m4b,"
    # 常见视频
    "mp4,mov,m4v,webm,avi,mkv,mpg,mpeg,3gp,ts,flv,ogv,wmv,rmvb,m2ts"
)


async def ask_gemini(
    question: str,
    *,
    stream_id: str = "",
    timeout_s: int = 240,
    model: str = "",
    think: bool | None = None,
    conversation: str = "",
    local_path: str | None = None,
    output_format: str = "auto",
    return_scope: str = "last",
    scope: str = "viewport",
    rounds: int = 1,
    multimodal: bool = False,
    upload_max_size_mb: float = 10.0,
    upload_allowed_extensions: str = GEMINI_ALLOWED_EXTENSIONS,
) -> AskResult:
    """统一入口：向 Gemini 真实提问，按输出形式返回结果。

    完整链路：获取（或创建）共享浏览器会话 → busy 加锁（会话保活）→
    设置模型（可选）→ （可选）上传 → 提问 → 等待回复 → 按 output_format
    处理 → release 解锁。Gemini 为并行第二站点，与 DeepSeek 互不影响；
    浏览器会话经代理（theme=gemini 的 profile）访问。

    Args:
        question: 用户问题（output_format=snapshot 时无需提问，可为空）。
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        timeout_s: 等待 AI 回复超时秒数。
        model: Gemini 模型（家族关键词 Flash-Lite / Flash / Pro，按关键词
            匹配网页当前版本；空默认用 Flash；Gemini 不锁定模型，每次调用
            可自由切换）。
        think: 是否开启扩展思考（True 开 / False 关 / None 不修改）。
        conversation: 对话定位方式：空（默认）沿用当前对话；精确标题进入
            该历史会话（未命中则新建）；"__new__" 强制新建。
        local_path: 已解析好的上传文件路径（None 表示不上传）。
        output_format: 输出形式（auto 纯文本返回 / snapshot 截图）。
        return_scope: 信息返回范围（last 最新回复，默认 / full 整段对话）。
        upload_max_size_mb: 上传文件大小上限（MB）。
        upload_allowed_extensions: 允许上传的扩展名（逗号分隔，小写）。

    Returns:
        AskResult: 结构化结果（成功时 ok=True，含对应输出字段）。
    """
    if not question or not question.strip():
        return AskResult(ok=False, error="问题不能为空")

    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key, theme="gemini")
        session.hold()
        manager.touch(stream_key, theme="gemini")
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = GeminiActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key, theme="gemini"),
            theme=manager.page_theme,
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )

        # 0b. 应用页面明暗主题（Gemini 改 body class，无 reload 副作用）
        if manager.page_theme:
            try:
                await actions.set_theme(manager.page_theme)
            except Exception:  # noqa: BLE001 - 主题应用失败不阻塞提问
                logger.warning("应用 Gemini 页面主题失败，使用默认主题")

        # 1. conversation 路由：未显式指定对话标题时默认新建；显式传精确标题进入继续
        err = await _locate_conversation(
            actions,
            conversation,
            new_chat_on_miss=True,
            default_new=True,
            new_page_cb=(
                (lambda: manager.new_session_page(stream_key, theme="gemini"))
                if session.active_conversation
                else None
            ),
        )
        if err:
            return AskResult(ok=False, error=err)

        # 2. 设置模型：Gemini 不锁定模型，每次可切换。model 为空时默认用
        #    Flash 家族（set_model 按关键词匹配网页当前版本，已在该家族时跳过）。
        want_model = (model or "").strip() or "Flash"
        ok, msg = await actions.set_model(want_model)
        if not ok:
            return AskResult(ok=False, error=f"设置 Gemini 模型失败: {msg}")

        # 2. 设置扩展思考开关（think 为空则不修改，由上层自主决定）
        if think is not None:
            ok, msg = await actions.set_thinking(think)
            if not ok:
                return AskResult(ok=False, error=f"设置扩展思考失败: {msg}")

        # 3. 上传附件（可选）
        if local_path:
            ok, msg = await actions.upload_file(
                local_path,
                max_size_mb=upload_max_size_mb,
                allowed_extensions=GEMINI_ALLOWED_EXTENSIONS,
            )
            if not ok:
                return AskResult(ok=False, error=msg)

        # 3. 提问并等待回复
        image_count_before = await actions.generated_image_count()
        try:
            previous_reply = await actions.get_conversation_text(scope="last")
            ok, msg = await actions.ask(question.strip())
            if not ok:
                return AskResult(ok=False, error=msg)
            done, last_reply = await actions.wait_reply_done(
                timeout_s=timeout_s,
                previous_reply=previous_reply,
            )
        except Exception as exc:  # noqa: BLE001 - 网页提问失败
            logger.error(f"Gemini 网页提问失败: {exc}", exc_info=True)
            return AskResult(ok=False, error=f"Gemini 网页提问失败: {exc}")
        if not done:
            return AskResult(ok=False, error="等待 Gemini 回复超时")

        # 3b. 检测对话中是否生成了图片（AI 直接出图时自动下载；纯文本回复跳过）
        image_path = ""
        gemini_images: list[str] = []
        gemini_descs: list[str] = []
        gemini_b64: list[str] = []
        try:
            image_path = await actions.try_download_generated_image(
                save_dir="data/ai_ui_snapshot_profile/gemini/images",
                wait_s=30,
                previous_count=image_count_before,
            ) or ""
            if image_path:
                gemini_images.append(image_path)
                import base64
                from src.app.plugin_system.api import media_api
                try:
                    with open(image_path, "rb") as f:  # noqa: PTH123
                        raw_b64 = base64.b64encode(f.read()).decode("utf-8")
                    if multimodal:
                        gemini_b64.append(raw_b64)
                    else:
                        desc = await media_api.recognize_media(raw_b64, "image")
                        if desc:
                            gemini_descs.append(desc.strip())
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"处理 Gemini 生成图片感知失败: {exc}")
        except Exception as exc:  # noqa: BLE001 - 检测/下载失败不阻塞提问
            logger.warning(f"检测 Gemini 生成图片失败: {exc}")

        # 4. 按 return_scope 取信息返回文本
        if return_scope == "full":
            content = await actions.get_conversation_text(scope="full")
        else:
            content = last_reply

        # 5. 读取当前活跃会话 ID 与标题并同步到会话（ID 作稳定 key、标题作展示）。
        #    新建对话首条提问时标题由 AI 异步生成，先短暂等待确保返回时非空。
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.wait_conversation_title()
        session.set_active_conversation(current_id, current_title)

        # 6. 仅 snapshot 输出形式截图
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
                model_name="gemini.google.com",
                upload=_upload_notice(local_path),
                image_path=image_path,
                images=gemini_images,
                image_descriptions=gemini_descs,
                images_base64=gemini_b64,
                snapshot_meta=meta,
            )
        return AskResult(
            ok=True,
            reply=content,
            conversation=current_title,
            model_name="gemini.google.com",
            upload=_upload_notice(local_path),
            image_path=image_path,
            images=gemini_images,
            image_descriptions=gemini_descs,
            images_base64=gemini_b64,
        )
    finally:
        session.release()


async def download_gemini_chat_image(
    *,
    stream_id: str = "",
    conversation: str = "",
    save_dir: str = "data/ai_ui_snapshot_profile/gemini/images",
) -> AskResult:
    """下载当前或指定 Gemini 对话中的最近一张生成图片。

    Args:
        stream_id: 聊天流 ID。
        conversation: 对话标题；为空使用当前页面。
        save_dir: 图片保存目录。

    Returns:
        AskResult: 成功时 images 与 image_path 包含本地文件路径。
    """
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key, theme="gemini")
        session.hold()
        manager.touch(stream_key, theme="gemini")
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = GeminiActions(
            session.page,
            touch_cb=lambda: manager.touch(stream_key, theme="gemini"),
        )
        err = await _locate_conversation(actions, conversation)
        if err:
            return AskResult(ok=False, error=err)
        path = await actions.download_generated_image(save_dir)
        if not path:
            return AskResult(ok=False, error="当前对话中未找到可下载的 Gemini 生成图片")
        current_title = await actions.get_active_conversation_title()
        return AskResult(
            ok=True,
            conversation=current_title,
            image_path=path,
            images=[path],
            model_name="gemini.google.com",
        )
    finally:
        session.release()


async def capture_gemini_snapshot(
    *,
    stream_id: str = "",
    conversation: str = "",
    think: str = "auto",
    scope: str = "viewport",
    rounds: int = 1,
) -> AskResult:
    """直接截取当前/指定 Gemini 对话界面，不提问、不改设置。

    定位会话（空=沿用当前 / 精确标题=进入该历史会话 / __new__=新建）后
    直接截图；与 DeepSeek 的 :func:`capture_snapshot` 对应，供 Gemini
    侧 ``gemini_snapshot`` 工具调用。

    Args:
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        conversation: 对话定位：空（默认）沿用当前对话；精确标题进入
            该历史会话（未命中报错）；"__new__" 强制新建。
        think: 思考参数。
        scope: 截图范围模式（viewport 正常视窗 / rounds 倒序轮次 / full 整页长图）。
        rounds: 截取回复轮数（scope=rounds 时生效）。

    Returns:
        AskResult: 成功时 ok=True 且 data_uri 含截图；失败时 ok=False。
    """
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key, theme="gemini")
        session.hold()
        manager.touch(stream_key, theme="gemini")
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = GeminiActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key, theme="gemini"),
            theme=manager.page_theme,
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
            "conversation", think=think, scope=scope, rounds=rounds
        )
        if not data_uris:
            return AskResult(ok=False, error="对话区截图失败", conversation=current_title)
        meta = await actions.get_snapshot_meta(scope=scope, rounds=rounds)
        return AskResult(
            ok=True,
            data_uri=data_uris,
            conversation=current_title,
            model_name="gemini.google.com",
            snapshot_meta=meta,
        )
    finally:
        session.release()


async def generate_gemini_image(
    prompt: str,
    *,
    stream_id: str = "",
    timeout_s: int = 180,
    reference_paths: list[str] | None = None,
    save_dir: str = "data/ai_ui_snapshot_profile/gemini/images",
    upload_allowed_extensions: str = "png,jpg,jpeg,webp,gif,bmp",
) -> str | None:
    """用 Gemini 原生能力生成图片并保存到本地。

    Args:
        prompt: 图片描述（传 reference_paths 时描述修改意图）。
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        timeout_s: 等待生成超时秒数。
        reference_paths: 参考图本地路径列表（可空；提供后基于参考图改图/生成）。
        save_dir: 生成图片保存目录（自动创建）。
        upload_allowed_extensions: 参考图允许的扩展名。

    Returns:
        str | None: 保存的本地文件路径；失败返回 None。
    """
    manager = get_manager()
    page = None
    try:
        page = await manager.open_page("gemini", "https://gemini.google.com/app")
        await page.wait_for_timeout(3000)
        actions = GeminiActions(
            page,
            max_screenshot_height=manager.max_screenshot_height,
            theme=manager.page_theme,
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )
        if not await actions.new_chat():
            logger.error("Gemini 独立生图新建对话失败")
            return None
        await page.wait_for_timeout(1500)
        ok, path = await actions.generate_image(
            prompt,
            save_dir,
            timeout_s=timeout_s,
            reference_paths=reference_paths,
            upload_allowed_extensions=upload_allowed_extensions,
        )
        return path if ok else None
    except Exception as exc:  # noqa: BLE001 - 生成失败
        logger.error(f"Gemini 图片生成失败: {exc}", exc_info=True)
        return None
    finally:
        await manager.close_page(page)


async def create_gemini_share(
    *,
    stream_id: str = "",
    conversation: str = "",
) -> AskResult:
    """直接获取当前/指定 Gemini 对话的公开分享链接，不提问。

    Args:
        stream_id: 聊天流 ID（用于隔离浏览器会话）。
        conversation: 对话定位：空（默认）沿用当前对话；精确标题进入
            该历史会话（未命中报错）。

    Returns:
        AskResult: 成功时 ok=True 且 share_url 非空；失败时 ok=False。
    """
    try:
        manager = get_manager()
        stream_key = stream_id or "default"
        session = await manager.get(stream_key, theme="gemini")
        session.hold()
        manager.touch(stream_key, theme="gemini")
    except Exception as exc:  # noqa: BLE001 - 会话创建失败
        logger.error(f"获取浏览器会话失败: {exc}", exc_info=True)
        return AskResult(ok=False, error=f"获取浏览器会话失败: {exc}")

    try:
        actions = GeminiActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_key, theme="gemini"),
            theme=manager.page_theme,
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )
        # 定位会话：空=沿用当前 / 精确标题=进入 / __new__=新建
        # （未命中报错：无可分享内容，不拿空会话去建链接）
        err = await _locate_conversation(actions, conversation)
        if err:
            return AskResult(ok=False, error=err)
        share_url = await actions.create_share_link()
        if not share_url:
            return AskResult(ok=False, error="生成分享链接失败")
        current_id = await actions.get_active_conversation_id()
        current_title = await actions.get_active_conversation_title()
        session.set_active_conversation(current_id, current_title)
        return AskResult(ok=True, share_url=share_url, conversation=current_title)
    finally:
        session.release()
