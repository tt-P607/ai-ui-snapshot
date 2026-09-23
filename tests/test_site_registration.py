"""站点配置与组件装配测试。"""

from __future__ import annotations

import pathlib
import sys

import pytest

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins.ai_ui_snapshot.config import AiUiSnapshotConfig  # noqa: E402
from plugins.ai_ui_snapshot.plugin import AiUiSnapshotPlugin  # noqa: E402
from plugins.ai_ui_snapshot.services.sites import SITE_NAMES, site_enabled  # noqa: E402
from plugins.ai_ui_snapshot.tools.deepseek_tools import DEEPSEEK_TOOLS  # noqa: E402
from plugins.ai_ui_snapshot.tools.doubao_tools import DOUBAO_TOOLS  # noqa: E402
from plugins.ai_ui_snapshot.tools.gemini_tools import GEMINI_TOOLS  # noqa: E402


@pytest.mark.parametrize("site,expected", [
    ("deepseek", DEEPSEEK_TOOLS),
    ("doubao", DOUBAO_TOOLS),
    ("gemini", GEMINI_TOOLS),
])
def test_site_registration_tracks_enabled_config(site: str, expected: list[type]) -> None:
    """各站点单独开启时只装配该站点工具，识图也使用同一开关。"""
    config = AiUiSnapshotConfig()
    for name in SITE_NAMES:
        setattr(config.sites, name, name == site)
    config.recognize.enabled = True
    config.recognize.site = site

    components = AiUiSnapshotPlugin(config).get_components()

    assert all(tool in components for tool in expected)
    assert all(tool not in components for name, tools in (
        ("deepseek", DEEPSEEK_TOOLS), ("doubao", DOUBAO_TOOLS), ("gemini", GEMINI_TOOLS)
    ) if name != site for tool in tools)
    assert site_enabled(config, site)
    assert not site_enabled(config, "unknown")

    setattr(config.sites, site, False)
    assert not site_enabled(config, site)
    assert AiUiSnapshotPlugin(config).get_components() == []


def test_site_registry_matches_config_fields() -> None:
    """新增站点时配置字段与站点列表必须同步。"""
    assert set(SITE_NAMES) == set(AiUiSnapshotConfig.SitesSection.model_fields)