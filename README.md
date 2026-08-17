# AI UI Snapshot

拟人化使用 DeepSeek 与 Gemini 的 Neo-MoFox 插件：用 bot 专属账号的登录态驱动真实 AI 网页，通过封装好的高层工具让 bot 像真人一样使用两大 AI 服务，UI 完全真实。

## 功能

### DeepSeek

- **一键提问**（`ask_deepseek`）：向 DeepSeek 真实提问，返回回复内容供自然转述。
- **直接截图**（`deepseek_snapshot`）：直接截取当前/指定 DeepSeek 对话界面为长截图并发送，不提问、不切换模式。
- **分享链接**（`deepseek_share`）：直接获取当前/指定对话的官方公开分享链接，不提问、不切换模式。
- **历史会话**（`deepseek_history`）：列出历史会话 / 进入指定会话继续对话。
- **状态查询**（`deepseek_state`）：查询当前对话模式与深度思考/联网搜索开关状态。
- **模式支持**：快速模式 / 专家模式 / 识图模式，每个对话模式一经选定即锁定。
- **开关控制**：深度思考、联网搜索。

### Gemini

- **一键提问**（`ask_gemini_ai`）：向 Gemini 真实提问（全模态上传图片/语音/视频/文档），返回回复文本。
- **生成图片**（`gemini_generate_image`）：用 Gemini 原生能力生成图片（可带 1 张/多张参考图改图/参考生成），自动发送到当前聊天。
- **直接截图**（`gemini_snapshot`）：直接截取当前/指定 Gemini 对话界面为长截图并发送，不提问、不改设置。
- **分享链接**（`gemini_share`）：直接获取当前/指定 Gemini 对话的官方公开分享链接，不提问。

### 通用

- **快捷命令**（`/ask`）：显式驱动真实 DeepSeek/Gemini 网页提问并截图（`-g` 切换 Gemini）。
- **会话重置**（`reset_browser`）：网页会话卡死、页面崩溃或无响应时，关闭并重建指定站点（`site` 参数：`deepseek` / `gemini`）的浏览器会话；相当于关掉浏览器重开进主页，登录态与云端历史对话都保留，可照常继续。
- **对话定位**（`conversation` 参数）：空沿用当前对话、精确标题进入历史会话（未命中则新建）、`__new__` 强制新建。
- **长截图**：完整对话长图，思考块默认收起，超长自动分片；截图顶部叠加 1:1 Chromium 矢量浏览器外壳（含动态对话标题、明暗主题自适应、真实 Google 账号头像）。
- **附带文件**：图片（media_id）或已下载文件一起提问。

## 前置条件

1. **Neo-MoFox 框架**：本插件是 Neo-MoFox 的插件，需要先安装并运行 Neo-MoFox 框架。
2. **Python** `>=3.11`。
3. **Playwright**：插件会自动安装；运行脚本时需已安装（`uv sync` 后可用）。
4. **Chrome 浏览器**（推荐）：插件优先使用系统安装的正式版 Chrome（Google 反自动化检测只信任正式版 Chrome），未找到时回退 Playwright 自带 Chromium（可能被 Gemini 风控登出，不推荐）。
5. **本地代理**（仅 Gemini 需要）：访问 Gemini 需代理，默认 `http://127.0.0.1:7890`，可用环境变量 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` 覆盖。
6. **bot 专属账号**：为 bot 注册独立的 DeepSeek / Google 账号并完成一次登录（见下文），避免暴露个人账号数据。

## 安装

将 `plugins/ai_ui_snapshot/` 目录放入 Neo-MoFox 的 `plugins/` 下，重启应用即可自动加载。插件目录内自带脚本（登录、验证），可直接运行。

## 快速上手（看完就能用）

### 第 1 步：登录 bot 账号（一次性）

在插件目录运行对应登录脚本，浏览器会打开真实网页，手动为 bot 账号完成登录后自动持久化登录态（保存到项目根 `data/ai_ui_snapshot_profile/<site>/`，已被 `.gitignore` 忽略，不随插件发布）：

```bash
# 进入插件目录
cd plugins/ai_ui_snapshot

# DeepSeek 登录（无需代理）
uv run python scripts/login_deepseek.py

# Gemini 登录（需代理）
uv run python scripts/login_gemini.py
```

### 第 2 步：确认登录态（可选）

```bash
# 用插件完整逻辑跑一次截图验证，产物在 scripts/ 目录
uv run python scripts/verify_deepseek_screenshot.py   # DeepSeek 截图验证
uv run python scripts/verify_gemini_screenshot.py     # Gemini 截图验证
```

### 第 3 步：在聊天中使用

命令 / 工具示例：

```
/ask 用通俗的语言解释什么是量子纠缠
/ask -g 帮我写一首关于秋天的短诗          # -g 切换 Gemini
/ask -m 专家 -c- 换一个专家模式的新对话来聊这个问题
```

工具（DeepSeek）：`ask_deepseek` / `deepseek_snapshot` / `deepseek_share` / `deepseek_history` / `deepseek_state`
工具（Gemini）：`ask_gemini_ai` / `gemini_generate_image` / `gemini_snapshot` / `gemini_share`
工具（通用）：`reset_browser`（卡死/崩溃恢复）

## 截图行为验证

插件内置验证脚本（走插件完整截图逻辑），用于确认长截图行为正确，产物输出到插件 `scripts/` 目录：

```bash
# DeepSeek 截图验证（思考折叠/侧边栏收起/思考展开三个场景）
uv run python scripts/verify_deepseek_screenshot.py

# Gemini 截图验证（真实长对话 + 可选 --short 短回复场景）
uv run python scripts/verify_gemini_screenshot.py
uv run python scripts/verify_gemini_screenshot.py --short
```

产物为 `scripts/verify_deepseek_*.png` / `scripts/verify_gemini_*.png`（已被 `.gitignore` 忽略）。

## 配置

见 `config/plugins/ai_ui_snapshot/config.toml`（插件加载后自动生成）：网页实时模式、截图渲染、浏览器外壳装饰（开关 / 主题 / 头像）、图片上传等。
