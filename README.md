# AI UI Snapshot

让 bot 用真人的方式操作 DeepSeek、Gemini、豆包网页：提问、截长图、拿分享链接、生图生视频。另外可以接管框架的图片识别（默认关闭）。

登录态用的是 bot 自己的账号，浏览器打开的是真实网站，所以截图就是真的网页截图，顶部还叠了浏览器外壳（标签页、地址栏、头像）。

## 能干什么

### DeepSeek

| 工具 | 用途 |
|---|---|
| `ask_deepseek` | 提问，返回回复文本。可以带图片或文件，可以控制深度思考和联网搜索开关 |
| `deepseek_snapshot` | 截当前对话（或指定对话）的长图发出去，不提问 |
| `deepseek_share` | 拿当前对话的官方分享链接，不提问 |
| `deepseek_history` | 列历史会话，或者进某个会话接着聊 |
| `deepseek_state` | 看当前在哪个对话、开关是什么状态 |

### Gemini（需要代理）

| 工具 | 用途 |
|---|---|
| `ask_gemini_ai` | 提问，全模态，图片/语音/视频/文档都能带 |
| `gemini_generate_image` | 让 Gemini 生图，可以带参考图改图，出图自动发到聊天 |
| `gemini_snapshot` | 截长图发出去 |
| `gemini_share` | 拿官方分享链接 |

模型名传族关键词就行：`Flash-Lite`、`Flash`、`Pro`。插件按关键词去菜单里找当前版本，站点换了版本号也不会失效。

### 豆包

| 工具 | 用途 |
|---|---|
| `ask_doubao` | 提问。多张图可以一次传进去问，同一个对话里可以连续追问 |
| `doubao_snapshot` | 截长图发出去 |
| `doubao_history` | 列历史会话，或者进某个会话接着聊 |
| `doubao_state` | 看当前档位（快速/专家） |
| `doubao_generate_image` | 用豆包的生图技能出图，可以带参考图做图生图 |
| `doubao_generate_video` | 提交视频生成任务，立刻返回，后台等着。生成完会唤醒 bot，由 bot 决定发不发 |
| `doubao_send_video` | 把生成完的视频发到聊天 |

生图生视频只开放免费模型，付费档位不出现在参数里。

### 识图接管（默认关闭）

框架收到图片时会问一圈"谁能识别"，内置 VLM 排在最后兜底。开启后这个插件会排前面，把图片丢给真实网页识别，拿到描述后交回框架，框架自己的 VLM 就不跑了。

**默认是关的**，要开就把配置里的 `[recognize] enabled` 改成 `true`。开启后每张图会通过真实网页识别一次。

站点默认用 DeepSeek，可以换成豆包或 Gemini。

提示词用的是框架的（`config/core.toml` 里 `[chat]` 的 `image_recognition_prompt` 和 `emoji_recognition_prompt`）。插件不自己再定义一份，要改就改框架那边，改一处就够。

中间任何一步失败（没登录、超时、图片读不出来、回复是空的），插件都直接让开，框架自己的 VLM 照常兜底。所以插件出问题时，识图会退回框架自己的实现，不会没有识图。

每次识图会新开一个对话。如果接着旧对话问，一旦图片没传上去，模型会对着上一张图回答，描述就对应到别的图片上了；而描述会被框架按图片哈希缓存，这条描述会被长期复用。新开对话能保证回复只对应当前这张图。都

### 其他

- `/ask` 命令：手动驱动网页提问并截图。`-g` 走 Gemini，`-db` 走豆包，`-m` 指定模型或档位
- `reset_browser`：网页卡死或崩溃时重建浏览器。登录态和云端历史都还在
- 所有对话类工具都有 `conversation` 参数：留空接着当前对话，填标题进指定会话，填 `__new__` 强制开新对话

## 装之前先确认这些

| 条件 | 说明 |
|---|---|
| Python 3.11+ 和 uv | 框架的要求，依赖用 `uv sync` 装 |
| 系统装了正式版 Chrome | 插件优先用系统 Chrome，留空会自动找常见安装路径。实在找不到才退回 Playwright 自带的 Chromium，但那个 Gemini 很容易被风控登出，字节系站点也可能反复要验证 |
| 每个站点一个 bot 账号 | DeepSeek、Google、豆包各登录一次。没登录的话对应工具会直接报错，不会硬用登出状态去操作 |
| 一个代理（只有 Gemini 要） | 配 `web.proxy_url`，或者设 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY`。DeepSeek 和豆包直连就行 |
| 登录时得有图形界面 | 登录脚本会开一个带界面的 Chrome 让你手动登。纯服务器或容器里没桌面的话第一次登录做不了，登完可以把登录态搬走，之后无头跑 |
| 网络能通 | 站点对频繁调用有频控，调得太勤可能弹人机验证，插件会明确报出来让你手动处理 |

另外几点：

- 无头跑用 `[web] headless = true`（默认就是）。但第一次登录必须在有界面的环境做
- 登录态放在 `data/ai_ui_snapshot_profile/<站点>/`，`.gitignore` 忽略掉了，不会跟着插件发布。换机器要重新登
- 配置在 `config/plugins/ai_ui_snapshot/config.toml`，改完要重启 bot
- 依赖 playwright，`uv sync` 的时候会装上
- 识图提示词不在插件配置里，读的是框架的 `config/core.toml`

## 怎么跑起来

### 1. 安装

- **市场安装**：在 Neo-MoFox WebUI 插件市场中搜索 `ai_ui_snapshot` 点击安装，或通过 CLI 安装。
- **手动安装**：将 `ai_ui_snapshot` 文件夹放入 `plugins/` 目录。
- **环境依赖**：
  - 根目录执行 `uv sync` 同步依赖（包含 `playwright`）。
  - 本插件优先复用系统已安装的官方 Google Chrome。如果系统没有 Chrome，在项目根执行一次 `uv run playwright install chromium` 下载无头浏览器内核。

### 2. 配置

改 `config/plugins/ai_ui_snapshot/config.toml`（第一次加载后自动生成），确认这几项：

```toml
[sites]
deepseek = true          # 站点开关，不用就关
gemini   = false         # 走代理，不用就关
doubao   = true

[web]
proxy_url = "http://127.0.0.1:7890"   # 只有 Gemini 需要，另外两个留空直连
headless  = true                      # 无人值守就 true

[screenshot]
browser_path = ""        # 留空自动找系统 Chrome，也可以写绝对路径

[recognize]
enabled = false          # 接管框架识图。开启后每张图通过真实网页识别
site    = "deepseek"     # 用哪个站点：deepseek / doubao / gemini
emoji   = true           # 表情包要不要也接管
timeout = 120            # 单张识图超时，秒
```

### 3. 登录 bot 账号

> **注意：执行登录前，请务必先停止正在运行的 Bot。**
> 浏览器登录态目录（`data/ai_ui_snapshot_profile/`）在同一时间只能被一个 Chrome 实例独占。如果 Bot 开着，登录脚本会因目录被占用（exitCode=21）而无法正常启动。

每个站点登一次。脚本会用普通 Chrome 打开网页让你手动登录：

```bash
# 在哪个目录跑都行，脚本按自己的位置找项目根
uv run python plugins/ai_ui_snapshot/scripts/login_deepseek.py
uv run python plugins/ai_ui_snapshot/scripts/login_gemini.py      # 需确保代理已开启
uv run python plugins/ai_ui_snapshot/scripts/login_doubao.py
```

脚本会弹出浏览器窗口，你在里面登录（想换账号就先在里面退出旧账号再登新的）。登录完成后**直接把浏览器窗口关掉**，脚本检测到窗口关闭后会自动用无头模式验证登录态并保存头像。

### 4. 确认能用

登录完成后先别急着开 Bot，跑两条脚本确认环境：

```bash
# 1. 验证启动参数与反爬指纹（不联网，检查 Chrome 进程命令行是否干净）
uv run python plugins/ai_ui_snapshot/scripts/verify_browser_flags.py

# 2. 检查登录态目录与登录就绪状态（需先关 bot）
uv run python plugins/ai_ui_snapshot/scripts/verify_login_readiness.py
```

两项检查通过后，即可启动 Bot（`uv run main.py`）。

---

## 怎么使用

### 聊天命令 `/ask`

用户可在聊天窗口直接向网页提问并获取仿真截图，方便快速测试与日常查看：

```text
/ask 为什么天空是蓝色的                  # 默认通过 DeepSeek 提问并截取长图
/ask -db 用一句话介绍你自己              # 走豆包
/ask -g 解释量子纠缠的基本原理            # 走 Gemini
/ask -m deepseek-reasoner 详细分析该算法  # 指定模型/档位
```

### 给 Bot 配置人设提示词（可选）

插件注册了一系列高层工具（`ask_deepseek`、`ask_doubao`、`gemini_generate_image` 等）。可以在 Bot 人设或 SystemPrompt 中加入简要指引，让 Bot 知道何时主动调用：

```markdown
- 当需要深度逻辑推理、联网求证最新时事或处理复杂问题时，你可以调用 `ask_deepseek` 或 `ask_doubao` 获取真实网页解答。
- 当群友请求你画图、设计插画时，可以调用 `doubao_generate_image` 或 `gemini_generate_image` 生成图片。
- 当你想把正在看的 AI 网页完整界面展示给群友时，可以调用 `deepseek_snapshot` 或 `doubao_snapshot` 发送长截图。
```

### 开启识图接管

默认关闭。若希望群里收到的图片由真实网页生成文字描述：
1. 确保在 `[sites]` 中开启了对应站点（如 `deepseek = true`）并已完成该站点登录；
2. 在 `config.toml` 中设置 `[recognize] enabled = true`；
3. 重启 Bot。当群聊出现图片时，Bot 会自动调用网页识图，识别结果回填给框架，框架内置的 VLM 不会重复调用；识别失败会自动回退至框架内置 VLM。
4. 本地可通过 `uv run python plugins/ai_ui_snapshot/scripts/verify_recognize.py` 完整验证该链路。

## 配置

`config/plugins/ai_ui_snapshot/config.toml`，改完重启生效：

| 节 | 键 | 默认 | 说明 |
|---|---|---|---|
| `[plugin]` | `enabled` | `true` | 总开关，关了所有组件都不注册 |
| `[sites]` | `deepseek` / `gemini` / `doubao` | `true` / `false` / `false` | 站点开关。关了对应工具就不注册，识图也不能用它 |
| `[web]` | `web_profile_dir` | `data/ai_ui_snapshot_profile` | 登录态目录。换路径要重新登录 |
| | `reply_timeout` | `240` | 等回复的超时秒数 |
| | `idle_timeout` | `600` | 浏览器闲多久自动关，`0` 表示不关 |
| | `headless` | `true` | 无头运行 |
| | `proxy_url` | `""` | 站点代理，只有 Gemini 要 |
| | `theme` | `auto` | 页面明暗：`auto` 按本地时间切，或者 `light` / `dark` |
| `[decoration]` | `enabled` | `true` | 截图顶部叠浏览器外壳 |
| | `theme` | `auto` | 外壳配色，跟页面主题互相独立 |
| | `avatar_url` | `""` | 外壳右上角的头像。留空就用登录时存下来的真实头像 |
| `[screenshot]` | `width` / `height` | `1440` / `900` | 视口尺寸 |
| | `device_scale_factor` | `2` | 高清倍率 |
| | `max_height` | `8000` | 长截图单张最高多少像素，超了就切片 |
| | `browser_path` | `""` | Chrome 路径，留空自动找 |
| `[upload]` | `enabled` / `max_size_mb` | `true` / `50` | 附件上传开关和大小上限 |
| `[recognize]` | `enabled` | `false` | 要不要接管框架识图。开启后每张图会通过真实网页识别一次 |
| | `site` | `deepseek` | 用哪个站点，得在 `[sites]` 里也开着 |
| | `emoji` | `true` | 表情包要不要也接管 |
| | `timeout` | `120` | 单张识图超时 |

识图提示词不在这个文件里，读的是框架的 `config/core.toml` → `[chat] image_recognition_prompt` / `emoji_recognition_prompt`，留空就用框架内置的。

## 脚本

都在插件 `scripts/` 下面。日常用的三个：

| 脚本 | 干什么 |
|---|---|
| `login_deepseek.py` | DeepSeek 登录或换账号 |
| `login_gemini.py` | Gemini 登录或换账号（走代理） |
| `login_doubao.py` | 豆包登录或换账号 |

`login_common.py` 是这三个共用的东西，不是入口，不用管。

验证脚本，改完代码或者换环境之后跑：

| 脚本 | 验什么 | 条件 |
|---|---|---|
| `verify_browser_flags.py` | 启动参数里没有会被 Chrome 判定为危险的那些项，`navigator.webdriver` 也藏好了。会看真实进程的命令行 | 不联网 |
| `verify_login_readiness.py` | 登录脚本和运行时用的是同一个登录态目录，三个站点的就绪判定都能认出已登录 | 先关 bot |
| `verify_conversation_switch.py` | 用本地假页面跑生产的 JS 和动作类：模型族匹配、扩展思考开关、三个站点进会话的语义 | 不联网 |
| `verify_real_conversation_switch.py` | 真站点上列历史会话、进指定会话并读回确认、重复进是幂等的、标题不存在会失败 | 关 bot + 联网 |
| `verify_recognize.py` | 识图接管：提示词来源、事件契约、真实识图、失败让开、临时文件清理干净 | 加 `--no-site` 就全本地 |
| `verify_deepseek_screenshot.py` | DeepSeek 长截图（思考折叠、侧边栏、思考展开） | 关 bot + 联网 |
| `verify_gemini_screenshot.py` | Gemini 长截图，加 `--short` 是短回复场景 | 关 bot + 联网 |
| `verify_doubao_screenshot.py` | 豆包截图全链路 | 关 bot + 联网 |
| `verify_doubao_full.py` | 豆包登录、提问、附件、截图连起来跑 | 关 bot + 联网 |
| `verify_doubao_media.py` | 豆包生图生视频的产物落盘 | 关 bot + 联网 |
| `verify_doubao_attachment.py` | 豆包附件上传和计数 | 关 bot + 联网 |

要关 bot 的原因：登录态就是同一个 `user_data_dir`，两个 Chrome 同时用会崩（exitCode=21）。

## 几个约定

### `conversation` 参数

| 传什么 | 结果 |
|---|---|
| 空 | 接着当前对话聊。同一个聊天流复用同一个浏览器页面，所以是连续的 |
| 标题 | 进这个历史会话。标题不存在的话，提问工具会新建一个对话继续；截图和分享工具会报错，因为它们不该去操作一个空会话 |
| `__new__` | 强制开新对话 |

判断"进没进会话"只看一件事：列表里能不能找到这个标题并点开。点完之后会轮询几秒确认切换生效，没确认到只记一行日志、不会阻断后面的动作。页面没及时刷新不代表没进去，要是因此就报失败，上层会白白放弃截图或提问。

### 识图接管的返回

| 情况 | 插件返回 | 结果 |
|---|---|---|
| 识图成功 | `SUCCESS`，写上描述和 `engine_processed=True` | 框架内置 VLM 看到标记就不跑了 |
| 接管关了 / 站点没启用 / 是语音或视频 / 不是图片 | `PASS` | 交回框架内置 VLM |
| 识图失败（没登录、超时、图片读不出来、回复空） | `PASS` | 交回框架内置 VLM |

### 遇到问题怎么办

| 现象 | 原因 | 处理 |
|---|---|---|
| 登录脚本弹出的 Chrome 闪退 / 报错 exitCode=21 | Bot 正在运行，占用了相同的用户数据目录 | **先停止 Bot**，再运行登录脚本 |
| 报错 `Executable doesn't exist` | 系统未安装 Chrome，也未下载 Playwright 内核 | 安装系统 Google Chrome，或执行 `uv run playwright install chromium` |
| 报"登录态失效" | 站点把你蹬了 | 重跑对应的 `login_*.py` |
| Gemini 全挂 | 代理没配或没开，或者登录态被风控清了 | 检查 `web.proxy_url`，重新登录 |
| 豆包报"触发了人机验证" | 调得太频繁 | 手动在浏览器里过验证，然后降低频率 |
| 页面卡死、没反应 | 浏览器会话出问题 | 调 `reset_browser`，登录态和历史都留着 |
| 识图没生效 | `[recognize] enabled` 还是 `false`（默认就没开）、站点没在 `[sites]` 里开、或者框架没注册提示词模板 | 挨个查一遍，插件日志会写清楚是哪个原因 |
| 截图顶部没有浏览器外壳 | `[decoration] enabled=false` | 打开就行 |
| 长截图被切成好几张 | 对话太长，超过 `screenshot.max_height` 了 | 分片是正常行为；不想分片可以调大 `max_height` |

## 为什么登录不用自动化

登录脚本是用 `subprocess` 直接启动普通 Chrome 的，只传 `--user-data-dir` 和（需要的话）`--proxy-server`。没有 Playwright，没有调试通道。

原因是踩出来的：Chromium 的 `kBadFlags` 名单（在 `chrome/browser/ui/startup/bad_flags_prompt.cc`）把 `--no-sandbox` 和 `--disable-blink-features` 都算危险参数。带上它们 Chrome 会在页面顶上弹一条"您使用的是不受支持的命令行标记"，Google 登录页看到这条就直接拒绝登录，提示"此浏览器或应用可能不安全"，点重试也没用。

其中 `--no-sandbox` 是 Playwright 默认加上的，不是我们写的，得用 `ignore_default_args` 剔掉才有效。所以现在运行时的会话也不传任何自定义启动参数了，`navigator.webdriver` 那些指纹改由页面脚本藏。

登录完成后脚本会用运行时那套无头会话再验证一次（确认真登上了、顺手存头像），这样"登录时的环境"和"运行时的环境"是同一套。

## 目录

```
plugins/ai_ui_snapshot/
├── config.py                 配置定义，框架照着它生成 config/plugins/ai_ui_snapshot/config.toml
├── plugin.py                 入口，按站点开关装配组件
├── manifest.json             清单（组件、依赖、版本）
├── commands/ask_command.py   /ask 命令
├── event_handler/
│   └── media_recognize.py    识图接管
├── services/
│   ├── service.py            统一入口（提问、截图、分享、识图）
│   ├── base/                 浏览器会话、通用页面动作、外壳装饰
│   └── deepseek/ gemini/ doubao/   各站点的常量和动作
├── tools/                    给模型用的工具
└── scripts/                  登录、验证、诊断脚本
```

运行时会往这些地方写东西，都不跟着插件发布：

- `data/ai_ui_snapshot_profile/<站点>/` 登录态和账号资产
- `data/ai_ui_snapshot_profile/<站点>/images/`、`videos/` 生成的图和视频
- `data/media_cache/` 框架的媒体缓存

## 许可

AGPL-3.0，见 `LICENSE`。
