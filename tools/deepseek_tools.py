"""LLM 工具集：DeepSeek 提问 / 历史会话 / 状态查询 / 截图 / 分享链接。

把 DeepSeek 网页操作封装为五个高层工具，bot 通过参数使用，无需逐步操控浏览器：
- ask_ai_and_snapshot：真实提问，返回回复文本（内部消化转述）。
- deepseek_snapshot：直接截取当前/指定对话界面为长截图并发送，不提问。
- deepseek_share：直接获取当前/指定对话的官方分享链接，不提问。
- deepseek_history：列出历史会话 / 进入指定会话（返回完整上下文 + 开关状态）。
- deepseek_state：查询当前对话模式与深度思考/联网搜索开关状态。

工具共用服务层（service）的提问 / 截图 / 分享 / 历史入口。
"""

from __future__ import annotations

from typing import Annotated, Any

from src.app.plugin_system.api import send_api
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseTool

from ..config import AiUiSnapshotConfig
from ..services.base.browser_session import get_manager
from ..services.service import (
    AskResult,
    ask_deepseek,
    capture_snapshot,
    create_share,
    resolve_media_path,
    strip_data_uri_prefix,
)
from .base import _ToolBase

logger = get_logger("ai_ui_snapshot.tool")


class AskAiAndSnapshotTool(_ToolBase):
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
        new_chat: Annotated[bool, "是否先开一个新对话再提问（等价 conversation=__new__）"] = False,
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
            new_chat: 是否先开新对话（等价 conversation=__new__）。
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


class DeepseekSnapshotTool(_ToolBase):
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
                processed_plain_text="[DeepSeek 界面截图]",
            )
            if not sent:
                return False, "截图已生成但发送失败"
        return True, {
            "sent": True,
            "conversation": result.conversation,
            "summary": "已截取 DeepSeek 对话界面并发出，请用拟人口吻简单引述即可。conversation 为当前对话标题。",
        }


class DeepseekShareTool(_ToolBase):
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


class DeepseekHistoryTool(_ToolBase):
    """DeepSeek 历史会话：列出 / 进入。"""

    name: str = "deepseek_history"
    description: str = (
        "操作 DeepSeek 的历史会话，像真人翻看之前的对话。"
        "action=list 列出侧边栏的历史会话标题；action=open 需提供 title 进入该会话，"
        "进入后工具会返回该会话的完整上下文与当前开关状态，之后可用 "
        "ask_ai_and_snapshot 继续在这个会话里对话。"
    )

    async def execute(
        self,
        action: Annotated[str, "操作：list（列出历史会话）/ open（进入指定会话）"],
        title: Annotated[str, "action=open 时的历史会话标题（list 返回的标题）"] = "",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行历史会话操作。

        Args:
            action: list / open。
            title: open 时的会话标题。

        Returns:
            tuple[bool, str | dict]: (是否成功, 结果或错误)。
        """
        try:
            actions = await self._actions()
        except Exception as exc:  # noqa: BLE001
            return False, f"浏览器会话不可用: {exc}"
        if action == "list":
            items = await actions.list_conversations()
            return True, {"histories": items}
        if action == "open":
            if not title:
                return False, "open 操作需提供 title"
            ok = await actions.open_conversation(title)
            if not ok:
                return False, f"未找到历史会话: {title}"
            context = await actions.get_conversation_text(scope="full")
            mode = await actions.get_mode()
            toggles = await actions.get_toggles()
            # 进入历史会话后，锁定该会话原本的模式，后续提问沿用（不可切换）
            stream_id = self.get_current_stream_id()
            current_id = await actions.get_active_conversation_id()
            current_title = await actions.get_active_conversation_title()
            if mode and current_id:
                get_manager().lock_conversation_mode(stream_id, current_id, mode, current_title or title)
            else:
                get_manager().set_active_conversation(stream_id, current_id, current_title or title)
            return True, {
                "opened": title,
                "mode": mode or "未知",
                "toggles": toggles,
                "locked_mode": mode or "未知",
                "conversation": current_title or title,
                "context": context,
                "tip": "已进入该会话（模式已锁定，不可切换），可用 ask_ai_and_snapshot 继续对话。conversation 字段为当前对话标题，后续追问可传同一标题。",
            }
        return False, f"未知操作: {action}（可选 list/open）"


class DeepseekStateTool(_ToolBase):
    """查询 DeepSeek 当前模式与开关状态。"""

    name: str = "deepseek_state"
    description: str = (
        "查询当前 DeepSeek 对话的模式（快速/专家/识图）以及深度思考、联网搜索"
        "开关是否开启。提问前调用可确认当前配置，或在需要时参考。"
    )

    async def execute(self) -> tuple[bool, str | dict[str, Any]]:
        """执行状态查询。

        Returns:
            tuple[bool, str | dict]: (是否成功, 模式/开关/锁定状态)。
        """
        try:
            actions = await self._actions()
        except Exception as exc:  # noqa: BLE001
            return False, f"浏览器会话不可用: {exc}"
        stream_id = self.get_current_stream_id()
        mode = await actions.get_mode()
        toggles = await actions.get_toggles()
        locked = get_manager().get_locked_mode(stream_id)
        return True, {
            "mode": mode or "未知",
            "locked_mode": locked or "未锁定",
            "tip": "会话模式一经选定即锁定，换模式需开新对话。",
            **toggles,
        }


# 供插件装配导出的 DeepSeek 工具类列表
DEEPSEEK_TOOLS: list[type[BaseTool]] = [
    AskAiAndSnapshotTool,
    DeepseekSnapshotTool,
    DeepseekShareTool,
    DeepseekHistoryTool,
    DeepseekStateTool,
]
