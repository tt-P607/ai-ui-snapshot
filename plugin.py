"""AI UI 仿真截图插件入口。

提供 `ai_ui_snapshot` 插件：让 Bot 像真人一样使用 DeepSeek 与 Gemini。
插件用任务级临时 Playwright 浏览器（复用 bot 账号登录态）驱动真实网页，
通过封装好的高层工具（DeepSeek：ask_deepseek / deepseek_snapshot /
deepseek_share / deepseek_history / deepseek_state；Gemini：ask_gemini_ai /
gemini_generate_image / gemini_share）操作，无需逐步操控浏览器。
"""

from __future__ import annotations

from src.app.plugin_system.api import log_api
from src.app.plugin_system.base import BasePlugin, register_plugin

from .config import AiUiSnapshotConfig
from .commands.ask_command import AiSnapshotCommand
from .services.base import browser_session
from .tools.deepseek_tools import DEEPSEEK_TOOLS
from .tools.gemini_tools import GEMINI_TOOLS

logger = log_api.get_logger("ai_ui_snapshot")


@register_plugin
class AiUiSnapshotPlugin(BasePlugin):
    """AI UI 仿真截图插件。

    提供封装好的高层工具（DeepSeek 与 Gemini 两套能力）与快捷命令（/ask），
    通过任务级临时浏览器驱动真实网页，Bot 以参数方式使用所有能力。
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
                idle_timeout_s=config.web.idle_timeout,
                headless=config.web.headless,
                browser_path=config.screenshot.browser_path,
                proxy_url=config.web.proxy_url,
                page_theme=config.web.theme,
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

        按 ``[sites]`` 各站点开关装配：DeepSeek 关闭时其工具与 /ask 命令不注册，
        Gemini 关闭时其工具不注册（默认 deepseek 开、gemini 关）。

        Returns:
            组件类列表（细粒度工具 + 一键提问工具 + 命令）。
        """
        config = self.config
        if isinstance(config, AiUiSnapshotConfig) and not config.plugin.enabled:
            return []
        if not isinstance(config, AiUiSnapshotConfig):
            return [*DEEPSEEK_TOOLS, *GEMINI_TOOLS, AiSnapshotCommand]
        components: list[type] = []
        if config.sites.deepseek:
            components.extend([*DEEPSEEK_TOOLS, AiSnapshotCommand])
        if config.sites.gemini:
            components.extend(GEMINI_TOOLS)
        return components
