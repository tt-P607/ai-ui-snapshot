"""LLM 工具：ask_ai_and_snapshot。

当用户提出专业/复杂问题或需要实时信息时，LLM 可调用本工具：插件向 DeepSeek
真实提问，按场景返回回复内容（内部消化转述）或生成官方界面截图/分享链接。
"""

from __future__ import annotations

from typing import Annotated, Any

from src.app.plugin_system.api import send_api
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseTool

from ..config import AiUiSnapshotConfig
from ..services.snapshot_service import (
    AskResult,
    ask_deepseek,
    resolve_media_path,
    strip_data_uri_prefix,
)

logger = get_logger("ai_ui_snapshot.tool")


class AskAiAndSnapshotTool(BaseTool):
    """向 DeepSeek 真实提问，返回回复内容或按场景生成截图/分享链接。"""

    name: str = "ask_ai_and_snapshot"
    description: str = (
        "向 DeepSeek 真实提问，像真人一样使用 DeepSeek。可用于："
        "①专业/深奥/复杂问题的转发与思考；②获取实时、不够新的信息（把 DeepSeek 当外部"
        "信息源，需要联网搜索时在快速模式开启 search）。按场景决定输出："
        "一般情况只把 DeepSeek 回复内容返回（内部消化），由模型自然转述；"
        "当需要给用户展示对话原始界面时用 snapshot 生成官方长截图；"
        "当回复很长或用户要分享时用 share_link 直接获取当前对话的公开分享链接"
        "（无需向 DeepSeek 提问，直接取链接，链接返回给模型，是否发给用户由模型按场景决定）。"
        "可指定模式（快速/专家/识图）、深度思考/联网搜索开关（专家与识图模式不支持联网搜索，"
        "专家模式不支持上传）、附带图片（media_id）或已下载文件（file_name）一起提问"
        "（识图模式真正理解图片，快速模式 OCR）、思考块展开方式（expand/collapse/reveal）、"
        "侧边栏显示（show/hide）、信息返回范围（last 最新回复 / full 整段对话）。"
        "conversation 参数控制对话定位：空（默认）沿用当前对话；传历史会话精确标题则进入"
        "该会话继续（标题用 deepseek_history list 获取；未命中则新建）；传 __new__ 强制开新对话。"
        "每次调用都会返回当前对话标题（conversation 字段），记住它可回到同一对话。"
        "注意：每个对话的模式一经选定即锁定不可切换（默认快速模式）；想换模式必须开新对话"
        "（conversation=__new__）。深度思考与联网搜索默认开启（专家/识图模式不支持联网搜索时会自动忽略）。"
    )

    async def execute(
        self,
        question: Annotated[str, "要提问的问题原文（完整、自然语言）。output_format=share_link 时无需提问，可为空字符串"] = "",
        output_format: Annotated[str, "输出形式（按场景选）：'auto'（默认，只返回 DeepSeek 回复文本，内部消化转述）/ 'snapshot'（需要展示对话原始界面时，生成官方长截图）/ 'share_link'（回复很长或用户要分享时，直接获取当前对话的公开分享链接，不提问）"] = "auto",
        mode: Annotated[str, "对话模式：快速模式/专家模式/识图模式（或 快速/专家/识图），空则沿用当前会话模式（首次默认快速）；每个对话模式一经选定即锁定不可切换"] = "",
        deepthink: Annotated[bool | None, "是否开启深度思考：true/false，默认 true"] = True,
        search: Annotated[bool | None, "是否开启联网搜索：true/false，默认 true；专家/识图模式不支持时自动忽略"] = True,
        new_chat: Annotated[bool, "旧参数：是否先开一个新对话再提问（等价 conversation=__new__，保留兼容）"] = False,
        conversation: Annotated[str, "对话定位：空（默认）沿用当前对话；历史会话精确标题则进入继续（用 deepseek_history list 获取标题，未命中则新建）；'__new__' 强制开新对话"] = "",
        image_id: Annotated[str, "附带提问的图片 media_id（聊天图片占位符 [图片(media_id)] 中的哈希），可空"] = "",
        file_name: Annotated[str, "附带提问的已下载文件名（media_retriever 已下载文件），可空"] = "",
        think: Annotated[str, "截图时思考过程块展开方式：'collapse'（折叠隐藏，默认）/ 'auto'（保持现状）/ 'expand'（强制展开）/ 'reveal'（仅被折叠时展开）"] = "collapse",
        sidebar: Annotated[str, "截图时左侧边栏显示方式：'auto'（保持现状，默认）/ 'show'（展开）/ 'hide'（收起隐藏）"] = "auto",
        return_scope: Annotated[str, "信息返回范围：'last'（最新一条 AI 回复，默认）/ 'full'（整段对话）"] = "last",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：解析参数与上传路径，统一入口提问，按场景映射返回。

        Args:
            question: 要提问的问题原文。
            output_format: 输出形式（auto/snapshot/share_link）。
            mode: 对话模式。
            deepthink: 深度思考开关。
            search: 智能搜索开关。
            new_chat: 旧参数：是否先开新对话再提问（等价 conversation=__new__）。
            conversation: 对话定位（空沿用当前 / 精确标题进入 / __new__ 新建）。
            image_id: 附带图片 media_id（可空）。
            file_name: 附带已下载文件名（可空）。
            think: 思考过程块展开方式。
            sidebar: 侧边栏显示方式。
            return_scope: 信息返回范围。

        Returns:
            tuple[bool, str | dict]: (是否成功, 结构化结果或错误信息)。
        """
        if (not question or not question.strip()) and output_format != "share_link":
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
                # 经 media_retriever Service 获取已下载文件路径（插件间解耦）
                local_path = await self._resolve_downloaded_file(stream_id, file_name)
                if not local_path:
                    return False, f"未找到已下载文件: {file_name}"

        result: AskResult = await ask_deepseek(
            question.strip(),
            stream_id=stream_id,
            timeout_s=config.web.reply_timeout,
            mode=mode,
            deepthink=deepthink,
            search=search,
            new_chat=new_chat,
            conversation=conversation,
            local_path=local_path,
            output_format=output_format,
            think=think,
            sidebar=sidebar,
            return_scope=return_scope,
            upload_max_size_mb=config.upload.max_size_mb,
            upload_allowed_extensions=config.upload.allowed_extensions,
        )
        if not result.ok:
            return False, result.error or "向 DeepSeek 提问失败"

        # 默认：只返回 AI 回复内容给模型内部消化，不发截图/链接
        if output_format == "auto":
            return True, {
                "model": result.model_name,
                "reply": result.reply,
                "conversation": result.conversation,
                "summary": "这是 DeepSeek 的回复内容，请自然地向用户转述/消化，无需发送截图或链接。conversation 为当前对话标题，后续追问可传同一标题回到此对话。",
                "upload": result.upload,
            }

        # share_link：分享链接返回给模型自行决定是否发送
        if output_format == "share_link":
            if not result.share_url:
                return False, "分享链接生成失败"
            return True, {
                "share_url": result.share_url,
                "model": result.model_name,
                "conversation": result.conversation,
                "summary": "已生成官方分享链接，是否发送给用户由你决定。conversation 为当前对话标题。",
                "upload": result.upload,
            }

        # snapshot：生成截图并发送到当前聊天（超长对话分片时逐张发送）
        if not result.data_uri:
            return False, "生成截图失败"
        for piece in result.data_uri:
            if not piece.startswith("data:"):
                return False, "生成截图失败"
            sent = await send_api.send_image(
                strip_data_uri_prefix(piece),
                stream_id,
                processed_plain_text="[AI 界面截图]",
            )
            if not sent:
                return False, "截图已生成但发送失败"

        return True, {
            "sent": True,
            "model": result.model_name,
            "conversation": result.conversation,
            "summary": f"已把这个问题转交给 {result.model_name} 并生成界面截图发出，请用拟人口吻简单引述即可。conversation 为当前对话标题，后续可传同一标题回到此对话。",
            "upload": result.upload,
        }

    @staticmethod
    async def _resolve_downloaded_file(stream_id: str, file_name: str) -> str | None:
        """经 media_retriever Service 解析已下载文件路径（插件间解耦）。

        Args:
            stream_id: 聊天流 ID。
            file_name: 已下载文件名。

        Returns:
            str | None: 本地文件路径；获取失败返回 None。
        """
        try:
            from src.app.plugin_system.api.service_api import get_service

            service = get_service("media_retriever:service:media_retriever_service")
            if service is None:
                logger.warning("未找到 media_retriever Service，无法解析已下载文件")
                return None
            resolve = getattr(service, "resolve_downloaded_file", None)
            if not callable(resolve):
                return None
            result = resolve(stream_id, file_name)
            return str(result) if result else None
        except Exception as exc:  # noqa: BLE001 - 服务不可用时降级
            logger.warning(f"解析 media_retriever 已下载文件失败: {exc}")
            return None
