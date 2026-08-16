"""LLM 工具共享基类。

提供各站点工具复用的能力：浏览器会话获取、media_retriever 已下载文件路径解析。
工具组件统一继承 :class:`_ToolBase`，避免各站点工具重复实现会话与文件解析样板。
"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseTool

from ..services.base.browser_session import get_manager
from ..services.deepseek.actions import BrowserActions

logger = get_logger("ai_ui_snapshot.tool_base")


class _ToolBase(BaseTool):
    """浏览器工具基类：获取共享会话并构造动作对象。"""

    async def _actions(self) -> BrowserActions:
        """获取当前 stream 的 DeepSeek 浏览器会话动作对象。

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
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )

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
