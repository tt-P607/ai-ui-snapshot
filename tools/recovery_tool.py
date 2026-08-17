"""LLM 工具：浏览器会话重置（恢复卡死/异常）。

供 AI 在遇到网页会话卡死、页面崩溃或无响应时主动调用，销毁指定站点的
浏览器会话；下一次提问/截图工具调用时由会话管理器自动重建全新会话。
仅重置页面运行状态，登录态与云端历史对话均保留。
"""

from __future__ import annotations

from typing import Annotated

from ..services.base.browser_session import get_manager
from .base import _ToolBase

# 可重置的站点主题归一化映射（含别名）
_SITE_ALIASES: dict[str, str] = {
    "deepseek": "deepseek",
    "ds": "deepseek",
    "gemini": "gemini",
    "google": "gemini",
}


class ResetBrowserTool(_ToolBase):
    """重置（关闭并重建）指定站点的浏览器会话。"""

    name: str = "reset_browser"
    description: str = (
        "关闭并重建指定站点的浏览器会话（相当于关掉浏览器重开，进入该站点主页）。"
        "适用于网页会话卡死、页面崩溃、无响应或连续操作异常时：调用后该站点下次"
        "提问/截图会从主页重新开始，登录态与云端历史对话都保留，可照常继续使用。"
        "注意：仅重置页面运行状态，不会丢失任何已正常完成的历史对话。"
        "请在确实遇到卡死/异常时使用，正常流程无需调用。"
    )

    async def execute(
        self,
        site: Annotated[str, "要重置的站点：deepseek / gemini（接受别名 ds / google）"] = "deepseek",
        reason: Annotated[str, "重置原因说明（仅作日志记录）"] = "",
    ) -> tuple[bool, str]:
        """执行：关闭指定站点当前流的浏览器会话。

        Args:
            site: 站点主题（deepseek/gemini，接受别名）。
            reason: 重置原因（仅作日志记录）。

        Returns:
            tuple[bool, str]: (是否成功, 结果或错误)。
        """
        theme = _SITE_ALIASES.get((site or "").strip().lower(), "")
        if not theme:
            return False, f"未知站点: {site}（可选 deepseek / gemini）"

        stream_id = self.get_current_stream_id()
        manager = get_manager()
        try:
            await manager.close(stream_id, theme=theme)
        except Exception as exc:  # noqa: BLE001 - 关闭异常不影响后续重建
            return False, f"重置 {theme} 浏览器会话失败: {exc}"

        return True, (
            f"已重置 {theme} 浏览器会话（原因: {reason or '未说明'}）。"
            "下一次该站点提问/截图会自动重建浏览器并回到主页；"
            "登录态与历史对话均保留，请照常继续使用。"
        )


# 恢复工具列表（供插件装配；DeepSeek/Gemini 任一启用即注册）
RECOVERY_TOOLS: list[type] = [ResetBrowserTool]