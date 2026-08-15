"""AI UI 仿真截图插件配置定义。

配置内容：
- plugin：插件启用开关
- web：网页实时模式（默认站点、持久化会话目录、回复/空闲超时、无头）
- screenshot：浏览器视口与渲染参数
- upload：向网页上传图片的参数（来源为聊天媒体 media_id）
"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class AiUiSnapshotConfig(BaseConfig):
    """AI UI 仿真截图插件配置。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "AI UI 仿真截图插件配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """插件基本配置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用插件",
            label="启用插件",
            tag="plugin",
            order=0,
        )

    @config_section("web", title="网页实时模式", tag="ai")
    class WebSection(SectionBase):
        """网页实时提问配置。"""

        default_theme: str = Field(
            default="deepseek",
            description="默认站点主题（当前仅支持 deepseek），用于定位登录态目录",
            label="默认主题",
            tag="ai",
            order=0,
        )
        url: str = Field(
            default="https://chat.deepseek.com/",
            description="浏览器打开的目标网址（留空使用主题默认地址）",
            label="目标网址",
            tag="ai",
            order=1,
        )
        web_profile_dir: str = Field(
            default="data/ai_ui_snapshot_profile",
            description="持久化浏览器会话目录（含 bot 登录态，本地不发布）",
            label="网页会话目录",
            tag="ai",
            order=2,
        )
        reply_timeout: int = Field(
            default=240,
            description="等待 AI 回复完成的超时秒数",
            label="回复超时",
            tag="ai",
            order=3,
        )
        idle_timeout: int = Field(
            default=600,
            description="任务级浏览器空闲自动关闭秒数（0 表示不自动关闭）",
            label="空闲超时",
            tag="ai",
            order=4,
        )
        headless: bool = Field(
            default=True,
            description="浏览器是否无头运行（True 无界面，节省资源）",
            label="无头模式",
            tag="ai",
            order=5,
        )

    @config_section("decoration", title="浏览器外壳装饰", tag="advanced")
    class DecorationSection(SectionBase):
        """浏览器外壳装饰参数（截图顶部模拟浏览器标签页/地址栏）。"""

        enabled: bool = Field(
            default=True,
            description="截图时是否在顶部叠加浏览器外壳装饰（真实标题/图标/URL + 原生风格顶栏）",
            label="启用装饰",
            tag="advanced",
            order=0,
        )
        theme: str = Field(
            default="auto",
            description="外壳配色：auto（跟随页面深/浅色，默认）/ light / dark",
            label="外壳配色",
            tag="advanced",
            order=1,
        )
        avatar_url: str = Field(
            default="",
            description="Google 账号自定义头像 URL（留空则默认使用 Google AI Pro 专属彩虹流光头像）",
            label="头像 URL",
            tag="advanced",
            order=2,
        )

    @config_section("screenshot", title="截图渲染", tag="advanced")
    class ScreenshotSection(SectionBase):
        """截图渲染参数。"""

        width: int = Field(
            default=1440,
            description="浏览器视口宽度（像素）",
            label="视口宽度",
            tag="advanced",
            order=0,
        )
        height: int = Field(
            default=900,
            description="浏览器视口高度（像素）",
            label="视口高度",
            tag="advanced",
            order=1,
        )
        device_scale_factor: int = Field(
            default=2,
            description="高清渲染倍率（2 表示 2x Retina）",
            label="渲染倍率",
            tag="advanced",
            order=2,
        )
        max_height: int = Field(
            default=8000,
            description="长截图单张最大高度（像素），超出则按此高度分片截取，避免超大图",
            label="长截图最大高度",
            tag="advanced",
            order=3,
        )
        browser_path: str = Field(
            default="",
            description="Chromium 可执行文件路径（留空使用 Playwright 自带浏览器）",
            label="浏览器路径",
            tag="advanced",
            order=4,
        )

    @config_section("upload", title="附件上传", tag="ai")
    class UploadSection(SectionBase):
        """向网页上传附件（图片/文档）的配置。"""

        enabled: bool = Field(
            default=True,
            description="是否允许通过 browser_upload 向网页上传附件（图片/文档）",
            label="启用上传",
            tag="ai",
            order=0,
        )
        max_size_mb: float = Field(
            default=10.0,
            description="上传附件最大大小（MB），超过拒绝上传",
            label="最大大小",
            tag="ai",
            order=1,
        )
        allowed_extensions: str = Field(
            default="png,jpg,jpeg,webp,gif,bmp,md,txt,pdf,doc,docx,xls,xlsx,csv,ppt,pptx,json,py",
            description="允许上传的附件扩展名（逗号分隔，小写；含图片与文档）",
            label="允许扩展名",
            tag="ai",
            order=2,
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    web: WebSection = Field(default_factory=WebSection)
    screenshot: ScreenshotSection = Field(default_factory=ScreenshotSection)
    decoration: DecorationSection = Field(default_factory=DecorationSection)
    upload: UploadSection = Field(default_factory=UploadSection)
