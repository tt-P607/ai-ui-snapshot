"""站点开关与识图可用性的共享判断。"""

from __future__ import annotations

from ..config import AiUiSnapshotConfig

SITE_NAMES: tuple[str, ...] = tuple(AiUiSnapshotConfig.SitesSection.model_fields)


def site_enabled(config: AiUiSnapshotConfig, site: str) -> bool:
    """检查站点名称有效且在当前配置中启用。"""
    return site in SITE_NAMES and bool(getattr(config.sites, site))