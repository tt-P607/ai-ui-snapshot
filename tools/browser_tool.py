"""LLM 封装工具集：DeepSeek 历史会话与状态查询。

把 DeepSeek 网页操作封装为高层工具，bot 通过参数使用，无需逐步操控浏览器：
- deepseek_history：列出历史会话 / 进入指定会话（返回完整上下文 + 开关状态），
  进入后可继续用 ask_ai_and_snapshot 对话。
- deepseek_state：查询当前对话模式与深度思考/联网搜索开关状态。

底层细粒度操作（读/点/输入/上传/截图等）封装在 BrowserActions 服务层内部，
不对 LLM 暴露。
"""

from __future__ import annotations

from typing import Annotated, Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseTool

from ..services.browser_actions import BrowserActions
from ..services.browser_session import get_manager

logger = get_logger("ai_ui_snapshot.browser_tool")


class _BrowserToolBase(BaseTool):
    """浏览器工具基类：获取共享会话并构造动作对象。"""

    async def _actions(self) -> BrowserActions:
        """获取当前 stream 的浏览器会话动作对象。

        Returns:
            BrowserActions: 页面动作封装。

        Raises:
            RuntimeError: 浏览器会话创建失败。
        """
        stream_id = self.get_current_stream_id()
        manager = get_manager()
        session = await manager.get(stream_id)
        manager.touch(stream_id)
        return BrowserActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
        )


class DeepseekHistoryTool(_BrowserToolBase):
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


class DeepseekStateTool(_BrowserToolBase):
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


# 供插件装配导出的封装工具类列表（不含细粒度 browser_* 工具）
BROWSER_TOOLS: list[type[BaseTool]] = [
    DeepseekHistoryTool,
    DeepseekStateTool,
]
