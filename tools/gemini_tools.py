"""LLM 工具集：向 Gemini 提问 / 生成图片 / 获取分享链接。

把 Gemini 网页操作封装为四个独立的高层工具，bot 通过参数使用，无需逐步
操控浏览器：
- ask_gemini_ai：向 Gemini 真实提问（全模态上传图片/音频/视频/文档），返回回复文本。
- gemini_generate_image：用 Gemini 原生能力生成图片（可带参考图改图）。
- gemini_snapshot：直接截取当前/指定 Gemini 对话界面为长截图并发送，不提问。
- gemini_share：获取当前/指定对话的公开分享链接。

工具共用服务层（service）的提问 / 生成 / 截图 / 分享入口。
"""

from __future__ import annotations

from typing import Annotated, Any

from src.app.plugin_system.api import send_api
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseTool

from ..config import AiUiSnapshotConfig
from ..services.service import (
    AskResult,
    ask_gemini,
    capture_gemini_snapshot,
    create_gemini_share,
    generate_gemini_image,
    resolve_media_path,
    strip_data_uri_prefix,
)
from .base import _ToolBase

logger = get_logger("ai_ui_snapshot.gemini_tool")

# 生成图片默认保存目录（本地，不发布）
_DEFAULT_IMAGE_DIR = "data/ai_ui_snapshot_profile/gemini/images"


class AskGeminiAiTool(_ToolBase):
    """向 Gemini 真实提问，返回回复内容供内部消化转述。"""

    name: str = "ask_gemini_ai"
    description: str = (
        "向 Gemini 真实提问，像真人一样使用 Gemini，返回回复文本供你自然转述。"
        "Gemini 是全模态模型，可附带图片/语音/视频/文档一起提问（image_id 传聊天里图片、"
        "语音、视频占位符中的 media_id，file_name 传 media_retriever 已下载文件名）。"
        "常用于：①音频更详细的信息——ASR 只能转文字，识别不准、语音带语调/环境音时，"
        "把语音 media_id 传给 image_id，让 Gemini 全模态听出说了什么、还能听出环境音与"
        "语气调调；②看视频，把视频 media_id 传给 image_id，Gemini 能看懂画面并"
        "详细转述内容，转述后你当作自己亲眼看过一样自然带出；③多模态理解、深度推理、长文。"
        "model 可指定模型（3.5 Flash-Lite 极速 / 3.6 Flash 全方位 / "
        "3.1 Pro 高阶），空默认用 3.6 Flash。Gemini 不锁定模型，每次调用可按需切换，"
        "无需开新对话。think 控制是否开启扩展思考（深度思考）：true 开 / false 关 / "
        "空由你按问题复杂度自主决定——复杂推理/数学/代码题可开，简单问答不必开。"
        "conversation 控制对话定位：空沿用当前、精确标题进入历史会话（未命中新建）、"
        "__new__ 开新对话。每次返回 conversation 对话标题，记住它可回到同一对话。"
        "生成图片请用 gemini_generate_image，分享链接用 gemini_share。"
    )

    async def execute(
        self,
        question: Annotated[str, "要提问的问题原文（完整、自然语言）"] = "",
        model: Annotated[str, "Gemini 模型：'3.5 Flash-Lite'/'3.6 Flash'/'3.1 Pro'，空默认用 3.6 Flash"] = "",
        think: Annotated[bool | None, "是否开启扩展思考（深度思考）：true 开 / false 关 / 空由你按问题复杂度自主决定"] = None,
        conversation: Annotated[str, "对话定位：空（默认）沿用当前对话；历史会话精确标题则进入继续；'__new__' 强制开新对话"] = "",
        image_id: Annotated[str, "附带提问的媒体 media_id（聊天里图片/语音/视频占位符 [media(media_id)] 中的哈希），可空；语音即转述说了什么、视频即看画面内容"] = "",
        file_name: Annotated[str, "附带提问的已下载文件名（media_retriever 已下载文件），可空"] = "",
        return_scope: Annotated[str, "信息返回范围：'last'（最新一条 AI 回复，默认）/ 'full'（整段对话）"] = "last",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：解析参数与上传路径，调用 Gemini 提问入口，返回回复文本。

        Args:
            question: 要提问的问题原文。
            model: Gemini 模型。
            think: 是否开启扩展思考。
            conversation: 对话定位（空沿用当前 / 精确标题进入 / __new__ 新建）。
            image_id: 附带媒体 media_id（图片/语音/视频，可空）。
            file_name: 附带已下载文件名（可空）。
            return_scope: 信息返回范围。

        Returns:
            tuple[bool, str | dict]: (是否成功, 结构化结果或错误信息)。
        """
        if not question or not question.strip():
            return False, "问题不能为空"

        config = self.plugin.config if isinstance(self.plugin.config, AiUiSnapshotConfig) else None
        if config is None:
            return False, "插件配置缺失，无法执行"

        stream_id = self.get_current_stream_id()

        # 附带图片/文件：先把 media_id / 已下载文件名解析为本地路径
        local_path: str | None = None
        if image_id or file_name:
            if image_id:
                local_path = await resolve_media_path(image_id)
                if not local_path:
                    return False, f"无法解析图片 media_id: {image_id}"
            elif file_name:
                local_path = await self._resolve_downloaded_file(stream_id, file_name)
                if not local_path:
                    return False, f"未找到已下载文件: {file_name}"

        result: AskResult = await ask_gemini(
            question.strip(),
            stream_id=stream_id,
            timeout_s=config.web.reply_timeout,
            model=model,
            think=think,
            conversation=conversation,
            local_path=local_path,
            output_format="auto",
            return_scope=return_scope,
            upload_max_size_mb=config.upload.max_size_mb,
        )
        if not result.ok:
            return False, result.error or "向 Gemini 提问失败"

        # Gemini 在对话中直接生成了图片：读取并发送到当前聊天
        image_sent = await self._send_generated_image(result, stream_id)

        summary = (
            "这是 Gemini 的回复内容，请自然地向用户转述/消化，无需发送截图或链接。"
            "conversation 为当前对话标题，后续追问可传同一标题回到此对话。"
        )
        if image_sent:
            summary = (
                "Gemini 在回答时直接生成了图片，图片已自动发送到当前聊天，"
                "回复正文见 reply 字段，请一并自然转述。"
            )

        return True, {
            "model": result.model_name,
            "reply": result.reply,
            "conversation": result.conversation,
            "summary": summary,
            "upload": result.upload,
        }

    @staticmethod
    async def _send_generated_image(result: AskResult, stream_id: str) -> bool:
        """将 Gemini 对话中生成的图片读取并发送到当前聊天。

        Args:
            result: Gemini 提问结果（image_path 非空时尝试发图）。
            stream_id: 聊天流 ID。

        Returns:
            bool: 是否成功发送了图片。
        """
        if not result.image_path:
            return False
        try:
            import base64 as _b64
            import pathlib as _pl

            raw = _pl.Path(result.image_path).read_bytes()
            image_b64 = _b64.b64encode(raw).decode("ascii")
            return bool(await send_api.send_image(
                image_b64,
                stream_id,
                processed_plain_text="[Gemini 生成的图片]",
            ))
        except Exception as exc:  # noqa: BLE001 - 发图失败不阻塞回复
            logger.warning(f"发送 Gemini 生成图片失败: {exc}")
            return False


class GeminiGenerateImageTool(_ToolBase):
    """用 Gemini 原生能力生成图片（可带参考图改图）。"""

    name: str = "gemini_generate_image"
    description: str = (
        "用 Gemini 原生图片生成能力生成一张图片并发送到当前聊天。"
        "prompt 描述想要的画面（画幅/风格/内容写清楚，如'一张横版的赛博朋克城市夜景水彩画'）。"
        "reference_image_ids 可传参考图 media_id 列表（聊天图片占位符中的哈希，可传 1 张或"
        "多张）：提供后 Gemini 会基于这些参考图改图/参考生成（prompt 里描述修改意图，"
        "如'把颜色改成蓝色'、'把第 1 张里的猫放到第 2 张的背景里'）。"
        "生成后自动把图片发送到当前聊天。"
    )

    async def execute(
        self,
        prompt: Annotated[str, "图片描述（写清画幅/风格/内容；带参考图时描述修改意图）"] = "",
        reference_image_ids: Annotated[list[str] | None, "参考图 media_id 列表（聊天图片占位符 [media(media_id)] 中的哈希），可空或传 1 张/多张；提供后基于这些参考图改图/生成"] = None,
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：生成图片并发送。

        Args:
            prompt: 图片描述。
            reference_image_ids: 参考图 media_id 列表（可空）。

        Returns:
            tuple[bool, str | dict]: (是否成功, 结果或错误)。
        """
        if not prompt or not prompt.strip():
            return False, "图片描述不能为空"

        config = self.plugin.config if isinstance(self.plugin.config, AiUiSnapshotConfig) else None
        if config is None:
            return False, "插件配置缺失，无法执行"

        stream_id = self.get_current_stream_id()

        # 解析参考图路径（支持多张）
        reference_paths: list[str] = []
        for media_id in reference_image_ids or []:
            media_id = str(media_id or "").strip()
            if not media_id:
                continue
            ref_path = await resolve_media_path(media_id)
            if not ref_path:
                return False, f"无法解析参考图 media_id: {media_id}"
            reference_paths.append(ref_path)

        path = await generate_gemini_image(
            prompt.strip(),
            stream_id=stream_id,
            timeout_s=config.web.reply_timeout,
            reference_paths=reference_paths or None,
            save_dir=_DEFAULT_IMAGE_DIR,
        )
        if not path:
            return False, "Gemini 图片生成失败"

        # 发送生成的图片：send_image 期望 base64（不识别本地路径），读取文件转 base64
        try:
            import base64 as _b64
            import pathlib as _pl

            raw = _pl.Path(path).read_bytes()
            image_b64 = _b64.b64encode(raw).decode("ascii")
        except Exception as exc:  # noqa: BLE001 - 读取失败
            return False, f"读取生成的图片失败: {exc}"
        sent = await send_api.send_image(
            image_b64,
            stream_id,
            processed_plain_text="[Gemini 生成的图片]",
        )
        if not sent:
            return False, "图片已生成但发送失败"

        return True, {
            "sent": True,
            "path": path,
            "summary": "已用 Gemini 生成图片并发出，请用拟人口吻简单引述即可。",
        }


class GeminiSnapshotTool(_ToolBase):
    """直接截取 Gemini 对话界面为长截图并发送，不提问、不改设置。"""

    name: str = "gemini_snapshot"
    description: str = (
        "直接截取 Gemini 对话界面为官方长截图并发送到当前聊天，不提问、不改模型/扩展思考开关。"
        "适用于：对方想直接看 Gemini 原始界面、或 Gemini 回复很长（人懒得总结）时，"
        "把界面截图甩给对方看。conversation 参数控制截哪个会话："
        "空（默认）截当前对话；传历史会话精确标题则进入该会话再截（未命中则新建）；"
        "传 __new__ 开新对话（空会话，一般不用）。"
        "说明：Gemini 的扩展思考（深度思考）开关只决定 AI 是否思考，思考内容会内联显示在"
        "回复里，无独立折叠 UI；需要让 AI 思考后回复，请在 ask_gemini_ai 的 think 参数控制。"
    )

    async def execute(
        self,
        conversation: Annotated[str, "对话定位：空（默认）截当前对话；历史会话精确标题则进入该会话再截；'__new__' 开新对话"] = "",
        think: Annotated[str, "'auto'（默认）。Gemini 思考内容无独立折叠 UI，截图不展开/折叠思考"] = "auto",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：定位会话并直接截图发送。

        Args:
            conversation: 对话定位（空当前 / 精确标题进入 / __new__ 新建）。
            think: 思考过程块展开方式（auto/expand/collapse）。

        Returns:
            tuple[bool, str | dict]: (是否成功, 结果或错误)。
        """
        config = self.plugin.config if isinstance(self.plugin.config, AiUiSnapshotConfig) else None
        if config is None:
            return False, "插件配置缺失，无法执行"

        stream_id = self.get_current_stream_id()
        result: AskResult = await capture_gemini_snapshot(
            stream_id=stream_id,
            conversation=conversation,
            think=think,
        )
        if not result.ok:
            return False, result.error or "截图失败"
        if not result.data_uri:
            return False, "截图失败"
        for piece in result.data_uri:
            if not piece.startswith("data:"):
                return False, "截图失败"
            sent = await send_api.send_image(
                strip_data_uri_prefix(piece),
                stream_id,
                processed_plain_text="[Gemini 界面截图]",
            )
            if not sent:
                return False, "截图已生成但发送失败"
        return True, {
            "sent": True,
            "conversation": result.conversation,
            "summary": "已截取 Gemini 对话界面并发出，请用拟人口吻简单引述即可。conversation 为当前对话标题。",
        }


class GeminiShareTool(_ToolBase):
    """获取 Gemini 当前/指定对话的公开分享链接。"""

    name: str = "gemini_share"
    description: str = (
        "获取 Gemini 当前/指定对话的官方公开分享链接，不提问。"
        "适用于 Gemini 回复很长、想让对方直接看完整内容时，把链接发出去。"
        "conversation 控制取哪个会话：空（默认）取当前对话；传历史会话精确标题则"
        "进入该会话再取（未命中则新建）。返回链接给模型，是否发送由你按场景决定。"
    )

    async def execute(
        self,
        conversation: Annotated[str, "对话定位：空（默认）取当前对话；历史会话精确标题则进入该会话再取；'__new__' 开新对话"] = "",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：获取当前/指定 Gemini 对话的分享链接。

        Args:
            conversation: 对话定位（空当前 / 精确标题进入 / __new__ 新建）。

        Returns:
            tuple[bool, str | dict]: (是否成功, 结果或错误)。
        """
        config = self.plugin.config if isinstance(self.plugin.config, AiUiSnapshotConfig) else None
        if config is None:
            return False, "插件配置缺失，无法执行"

        stream_id = self.get_current_stream_id()
        result: AskResult = await create_gemini_share(
            stream_id=stream_id,
            conversation=conversation,
        )
        if not result.ok:
            return False, result.error or "生成分享链接失败"
        if not result.share_url:
            return False, "生成分享链接失败"
        return True, {
            "share_url": result.share_url,
            "conversation": result.conversation,
            "summary": "已生成 Gemini 官方分享链接，是否发送给用户由你决定。conversation 为当前对话标题。",
        }


# 供插件装配导出的 Gemini 工具类列表
GEMINI_TOOLS: list[type[BaseTool]] = [
    AskGeminiAiTool,
    GeminiGenerateImageTool,
    GeminiSnapshotTool,
    GeminiShareTool,
]
