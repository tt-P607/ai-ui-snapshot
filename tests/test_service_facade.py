"""三站服务兼容导出与单例媒体锁测试。"""

from __future__ import annotations

import pathlib
import sys

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins.ai_ui_snapshot.services import service  # noqa: E402
from plugins.ai_ui_snapshot.services.base import shared  # noqa: E402
from plugins.ai_ui_snapshot.services.deepseek import service as deepseek  # noqa: E402
from plugins.ai_ui_snapshot.services.doubao import service as doubao  # noqa: E402
from plugins.ai_ui_snapshot.services.gemini import service as gemini  # noqa: E402


def test_facade_exports_site_services_without_duplicate_implementations() -> None:
    """旧入口与新站点服务使用同一函数与结果类型。"""
    assert service.AskResult is shared.AskResult
    assert service.ask_deepseek is deepseek.ask_deepseek
    assert service.capture_snapshot is deepseek.capture_snapshot
    assert service.create_share is deepseek.create_share
    assert service.ask_gemini is gemini.ask_gemini
    assert service.capture_gemini_snapshot is gemini.capture_gemini_snapshot
    assert service.generate_gemini_image is gemini.generate_gemini_image
    assert service.ask_doubao is doubao.ask_doubao
    assert service.capture_doubao_snapshot is doubao.capture_doubao_snapshot
    assert service.submit_doubao_video is doubao.submit_doubao_video
    assert service.strip_data_uri_prefix is shared.strip_data_uri_prefix


def test_doubao_media_lock_has_single_owner() -> None:
    """生图与生视频共用同一个豆包站点媒体锁。"""
    assert doubao.generate_doubao_image.__globals__["_doubao_media_lock"] is doubao._doubao_media_lock
    assert doubao.submit_doubao_video.__globals__["_doubao_media_lock"] is doubao._doubao_media_lock