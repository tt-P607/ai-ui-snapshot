"""ai_ui_snapshot 服务层。

按站点拆分：

- ``base``：站点无关通用层（页面动作、浏览器会话、浏览器外壳装饰、通用工具）
- ``deepseek``：DeepSeek 站点适配层（常量 + 动作）
- ``gemini``：Gemini 站点适配层（常量 + 动作）
- ``service``：业务编排（原 snapshot_service）
"""
