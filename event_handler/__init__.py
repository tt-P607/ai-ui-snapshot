"""事件处理器组件集合。

对外暴露本插件订阅系统事件的全部处理器类，供插件装配使用。
"""

from __future__ import annotations

from .media_recognize import ImageRecognizeHandler

# 供插件装配导出的事件处理器类列表
EVENT_HANDLERS: list[type] = [ImageRecognizeHandler]

__all__ = ["EVENT_HANDLERS", "ImageRecognizeHandler"]
