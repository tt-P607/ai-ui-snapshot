"""AI UI 仿真截图插件入口。

提供 `ai_ui_snapshot` 插件：让 Bot 像真人一样使用 DeepSeek、Gemini 与豆包。
插件用任务级临时 Playwright 浏览器（复用 bot 账号登录态）驱动真实网页，
通过封装好的高层工具（DeepSeek：ask_deepseek / deepseek_snapshot /
deepseek_share / deepseek_history / deepseek_state；Gemini：ask_gemini_ai /
gemini_generate_image / gemini_snapshot / gemini_share；豆包：ask_doubao /
doubao_snapshot / doubao_history / doubao_state / doubao_generate_image /
doubao_generate_video / doubao_send_video）操作，无需逐步操控浏览器。
"""

from __future__ import annotations

from src.app.plugin_system.api import log_api
from src.app.plugin_system.base import BasePlugin, register_plugin

from .config import AiUiSnapshotConfig
from .commands.ask_command import AiSnapshotCommand
from .event_handler import EVENT_HANDLERS
from .services.base import browser_session
from .tools.deepseek_tools import DEEPSEEK_TOOLS
from .tools.doubao_tools import DOUBAO_TOOLS
from .tools.gemini_tools import GEMINI_TOOLS
from .tools.recovery_tool import RECOVERY_TOOLS

logger = log_api.get_logger("ai_ui_snapshot")


@register_plugin
class AiUiSnapshotPlugin(BasePlugin):
    """AI UI 仿真截图插件。

    提供封装好的高层工具（DeepSeek / Gemini / 豆包三套能力）与快捷命令
    （/ask），通过任务级临时浏览器驱动真实网页，Bot 以参数方式使用所有能力。
    """

    plugin_name: str = "ai_ui_snapshot"

    configs = [AiUiSnapshotConfig]

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

        按 ``[sites]`` 各站点开关装配：站点关闭时其工具不注册；/ask 命令在
        任一站点启用时注册（命令内部按旗标路由到各站点）；恢复工具同理
        （可重置任意站点，独立于具体站点开关）。
        识图接管处理器（``[recognize]``）仅在「接管开启且识图站点已启用」时
        注册，避免注册了又必然走不通。

        Returns:
            组件类列表（各站点细粒度工具 + 识图处理器 + 命令 + 恢复工具）。
        """
        config = self.config
        if isinstance(config, AiUiSnapshotConfig) and not config.plugin.enabled:
            return []
        if not isinstance(config, AiUiSnapshotConfig):
            return [
                *DEEPSEEK_TOOLS,
                *GEMINI_TOOLS,
                *DOUBAO_TOOLS,
                *RECOVERY_TOOLS,
                *EVENT_HANDLERS,
                AiSnapshotCommand,
            ]
        components: list[type] = []
        any_site = False
        if config.sites.deepseek:
            components.extend(DEEPSEEK_TOOLS)
            any_site = True
        if config.sites.gemini:
            components.extend(GEMINI_TOOLS)
            any_site = True
        if config.sites.doubao:
            components.extend(DOUBAO_TOOLS)
            any_site = True
        # 识图接管：需开启接管且所配站点已启用
        recognize_site = (config.recognize.site or "").strip().lower()
        if config.recognize.enabled and getattr(config.sites, recognize_site, False):
            components.extend(EVENT_HANDLERS)
        # /ask 命令与恢复工具随任一站点启用注册，不绑定具体站点
        if any_site:
            components.extend([*RECOVERY_TOOLS, AiSnapshotCommand])
        return components
