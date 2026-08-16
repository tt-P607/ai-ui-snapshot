"""站点无关通用工具。

提供供各站点动作层复用的纯工具函数：data URI 编码、主题归一化与
自动主题解析、浏览器外壳配色归一化。
"""

from __future__ import annotations

import base64
import time

# 合法主题取值（auto 按本地时间解析为 light/dark）
_VALID_THEMES: frozenset[str] = frozenset({"auto", "light", "dark"})


def data_uri(data: bytes) -> str:
    """将 PNG 字节流转为 data URI。

    Args:
        data: PNG 字节流。

    Returns:
        str: data URI。
    """
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def normalize_theme(theme: str) -> str:
    """归一化主题为 auto/light/dark。

    Args:
        theme: 原始输入（auto/light/dark，大小写不敏感）。

    Returns:
        str: auto/light/dark 之一；无法识别时回退 auto。
    """
    value = (theme or "").strip().lower()
    return value if value in _VALID_THEMES else "auto"


def resolve_auto_theme() -> str:
    """按本地时间解析 auto 主题：18:00-06:00 为深色，其余浅色。

    Returns:
        str: light / dark。
    """
    hour = time.localtime().tm_hour
    return "dark" if hour >= 18 or hour < 6 else "light"
