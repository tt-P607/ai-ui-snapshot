# scripts

插件的可执行脚本都在这。每个都能从任意目录跑，脚本会按自己的位置找项目根，不用先 `cd` 过来。

完整说明在上级的 [README.md](../README.md)，这里只告诉你现在该跑哪个。

## 双击打开手工窗口

Windows 下先停止 Bot，再双击对应 BAT：

- [open_deepseek.bat](open_deepseek.bat)：DeepSeek。
- [open_gemini.bat](open_gemini.bat)：Gemini，沿用插件代理。
- [open_doubao.bat](open_doubao.bat)：豆包。

入口按自身位置找到项目，读取现有插件配置和原站点登录资料，不包含机器专用路径。需要已安装 Chrome，并确保 `uv` 在 PATH 中。可以手工登录、提问或处理网站风控；关闭后不会自动启动无头浏览器或校验登录态。操作完成后关闭该站点所有窗口，再启动 Bot。

也可以从项目根用 Python 入口，仅添加 `--open-only`：

```bash
uv run python plugins/ai_ui_snapshot/scripts/login_deepseek.py --open-only
uv run python plugins/ai_ui_snapshot/scripts/login_gemini.py --open-only
uv run python plugins/ai_ui_snapshot/scripts/login_doubao.py --open-only
```

从其他目录执行时，用 `uv run --project <项目根目录> python <登录脚本路径> --open-only`。非 Windows 系统需要配置本机 `screenshot.browser_path`，并自行确保该站点浏览器和 Bot 已关闭。手工通过验证不保证后续无头调用不会再次被风控。

## 登录或换账号

```bash
uv run python plugins/ai_ui_snapshot/scripts/login_deepseek.py    # DeepSeek，直连
uv run python plugins/ai_ui_snapshot/scripts/login_gemini.py      # Gemini，走代理
uv run python plugins/ai_ui_snapshot/scripts/login_doubao.py      # 豆包
```

脚本会开一个普通 Chrome（没有自动化驱动，也没有特殊启动参数），你在里面登录。换账号就先在里面退出旧的、再登新的。登完把窗口关掉，脚本会自己校验登录态并保存。

## 确认环境没问题

```bash
uv run python plugins/ai_ui_snapshot/scripts/verify_browser_flags.py      # 启动参数和指纹，不用联网
uv run python plugins/ai_ui_snapshot/scripts/verify_login_readiness.py    # 登录态和目录，要先关 bot
```

## 验证功能

- **基础与契约验证**：
  ```bash
  uv run python plugins/ai_ui_snapshot/scripts/verify_conversation_switch.py      # 会话进入和模型匹配，不用联网
  uv run python plugins/ai_ui_snapshot/scripts/verify_recognize.py --no-site      # 识图接管契约，全本地
  uv run python plugins/ai_ui_snapshot/scripts/verify_recognize.py                # 识图真实链路，联网
  uv run python plugins/ai_ui_snapshot/scripts/verify_real_conversation_switch.py # 真站点会话切换，联网
  ```
- **站点功能与截图验证**：
  ```bash
  uv run python plugins/ai_ui_snapshot/scripts/verify_deepseek_screenshot.py      # DeepSeek 提问与截图
  uv run python plugins/ai_ui_snapshot/scripts/verify_gemini_screenshot.py        # Gemini 截图（支持 --short）
  uv run python plugins/ai_ui_snapshot/scripts/verify_doubao_screenshot.py        # 豆包截图冒烟
  uv run python plugins/ai_ui_snapshot/scripts/verify_doubao_full.py              # 豆包全量交互验证
  uv run python plugins/ai_ui_snapshot/scripts/verify_doubao_media.py             # 豆包生图/生视频验证
  uv run python plugins/ai_ui_snapshot/scripts/verify_doubao_attachment.py        # 豆包附件全流程验证
  ```

## 注意

- 登录态是同一个 `user_data_dir`，两个 Chrome 不能同时用（会崩，exitCode=21）。写了"要先关 bot"的脚本，跑之前记得先停 bot
- BAT 入口在 Windows 下检查资料目录占用；检测到 Bot 或其他该站点窗口时拒绝启动，不强制关闭进程
- 联网的脚本会真访问站点。不提问的那几个只做定位和截图；`verify_recognize.py` 会真提问一次
- `login_common.py` 是三个登录脚本共用的东西，不是入口
