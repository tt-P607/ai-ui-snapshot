"""识图接管事件处理器：把框架的图片/表情包识别接到真实 AI 网页。

框架收到图片/表情包时会发布 ``on_media_recognize`` 事件询问识别结果，内置
VLM 引擎以 ``priority=0`` 兜底订阅。本处理器以更高 ``weight`` 先应答，用真实
网页（DeepSeek / 豆包 / Gemini，可配）识图并把描述回填给框架。

契约（与 ``src/core/managers/media_manager/recognition.py`` 一致）：

- ``engine == "vlm"`` 且 ``media_type`` 属于 image/emoji 时才处理。
- 成功：写入 ``description`` 并置 ``engine_processed=True``，返回 ``SUCCESS``；
  内置 VLM 处理器见到该标记会自行跳过。
- 失败：返回 ``PASS``（不传播任何改动），框架内置 VLM 照常兜底。

因此本处理器是「尽力而为」：站点未登录、超时、回复为空等情况都不会让
框架的识图能力变差。
"""

from __future__ import annotations

import base64
import pathlib
import tempfile
from typing import Any

from src.app.plugin_system.api.event_api import EventDecision
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import EventType

from ..config import AiUiSnapshotConfig
from ..services.service import (
    recognize_image_by_site,
    resolve_media_path,
    sniff_image_suffix,
)
from ..services.sites import SITE_NAMES, site_enabled

logger = get_logger("ai_ui_snapshot.media_recognize")

#: 媒体管理器在事件里使用的引擎取值（图片/表情包走 VLM 分支）
_ENGINE_VLM = "vlm"
#: 本处理器接管的媒体类型
_MEDIA_TYPES = ("image", "emoji")


class ImageRecognizeHandler(BaseEventHandler):
    """用真实 AI 网页接管框架的图片/表情包识别。"""

    name: str = "media_recognize"
    description: str = "用真实 DeepSeek/豆包/Gemini 网页接管框架图片识别"
    # 内置 VLM 兜底处理器为 priority=0，这里取正权重确保先应答
    weight: int = 100
    intercept_message: bool = False
    init_subscribe: list[EventType | str] = [EventType.ON_MEDIA_RECOGNIZE]
    # 识图要走真实网页（上传+等回复），远超事件总线默认的 30s 保护；
    # 超时由配置的 recognize.timeout 与各 ask 入口自身控制。
    timeout: float = 0

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """按事件契约尝试识图，失败则放行给框架内置 VLM。

        Args:
            event_name: 事件名称（``on_media_recognize``）。
            params: 事件参数（含 media_type / engine / base64_data 等）。

        Returns:
            tuple[EventDecision, dict[str, Any]]: 成功为 ``SUCCESS`` 且已回填
            ``description``；不处理或失败为 ``PASS``。
        """
        config = self.plugin.config
        if not isinstance(config, AiUiSnapshotConfig) or not config.recognize.enabled:
            return EventDecision.PASS, params

        # 只接管 VLM 分支的图片/表情包；语音（asr）与视频（video）不碰
        if params.get("engine") != _ENGINE_VLM:
            return EventDecision.PASS, params
        # 已被前序处理器处理过则让行
        if params.get("engine_processed") or params.get("skip_engine"):
            return EventDecision.PASS, params

        media_type = str(params.get("media_type") or "")
        if media_type not in _MEDIA_TYPES:
            return EventDecision.PASS, params
        if media_type == "emoji" and not config.recognize.emoji:
            return EventDecision.PASS, params

        site = (config.recognize.site or "").strip().lower()
        if site not in SITE_NAMES:
            logger.warning(f"识图站点配置无效: {config.recognize.site!r}，交回框架内置 VLM")
            return EventDecision.PASS, params
        if not site_enabled(config, site):
            logger.info(f"识图站点 {site} 未在 [sites] 启用，交回框架内置 VLM")
            return EventDecision.PASS, params

        image_path, cleanup = await self._locate_image(params)
        if not image_path:
            logger.warning("识图失败：取不到图片文件，交回框架内置 VLM")
            return EventDecision.PASS, params

        media_hash = str(params.get("media_hash") or "")
        stream_id = str(params.get("stream_id") or "")
        try:
            logger.info(f"开始识图（{site}）: {media_hash[:8]}... 类型={media_type}")
            description = await recognize_image_by_site(
                image_path,
                site=site,
                media_type=media_type,
                stream_id=stream_id,
                timeout_s=config.recognize.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - 任何异常都交回框架兜底
            logger.warning(f"识图异常（{site}）: {media_hash[:8]}... {exc}")
            description = ""
        finally:
            if cleanup:
                cleanup()

        if not description:
            logger.warning(f"识图未取得描述（{site}）: {media_hash[:8]}...，交回框架内置 VLM")
            return EventDecision.PASS, params

        logger.info(f"识图完成（{site}）: {media_hash[:8]}... → {description[:60]}...")
        params["description"] = description
        params["engine_processed"] = True
        return EventDecision.SUCCESS, params

    @staticmethod
    async def _locate_image(
        params: dict[str, Any],
    ) -> tuple[str, Any]:
        """取得待识别图片的本地路径。

        优先用媒体管理器已落盘的文件（框架在发布识别事件前就已存盘）；
        取不到时把事件里的 base64 落成临时文件，并返回清理函数。

        Args:
            params: 事件参数。

        Returns:
            tuple[str, Any]: (本地图片路径或空串, 清理回调或 None)。
        """
        media_hash = str(params.get("media_hash") or "")
        if media_hash:
            try:
                cached_path = await resolve_media_path(media_hash)
            except Exception as exc:  # noqa: BLE001 - 查询失败则退回临时文件
                logger.debug(f"查询媒体落盘路径失败: {exc}")
                cached_path = None
            if cached_path and pathlib.Path(cached_path).is_file():
                return cached_path, None

        raw_b64 = params.get("base64_data")
        if not isinstance(raw_b64, str) or not raw_b64:
            return "", None
        try:
            raw = base64.b64decode(raw_b64, validate=False)
        except Exception:  # noqa: BLE001 - base64 损坏
            return "", None
        if not raw:
            return "", None
        suffix = sniff_image_suffix(raw)
        tmp = tempfile.NamedTemporaryFile(prefix="aiui_recognize_", suffix=f".{suffix}", delete=False)
        tmp.write(raw)
        tmp.close()
        tmp_path = tmp.name

        def _cleanup() -> None:
            """删除临时图片文件。"""
            try:
                pathlib.Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass

        return tmp_path, _cleanup
