# AI UI Snapshot

拟人化使用 DeepSeek 的 Neo-MoFox 插件：用 bot 专属账号的登录态驱动真实 DeepSeek 网页，通过封装好的高层工具让 bot 像真人一样使用 DeepSeek，UI 完全真实。

## 功能

- **一键提问**（`ask_ai_and_snapshot`）：向 DeepSeek 真实提问，返回回复内容供自然转述。
- **直接截图**（`deepseek_snapshot`）：直接截取当前/指定 DeepSeek 对话界面为长截图并发送，不提问、不切换模式。
- **分享链接**（`deepseek_share`）：直接获取当前/指定对话的官方公开分享链接，不提问、不切换模式。
- **历史会话**（`deepseek_history`）：列出历史会话 / 进入指定会话继续对话。
- **状态查询**（`deepseek_state`）：查询当前对话模式与深度思考/联网搜索开关状态。
- **快捷命令**（`/ask`）：显式驱动真实 DeepSeek 网页提问并截图。
- **模式支持**：快速模式 / 专家模式 / 识图模式，每个对话模式一经选定即锁定。
- **开关控制**：深度思考、联网搜索。
- **附带文件**：图片（media_id）或已下载文件一起提问（识图模式真正理解图片，快速模式 OCR）。
- **对话定位**（`conversation` 参数）：空沿用当前对话、精确标题进入历史会话（未命中则新建）、`__new__` 强制新建。
- **长截图**：完整对话长图，思考块默认收起，超长自动分片；截图顶部叠加 1:1 Chromium 矢量浏览器外壳（含动态对话标题、明暗主题自适应、真实 Google 账号头像）。
- **分享链接**：直接获取当前/指定对话的公开分享链接（`deepseek_share`）。

## 依赖

- Python `>=3.11`
- Neo-MoFox 框架（`src.app.plugin_system.api.*`）
- `playwright`（插件会自动安装）

## 安装

将 `plugins/ai_ui_snapshot/` 目录放入 Neo-MoFox 的 `plugins/` 下，重启应用即可自动加载。

## 使用

先运行 `scripts/capture_real_ui.py` 用 bot 专属账号完成一次登录（登录态持久化到 `data/ai_ui_snapshot_profile/`），之后插件即复用该登录态驱动真实网页。

命令 / 工具示例：

```
/ask 用通俗的语言解释什么是量子纠缠
/ask -m 专家 -c- 换一个专家模式的新对话来聊这个问题
```

## 配置

见 `config/plugins/ai_ui_snapshot/config.toml`（插件加载后自动生成）：网页实时模式、截图渲染、浏览器外壳装饰（开关 / 主题 / 头像）、图片上传等。
