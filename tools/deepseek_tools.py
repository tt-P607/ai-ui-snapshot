"""LLM 工具集：DeepSeek 提问 / 历史会话 / 状态查询 / 截图 / 分享链接。

把 DeepSeek 网页操作封装为五个高层工具，bot 通过参数使用，无需逐步操控浏览器：
- ask_deepseek：真实提问，返回回复文本（内部消化转述）。
- deepseek_snapshot：直接截取当前/指定对话界面为长截图并发送，不提问。
- deepseek_share：直接获取当前/指定对话的官方分享链接，不提问。
- deepseek_history：列出历史会话 / 进入指定会话（返回完整上下文 + 开关状态）。
- deepseek_state：查询当前对话的深度思考/智能搜索开关状态。

工具共用服务层（service）的提问 / 截图 / 分享 / 历史入口。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

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

    name: str = "ask_deepseek"
    description: str = (
        "向 DeepSeek 真实提问，像真人一样使用 DeepSeek，返回回复文本供你自然转述。"
        "可用于：①专业/深奥/复杂问题的转发与思考；②获取实时、不够新的信息"
        "（把 DeepSeek 当外部信息源，需要联网时开启 search）。"
        "可指定深度思考/联网搜索开关、附带图片（media_id）或已下载文件（file_name）"
        "一起提问、信息返回范围（last 最新回复 / full 整段对话）。"
        "conversation 参数控制对话定位：【持续对话规则】：若要在同一对话中持续交谈，"
        "必须显式传入该对话的精确标题（取自上次调用返回的 conversation 字段）；"
        "若不传该参数（留空），系统会自动开启一个全新对话，避免不同话题串台！"
        "深度思考与智能搜索默认开启。"
        "需要展示 DeepSeek 原始界面或长回复时，另用 deepseek_snapshot 截图、"
        "deepseek_share 取分享链接。"
    )

    async def execute(
        self,
        question: Annotated[str, "要提问的问题原文（完整、自然语言）"] = "",
        deepthink: Annotated[bool | None, "是否开启深度思考：true/false，默认 true"] = True,
        search: Annotated[bool | None, "是否开启联网搜索：true/false，默认 true"] = True,
        new_chat: Annotated[bool, "是否先开一个新对话再提问（等价 conversation=__new__）"] = False,
        conversation: Annotated[str, "对话定位：留空（默认）自动创建全新对话！若要在同一对话中持续聊，必须显式传入上次返回的 conversation 标题"] = "",
        image_id: Annotated[str, "附带提问的图片 media_id（聊天图片占位符 [图片(media_id)] 中的哈希），可空"] = "",
        file_name: Annotated[str, "附带提问的已下载文件名（media_retriever 已下载文件），可空"] = "",
        return_scope: Annotated[str, "信息返回范围：'last'（最新一条 AI 回复，默认）/ 'full'（整段对话）"] = "last",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：解析参数与上传路径，调用提问入口，返回回复文本。

        Args:
            question: 要提问的问题原文。
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
    """直接截取 DeepSeek 对话界面并发送，不提问。"""

    name: str = "deepseek_snapshot"
    description: str = (
        "截取 DeepSeek 对话界面并发送到当前聊天，不提问。"
        "默认截取正常视窗（人类可读的标准桌面窗口比例，带浏览器顶栏，聚焦最新回复）；"
        "若需要截取完整长回复或多轮历史问答，可传 scope='rounds' 并指定 rounds 参数"
        "（从后往前倒序完整截取最近 rounds 个回复及提问，例如 rounds=2 截取最后两轮）；"
        "scope='full' 为撑开整页长截图。调用后返回截断感知元数据，供你获知画面呈现内容。"
    )

    async def execute(
        self,
        conversation: Annotated[str, "对话定位：空（默认）截当前对话；历史会话精确标题则进入该会话再截（用 deepseek_history list 获取标题，未命中报错）；'__new__' 开新对话（空会话）"] = "",
        think: Annotated[str, "截图时思考过程块展开方式：'collapse'（折叠隐藏，默认）/ 'auto'（保持现状）/ 'expand'（强制展开）/ 'reveal'（仅被折叠时展开）"] = "collapse",
        sidebar: Annotated[str, "截图时左侧边栏显示方式：'auto'（保持现状，默认）/ 'show'（展开）/ 'hide'（收起隐藏）"] = "auto",
        scope: Annotated[Literal["viewport", "rounds", "full"], "截图范围：'viewport' 默认正常视窗比例（推荐）/ 'rounds' 按轮次完整截取 / 'full' 撑开整页长截图"] = "viewport",
        rounds: Annotated[int, "截取回复轮数（从后往前倒序，例如 2 表示截取最后 2 个完整回复及对应提问；默认 1，scope='rounds' 时生效）"] = 1,
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：定位会话并直接截图发送。

        Args:
            conversation: 对话定位（空当前 / 精确标题进入 / __new__ 新建）。
            think: 思考块展开方式。
            sidebar: 侧边栏显示方式。
            scope: 截图范围模式（viewport/rounds/full）。
            rounds: 截取的轮数。

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
            scope=scope,
            rounds=rounds,
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
        res: dict[str, Any] = {
            "sent": True,
            "conversation": result.conversation,
            "scope": scope,
            "rounds": rounds,
            "summary": "已截取 DeepSeek 对话界面并发出。请结合 snapshot_meta 了解画面展示内容并拟人化引述。",
        }
        if result.snapshot_meta:
            res["snapshot_meta"] = result.snapshot_meta
        return True, res


class DeepseekShareTool(_ToolBase):
    """直接获取 DeepSeek 当前/指定对话的官方公开分享链接，不提问。"""

    name: str = "deepseek_share"
    description: str = (
        "直接获取 DeepSeek 当前/指定对话的官方公开分享链接，不提问。"
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
        "ask_deepseek 继续在这个会话里对话。"
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
            toggles = await actions.get_toggles()
            stream_id = self.get_current_stream_id()
            current_id = await actions.get_active_conversation_id()
            current_title = await actions.get_active_conversation_title()
            get_manager().set_active_conversation(stream_id, current_id, current_title or title)
            return True, {
                "opened": title,
                "toggles": toggles,
                "conversation": current_title or title,
                "context": context,
                "tip": "已进入该会话，可用 ask_deepseek 继续对话。conversation 字段为当前对话标题，后续追问可传同一标题。",
            }
        return False, f"未知操作: {action}（可选 list/open）"


class DeepseekStateTool(_ToolBase):
    """查询 DeepSeek 当前对话的深度思考/智能搜索开关状态。"""

    name: str = "deepseek_state"
    description: str = (
        "查询当前 DeepSeek 对话的标题以及深度思考、智能搜索开关是否开启。"
        "提问前调用可确认当前设置，或在需要时参考。"
    )

    async def execute(self) -> tuple[bool, str | dict[str, Any]]:
        """执行状态查询。

        Returns:
            tuple[bool, str | dict]: (是否成功, 对话/开关状态)。
        """
        try:
            actions = await self._actions()
        except Exception as exc:  # noqa: BLE001
            return False, f"浏览器会话不可用: {exc}"
        toggles = await actions.get_toggles()
        title = await actions.get_active_conversation_title()
        return True, {
            "conversation": title or "未知",
            "tip": "深度思考与智能搜索是网页输入框上的开关，可在提问前调整。",
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
