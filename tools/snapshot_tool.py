"""LLM 工具集：向 DeepSeek 提问 / 直接截图 / 直接取分享链接。

把 DeepSeek 网页操作封装为三个独立的高层工具，bot 通过参数使用，无需逐步
操控浏览器：
- ask_ai_and_snapshot：真实提问，返回回复文本（内部消化转述）。
- deepseek_snapshot：直接截取当前/指定对话界面为长截图并发送，不提问。
- deepseek_share：直接获取当前/指定对话的官方分享链接，不提问。

三个工具共用服务层（snapshot_service）的提问 / 截图 / 分享入口。
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
    capture_snapshot,
    create_share,
    resolve_media_path,
    strip_data_uri_prefix,
)

logger = get_logger("ai_ui_snapshot.tool")


class AskAiAndSnapshotTool(BaseTool):
    """向 DeepSeek 真实提问，返回回复内容供内部消化转述。"""

    name: str = "ask_ai_and_snapshot"
    description: str = (
        "向 DeepSeek 真实提问，像真人一样使用 DeepSeek，返回回复文本供你自然转述。"
        "可用于：①专业/深奥/复杂问题的转发与思考；②获取实时、不够新的信息"
        "（把 DeepSeek 当外部信息源，需要联网搜索时在快速模式开启 search）。"
        "可指定模式（快速/专家/识图）、深度思考/联网搜索开关（专家与识图模式不支持"
        "联网搜索，专家模式不支持上传）、附带图片（media_id）或已下载文件（file_name）"
        "一起提问（识图模式真正理解图片，快速模式 OCR）、信息返回范围"
        "（last 最新回复 / full 整段对话）。"
        "conversation 参数控制对话定位：空（默认）沿用当前对话；传历史会话精确标题则"
        "进入该会话继续（标题用 deepseek_history list 获取；未命中则新建）；"
        "传 __new__ 强制开新对话。每次调用都会返回当前对话标题（conversation 字段），"
        "记住它可回到同一对话。注意：每个对话的模式一经选定即锁定不可切换（默认快速模式）；"
        "想换模式必须开新对话（conversation=__new__）。深度思考与联网搜索默认开启"
        "（专家/识图模式不支持联网搜索时会自动忽略）。"
        "需要展示 DeepSeek 原始界面或长回复时，另用 deepseek_snapshot 截图、"
        "deepseek_share 取分享链接。"
    )

    async def execute(
        self,
        question: Annotated[str, "要提问的问题原文（完整、自然语言）"] = "",
        mode: Annotated[str, "对话模式：快速模式/专家模式/识图模式（或 快速/专家/识图），空则沿用当前会话模式（首次默认快速）；每个对话模式一经选定即锁定不可切换"] = "",
        deepthink: Annotated[bool | None, "是否开启深度思考：true/false，默认 true"] = True,
        search: Annotated[bool | None, "是否开启联网搜索：true/false，默认 true；专家/识图模式不支持时自动忽略"] = True,
        new_chat: Annotated[bool, "旧参数：是否先开一个新对话再提问（等价 conversation=__new__，保留兼容）"] = False,
        conversation: Annotated[str, "对话定位：空（默认）沿用当前对话；历史会话精确标题则进入继续（用 deepseek_history list 获取标题，未命中则新建）；'__new__' 强制开新对话"] = "",
        image_id: Annotated[str, "附带提问的图片 media_id（聊天图片占位符 [图片(media_id)] 中的哈希），可空"] = "",
        file_name: Annotated[str, "附带提问的已下载文件名（media_retriever 已下载文件），可空"] = "",
        return_scope: Annotated[str, "信息返回范围：'last'（最新一条 AI 回复，默认）/ 'full'（整段对话）"] = "last",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：解析参数与上传路径，调用提问入口，返回回复文本。

        Args:
            question: 要提问的问题原文。
            mode: 对话模式。
            deepthink: 深度思考开关。
            search: 智能搜索开关。
            new_chat: 旧参数：是否先开新对话（等价 conversation=__new__）。
            conversation: 对话定位（空沿用当前 / 精确标题进入 / __new__ 新建）。
            image_id: 附带图片 media_id（可空）。
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
            output_format="auto",
            return_scope=return_scope,
            upload_max_size_mb=config.upload.max_size_mb,
        )
        if not result.ok:
            return False, result.error or "向 DeepSeek 提问失败"

        return True, {
            "model": result.model_name,
            "reply": result.reply,
            "conversation": result.conversation,
            "summary": "这是 DeepSeek 的回复内容，请自然地向用户转述/消化，无需发送截图或链接。conversation 为当前对话标题，后续追问可传同一标题回到此对话。",
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

            service = get_service("media_retriever:service:media_retriever")
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


class DeepseekSnapshotTool(BaseTool):
    """直接截取 DeepSeek 对话界面为长截图并发送，不提问、不改模式。"""

    name: str = "deepseek_snapshot"
    description: str = (
        "直接截取 DeepSeek 对话界面为官方长截图并发送到当前聊天，不提问、不切换模式。"
        "适用于：对方想直接看 DeepSeek 原始界面、或 DeepSeek 回复很长（人懒得总结）时，"
        "把界面截图甩给对方看，直观又省事。conversation 参数控制截哪个会话："
        "空（默认）截当前对话；传历史会话精确标题则进入该会话再截"
        "（标题用 deepseek_history list 获取，未命中报错）；传 __new__ 开新对话（空会话，一般不用）。"
        "think 控制思考过程块展开方式（collapse 默认折叠 / auto / expand / reveal），"
        "sidebar 控制左侧边栏显示（auto 默认 / show / hide）。"
    )

    async def execute(
        self,
        conversation: Annotated[str, "对话定位：空（默认）截当前对话；历史会话精确标题则进入该会话再截（用 deepseek_history list 获取标题，未命中报错）；'__new__' 开新对话（空会话）"] = "",
        think: Annotated[str, "截图时思考过程块展开方式：'collapse'（折叠隐藏，默认）/ 'auto'（保持现状）/ 'expand'（强制展开）/ 'reveal'（仅被折叠时展开）"] = "collapse",
        sidebar: Annotated[str, "截图时左侧边栏显示方式：'auto'（保持现状，默认）/ 'show'（展开）/ 'hide'（收起隐藏）"] = "auto",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：定位会话并直接截图发送。

        Args:
            conversation: 对话定位（空当前 / 精确标题进入 / __new__ 新建）。
            think: 思考块展开方式。
            sidebar: 侧边栏显示方式。

        Returns:
            tuple[bool, str | dict]: (是否成功, 结果或错误)。
        """
        config = self.plugin.config if isinstance(self.plugin.config, AiUiSnapshotConfig) else None
        if config is None:
            return False, "插件配置缺失，无法执行"

        stream_id = self.get_current_stream_id()
        result: AskResult = await capture_snapshot(
            stream_id=stream_id,
            conversation=conversation,
            think=think,
            sidebar=sidebar,
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
                processed_plain_text="[AI 界面截图]",
            )
            if not sent:
                return False, "截图已生成但发送失败"
        return True, {
            "sent": True,
            "conversation": result.conversation,
            "summary": "已截取 DeepSeek 对话界面并发出，请用拟人口吻简单引述即可。conversation 为当前对话标题。",
        }


class DeepseekShareTool(BaseTool):
    """直接获取 DeepSeek 当前/指定对话的官方公开分享链接，不提问。"""

    name: str = "deepseek_share"
    description: str = (
        "直接获取 DeepSeek 当前/指定对话的官方公开分享链接，不提问、不切换模式。"
        "适用于：DeepSeek 回复很长、想让对方直接看完整内容时，把链接发出去。"
        "conversation 参数控制取哪个会话：空（默认）取当前对话；传历史会话精确标题则"
        "进入该会话再取（标题用 deepseek_history list 获取，未命中报错）；"
        "传 __new__ 开新对话（空会话，一般不用）。返回链接给模型，是否发送由你按场景决定。"
    )

    async def execute(
        self,
        conversation: Annotated[str, "对话定位：空（默认）取当前对话；历史会话精确标题则进入该会话再取（用 deepseek_history list 获取标题，未命中报错）；'__new__' 开新对话"] = "",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：定位会话并直接获取分享链接。

        Args:
            conversation: 对话定位（空当前 / 精确标题进入 / __new__ 新建）。

        Returns:
            tuple[bool, str | dict]: (是否成功, 结果或错误)。
        """
        config = self.plugin.config if isinstance(self.plugin.config, AiUiSnapshotConfig) else None
        if config is None:
            return False, "插件配置缺失，无法执行"

        stream_id = self.get_current_stream_id()
        result: AskResult = await create_share(
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
            "summary": "已生成 DeepSeek 官方分享链接，是否发送给用户由你决定。conversation 为当前对话标题。",
        }


# 供插件装配导出的工具类列表
SNAPSHOT_TOOLS: list[type[BaseTool]] = [
    AskAiAndSnapshotTool,
    DeepseekSnapshotTool,
    DeepseekShareTool,
]
