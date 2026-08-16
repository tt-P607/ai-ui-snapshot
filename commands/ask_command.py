"""快捷命令：在真实 AI 网页提问并截图对话区发送。

用法：
    /ask <问题>                          → 用 DeepSeek 网页提问并截图发送
    /ask -g <问题>                       → 用 Gemini 网页提问并截图发送
    /ask -m 专家 <问题>                  → 切换到专家模式后提问
    /ask -d <问题>                       → 开启深度思考后提问
    /ask -s <问题>                       → 开启联网搜索后提问（快速模式）
    /ask -t 展开 <问题>                  → 截图时展开思考过程块（展开/折叠/收起/reveal）
    /ask -b 收起 <问题>                  → 截图时收起左侧边栏（展开/收起/隐藏/show/hide）
    /ask -n <问题>                       → 先开新对话再提问（换模式前使用）
    /ask -d -s -m 识图 -t 折叠 -b 收起 <问题>  → 组合使用
"""

from __future__ import annotations

from src.app.plugin_system.api import send_api
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseCommand, cmd_route

from ..config import AiUiSnapshotConfig
from ..services.service import (
    ask_deepseek,
    ask_gemini,
    strip_data_uri_prefix,
)

logger = get_logger("ai_ui_snapshot.command")


def _parse_ask_flags(text: str) -> tuple[str, str, str, bool | None, bool | None, str, str, str, bool, str]:
    """解析 /ask 命令的可选开关前缀。

    支持 ``-g``（站点：gemini；默认 deepseek）、``-m <模式/模型>``（模式/模型）、
    ``-d``（深度思考开）、``-d-``（深度思考关）、``-s``（联网开）、``-s-``
    （联网关）、``-t <思考方式>``（思考过程块展开方式：展开/折叠/收起/reveal/
    expand/collapse）、``-b <侧边栏>``（侧边栏显示：展开/收起/隐藏/show/hide）、
    ``-r <范围>``（信息返回：last 最新回复 / full 整段对话）、``-c <标题>``
    （进入指定历史会话，标题取自 deepseek_history list，未命中则新建）、
    ``-c-``（强制开新对话）、``-n``（等价 ``-c-``，保留兼容）。解析后返回剩余问题文本。

    Args:
        text: 原始命令参数。

    Returns:
        tuple[str, str, bool | None, bool | None, str, str, str, bool, str, str]:
            (问题文本, 站点, 模式/模型, 深度思考, 智能搜索, 思考过程块展开方式,
            侧边栏显示方式, 信息返回范围, 是否开新对话, 对话标题)。
    """
    tokens = (text or "").split()
    site = "deepseek"
    mode = ""
    think = "collapse"
    sidebar = "auto"
    return_scope = "last"
    new_chat = False
    conversation = ""
    deepthink: bool | None = None
    search: bool | None = None
    body: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-g":
            site = "gemini"
            i += 1
            continue
        if tok == "-m" and i + 1 < len(tokens):
            mode = tokens[i + 1]
            i += 2
            continue
        if tok == "-t" and i + 1 < len(tokens):
            think = tokens[i + 1]
            i += 2
            continue
        if tok == "-b" and i + 1 < len(tokens):
            sidebar = tokens[i + 1]
            i += 2
            continue
        if tok == "-r" and i + 1 < len(tokens):
            return_scope = tokens[i + 1]
            i += 2
            continue
        if tok == "-c" and i + 1 < len(tokens):
            conversation = tokens[i + 1]
            i += 2
            continue
        if tok == "-c-":
            conversation = "__new__"
            i += 1
            continue
        if tok == "-n":
            new_chat = True
            i += 1
            continue
        if tok in ("-d", "-d-"):
            deepthink = tok == "-d"
            i += 1
            continue
        if tok in ("-s", "-s-"):
            search = tok == "-s"
            i += 1
            continue
        body.append(tok)
        i += 1
    return " ".join(body).strip(), site, mode, deepthink, search, think, sidebar, return_scope, new_chat, conversation


class AiSnapshotCommand(BaseCommand):
    """AI 界面截图快捷命令。"""

    name: str = "ask"
    description: str = "在真实 AI 网页提问并截图对话区发送：/ask [-g] [-m 模式] [-d] [-s] <问题>"

    @cmd_route()
    async def handle_ask(self, question: str) -> tuple[bool, str]:
        """在真实 AI 网页提问并截图发送。

        默认用 DeepSeek（快速模式 + 深度思考 + 联网搜索）；``-g`` 切换 Gemini
        （默认模型 3.6 Flash，扩展思考由 -d 控制）。连续对话由同 stream 复用
        同一浏览器页面保证。

        Args:
            question: 问题文本（可带 -g/-m/-d/-s/-t/-b/-r 开关前缀）。

        Returns:
            tuple[bool, str]: (是否成功, 结果信息)。
        """
        body, site, mode, deepthink, search, think, sidebar, return_scope, new_chat, conversation = _parse_ask_flags(question)
        if not body:
            return False, "用法：/ask [-g] [-m 模式] [-d] [-s] [-t 展开/折叠] [-b 收起/展开] [-r last/full] [-c 标题/-c-] [-n] <问题>"

        config = self.plugin.config if isinstance(self.plugin.config, AiUiSnapshotConfig) else None
        if config is None:
            return False, "插件配置缺失，无法执行"

        if site == "gemini":
            return await self._handle_gemini(body, mode, deepthink, return_scope, new_chat, conversation, config)
        return await self._handle_deepseek(body, mode, deepthink, search, think, sidebar, return_scope, new_chat, conversation, config)

    async def _handle_deepseek(
        self,
        body: str,
        mode: str,
        deepthink: bool | None,
        search: bool | None,
        think: str,
        sidebar: str,
        return_scope: str,
        new_chat: bool,
        conversation: str,
        config: AiUiSnapshotConfig,
    ) -> tuple[bool, str]:
        """用 DeepSeek 网页提问并截图发送。

        Args:
            body: 问题文本。
            mode: 对话模式。
            deepthink: 深度思考开关。
            search: 智能搜索开关。
            think: 思考过程块展开方式。
            sidebar: 侧边栏显示方式。
            return_scope: 信息返回范围。
            new_chat: 是否先开新对话。
            conversation: 对话定位。
            config: 插件配置。

        Returns:
            tuple[bool, str]: (是否成功, 结果信息)。
        """
        # 默认：快速模式 + 深度思考 + 联网搜索（快速模式同时支持两者）
        if not mode:
            mode = "快速模式"
        if deepthink is None:
            deepthink = True
        if search is None:
            search = True

        result = await ask_deepseek(
            body,
            stream_id=self.stream_id,
            timeout_s=config.web.reply_timeout,
            mode=mode,
            deepthink=deepthink,
            search=search,
            new_chat=new_chat,
            conversation=conversation,
            output_format="snapshot",
            think=think,
            sidebar=sidebar,
            return_scope=return_scope,
        )
        if not result.ok:
            return False, result.error or "生成截图失败"
        if not result.data_uri:
            return False, "生成截图失败"

        for piece in result.data_uri:
            if not piece.startswith("data:"):
                return False, "生成截图失败"
            sent = await send_api.send_image(
                strip_data_uri_prefix(piece),
                self.stream_id,
                processed_plain_text="[DeepSeek 界面截图]",
            )
            if not sent:
                return False, "截图已生成但发送失败"

        return True, f"已生成 {result.model_name} 界面截图并发送。"

    async def _handle_gemini(
        self,
        body: str,
        mode: str,
        deepthink: bool | None,
        return_scope: str,
        new_chat: bool,
        conversation: str,
        config: AiUiSnapshotConfig,
    ) -> tuple[bool, str]:
        """用 Gemini 网页提问并截图发送。

        Args:
            body: 问题文本。
            mode: 对话模式（Gemini 模型名）。
            deepthink: 是否开启扩展思考。
            return_scope: 信息返回范围。
            new_chat: 是否先开新对话（映射为 conversation="__new__"）。
            conversation: 对话定位。
            config: 插件配置。

        Returns:
            tuple[bool, str]: (是否成功, 结果信息)。
        """
        # 默认模型 3.6 Flash；-d 控制扩展思考
        if not mode:
            mode = "3.6 Flash"
        if new_chat and not conversation:
            conversation = "__new__"

        result = await ask_gemini(
            body,
            stream_id=self.stream_id,
            timeout_s=config.web.reply_timeout,
            model=mode,
            think=deepthink,
            conversation=conversation,
            output_format="snapshot",
            return_scope=return_scope,
        )
        if not result.ok:
            return False, result.error or "生成截图失败"
        if not result.data_uri:
            return False, "生成截图失败"

        for piece in result.data_uri:
            if not piece.startswith("data:"):
                return False, "生成截图失败"
            sent = await send_api.send_image(
                strip_data_uri_prefix(piece),
                self.stream_id,
                processed_plain_text="[Gemini 界面截图]",
            )
            if not sent:
                return False, "截图已生成但发送失败"

        return True, f"已生成 {result.model_name} 界面截图并发送。"
