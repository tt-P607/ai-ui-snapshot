"""LLM 工具共享基类。

提供各站点工具复用的能力：浏览器会话获取、media_retriever 已下载文件路径解析。
工具组件统一继承 :class:`_ToolBase`，避免各站点工具重复实现会话与文件解析样板。
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseTool

logger = get_logger("ai_ui_snapshot.tool_base")


@runtime_checkable
class _DownloadedFileResolver(Protocol):
    """media_retriever Service 已下载文件解析能力的形状声明。"""

    def resolve_downloaded_file(self, stream_id: str, file_name: str) -> str | None: ...


class _ToolBase(BaseTool):
    """站点无关的媒体解析工具基类。"""

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
            if not isinstance(service, _DownloadedFileResolver):
                logger.warning("未找到 media_retriever 已下载文件解析能力")
                return None
            result = service.resolve_downloaded_file(stream_id, file_name)
            return str(result) if result else None
        except Exception as exc:  # noqa: BLE001 - 服务不可用时降级
            logger.warning(f"解析 media_retriever 已下载文件失败: {exc}")
            return None

    @staticmethod
    def _split_media_ids(raw_ids: str) -> list[str]:
        """拆分 media_id 串（支持英文/中文逗号、顿号、空白分隔）。

        Args:
            raw_ids: 一个或多个 media_id 组成的字符串。

        Returns:
            list[str]: 去重后的 media_id 列表（保持原顺序）。
        """
        parts = [p.strip() for p in re.split(r"[,，、\s]+", raw_ids or "") if p.strip()]
        seen: set[str] = set()
        ordered: list[str] = []
        for part in parts:
            if part not in seen:
                seen.add(part)
                ordered.append(part)
        return ordered

    async def _resolve_media_ids(self, raw_ids: str) -> tuple[list[str], str]:
        """把 media_id 串（可含多个）解析为本地文件路径列表。

        Args:
            raw_ids: 一个或多个 media_id（逗号/顿号/空格分隔）。

        Returns:
            tuple[list[str], str]: (本地路径列表, 错误信息或空字符串)。
        """
        from ..services.service import resolve_media_path

        paths: list[str] = []
        for media_id in self._split_media_ids(raw_ids):
            path = await resolve_media_path(media_id)
            if not path:
                return [], f"无法解析媒体 media_id: {media_id}"
            paths.append(path)
        return paths, ""
