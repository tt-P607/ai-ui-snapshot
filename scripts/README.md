# scripts

插件的可执行脚本都在这。每个都能从任意目录跑，脚本会按自己的位置找项目根，不用先 `cd` 过来。

完整说明在上级的 [README.md](../README.md)，这里只告诉你现在该跑哪个。

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

```bash
uv run python plugins/ai_ui_snapshot/scripts/verify_conversation_switch.py      # 会话进入和模型匹配，不用联网
uv run python plugins/ai_ui_snapshot/scripts/verify_recognize.py --no-site      # 识图接管契约，全本地
uv run python plugins/ai_ui_snapshot/scripts/verify_recognize.py                # 识图真实链路，联网
uv run python plugins/ai_ui_snapshot/scripts/verify_real_conversation_switch.py # 真站点会话切换，联网
```

截图类的有 `verify_deepseek_screenshot.py`、`verify_gemini_screenshot.py`（可以加 `--short`）、`verify_doubao_screenshot.py`、`verify_doubao_full.py`、`verify_doubao_media.py`、`verify_doubao_attachment.py`。

## 排查站点改版

站点换了页面结构、选择器失效时用这些，它们会把页面结构 dump 出来对照：

`probe_doubao_dom*.py`、`probe_doubao_skill.py`、`probe_doubao_upload*.py`、`probe_doubao_imgdiag.py`、`diag_doubao_ready.py`、`diag_doubao_attach.py`

## 注意

- 登录态是同一个 `user_data_dir`，两个 Chrome 不能同时用（会崩，exitCode=21）。写了"要先关 bot"的脚本，跑之前记得先停 bot
- 联网的脚本会真访问站点。不提问的那几个只做定位和截图；`verify_recognize.py` 会真提问一次
- `login_common.py` 是三个登录脚本共用的东西，不是入口
