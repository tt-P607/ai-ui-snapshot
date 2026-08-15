"""AI UI 仿真截图插件入口。

提供 `ai_ui_snapshot` 插件：让 Bot 像真人一样使用 DeepSeek。插件用任务级
临时 Playwright 浏览器（复用 bot 账号登录态）驱动真实 DeepSeek 网页，
通过封装好的高层工具（ask_ai_and_snapshot 提问、deepseek_snapshot 截图、
deepseek_share 分享链接、deepseek_history 历史会话、deepseek_state 状态查询）
操作，无需逐步操控浏览器。
"""

from __future__ import annotations

from src.app.plugin_system.api import log_api
from src.app.plugin_system.base import BasePlugin, register_plugin

from .config import AiUiSnapshotConfig
from .commands.ask_command import AiSnapshotCommand
from .services import browser_session
from .tools.browser_tool import BROWSER_TOOLS
from .tools.snapshot_tool import SNAPSHOT_TOOLS

logger = log_api.get_logger("ai_ui_snapshot")


@register_plugin
class AiUiSnapshotPlugin(BasePlugin):
    """AI UI 仿真截图插件。

    提供封装好的高层工具（ask_ai_and_snapshot / deepseek_snapshot /
    deepseek_share / deepseek_history / deepseek_state）与快捷命令（/ask），
    通过任务级临时浏览器驱动真实 DeepSeek 网页，Bot 以参数方式使用所有能力。
    """

    plugin_name: str = "ai_ui_snapshot"

    configs: list[type] = [AiUiSnapshotConfig]

    dependent_components: list[str] = []

    async def on_plugin_loaded(self) -> None:
        """插件加载时初始化共享浏览器会话管理器。"""
        config = self.config
        if isinstance(config, AiUiSnapshotConfig) and config.plugin.enabled:
            browser_session.init_manager(
                profile_root=config.web.web_profile_dir,
                theme=config.web.default_theme,
                url=config.web.url if config.web.url else "https://chat.deepseek.com/",
                idle_timeout_s=config.web.idle_timeout,
                headless=config.web.headless,
                browser_path=config.screenshot.browser_path,
                viewport_width=config.screenshot.width,
                viewport_height=config.screenshot.height,
                device_scale_factor=config.screenshot.device_scale_factor,
                max_screenshot_height=config.screenshot.max_height,
                decoration_enabled=config.decoration.enabled,
                decoration_theme=config.decoration.theme,
                decoration_avatar_url=config.decoration.avatar_url,
            )

    async def on_plugin_unload(self) -> None:
        """插件卸载时关闭所有浏览器会话。"""
        await browser_session.close_all_sessions()

    def get_components(self) -> list[type]:
        """返回当前插件包含的组件。

        Returns:
            组件类列表（细粒度工具 + 一键提问工具 + 命令）。
        """
        config = self.config
        if isinstance(config, AiUiSnapshotConfig) and not config.plugin.enabled:
            return []
        return [*BROWSER_TOOLS, *SNAPSHOT_TOOLS, AiSnapshotCommand]
