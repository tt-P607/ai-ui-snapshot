"""LLM 工具集：豆包提问 / 历史会话 / 状态查询 / 截图 / 生图 / 生视频。

把豆包网页操作封装为高层工具，bot 通过参数使用，无需逐步操控浏览器：

- ask_doubao：真实提问，返回回复文本（内部消化转述）。

- doubao_snapshot：直接截取当前/指定对话界面为长截图并发送，不提问。

- doubao_history：列出历史会话 / 进入指定会话（返回完整上下文 + 档位状态）。

- doubao_state：查询当前模型档位状态。

- doubao_generate_image：图像生成技能生图并发送（同步等待，通常 <90s）。

- doubao_generate_video：视频生成技能提交任务（立即返回，后台等待完成后

  系统会再次唤醒 bot 决策是否发送）。

- doubao_send_video：发送已生成完成的视频文件到当前聊天。

工具共用服务层（service）入口。

"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from src.app.plugin_system.api import send_api

from src.app.plugin_system.api.log_api import get_logger

from src.app.plugin_system.base import BaseTool

from ..config import AiUiSnapshotConfig

from ..services.base.browser_session import get_manager

from ..services.service import (
    AskResult,
    ask_doubao,
    capture_doubao_snapshot,
    generate_doubao_image,
    strip_data_uri_prefix,
    submit_doubao_video,
)

from .base import _ToolBase

logger = get_logger("ai_ui_snapshot.doubao_tool")


class _DoubaoToolBase(_ToolBase):
    """豆包工具基类：获取豆包站点（theme=doubao）浏览器会话动作对象。"""

    _SITE_THEME = "doubao"

    async def _doubao_actions(self):
        """获取当前 stream 的豆包浏览器会话动作对象。

        Returns:

            DoubaoActions: 豆包页面动作封装。

        Raises:

            RuntimeError: 浏览器会话创建失败。

        """

        from ..services.doubao.actions import DoubaoActions

        stream_id = self.get_current_stream_id()

        manager = get_manager()

        session = await manager.get(stream_id, theme=self._SITE_THEME)

        manager.touch(stream_id, theme=self._SITE_THEME)

        return DoubaoActions(
            session.page,
            max_screenshot_height=manager.max_screenshot_height,
            touch_cb=lambda: manager.touch(stream_id, theme=self._SITE_THEME),
            decoration_enabled=manager.decoration_enabled,
            decoration_theme=manager.decoration_theme,
            decoration_avatar_url=manager.decoration_avatar_url,
        )


class AskDoubaoTool(_DoubaoToolBase):
    """向豆包真实提问，返回回复内容供内部消化转述。"""

    name: str = "ask_doubao"

    description: str = (
        "向豆包真实提问，像真人一样使用豆包，返回回复文本供你自然转述。"
        "可用于：①生活/中文语境/创意类问题的转发；②需要字节系内容理解能力的场景。"
        "可指定模型档位（快速=默认轻量档 / 专家=深度思考档），"
        "附带图片（media_id）或已下载文件（file_name）一起提问"
        "（豆包仅接受图片/文档，单文件≤20MB，不支持音频/视频），"
        "信息返回范围（last 最新回复 / full 整段对话）。"
        "豆包无独立联网搜索开关（由模型自动决策是否联网）。"
        "conversation 参数控制对话定位：空（默认）沿用当前对话；传历史会话精确标题则"
        "进入该会话继续（标题用 doubao_history list 获取；未命中则新建）；"
        "传 __new__ 强制开新对话。每次调用都会返回当前对话标题（conversation 字段），"
        "记住它可回到同一对话。需要展示豆包原始界面时，另用 doubao_snapshot 截图。"
    )

    async def execute(
        self,
        question: Annotated[str, "要提问的问题原文（完整、自然语言）"] = "",
        model: Annotated[
            str, "模型档位：快速（默认轻量）/ 专家（深度思考档）；空则沿用当前档位"
        ] = "",
        new_chat: Annotated[
            bool, "是否先开一个新对话再提问（等价 conversation=__new__）"
        ] = False,
        conversation: Annotated[
            str,
            "对话定位：空（默认）沿用当前对话；历史会话精确标题则进入继续（用 doubao_history list 获取标题，未命中则新建）；'__new__' 强制开新对话",
        ] = "",
        image_id: Annotated[
            str,
            "附带提问的图片 media_id（聊天图片占位符 [图片(media_id)] 中的哈希）；多张图用英文逗号分隔可一次提问多张图，可空",
        ] = "",
        file_name: Annotated[
            str, "附带提问的已下载文件名（media_retriever 已下载文件），可空"
        ] = "",
        return_scope: Annotated[
            str, "信息返回范围：'last'（最新一条 AI 回复，默认）/ 'full'（整段对话）"
        ] = "last",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：解析参数与上传路径，调用提问入口，返回回复文本。

        Args:

            question: 要提问的问题原文。

            model: 模型档位（快速/专家）。

            new_chat: 是否先开新对话（等价 conversation=__new__）。

            conversation: 对话定位（空沿用当前 / 精确标题进入 / __new__ 新建）。

            image_id: 附带图片 media_id（可空）。

            file_name: 附带已下载文件名（可空）。

            return_scope: 信息返回范围。

        Returns:

            tuple[bool, str | dict]: (是否成功, 结构化结果或错误信息)。

        """

        if not question or not question.strip():
            return False, "问题不能为空"

        config = (
            self.plugin.config
            if isinstance(self.plugin.config, AiUiSnapshotConfig)
            else None
        )

        if config is None:
            return False, "插件配置缺失，无法执行"

        stream_id = self.get_current_stream_id()

        # 附带图片/文件：先把 media_id（可多个） / 已下载文件名解析为本地路径，
        # 多张图作为一条消息一次上传（不会每张图另开一条提问）
        local_paths: list[str] = []
        if image_id:
            paths, err = await self._resolve_media_ids(image_id)
            if err:
                return False, err
            local_paths.extend(paths)
        if file_name:
            file_path = await self._resolve_downloaded_file(stream_id, file_name)
            if not file_path:
                return False, f"未找到已下载文件: {file_name}"
            local_paths.append(file_path)

        result: AskResult = await ask_doubao(
            question.strip(),
            stream_id=stream_id,
            timeout_s=config.web.reply_timeout,
            model=model,
            new_chat=new_chat,
            conversation=conversation,
            local_paths=local_paths or None,
            return_scope=return_scope,
            upload_max_size_mb=config.upload.max_size_mb,
        )

        if not result.ok:
            return False, result.error or "向豆包提问失败"

        return True, {
            "model": result.model_name,
            "reply": result.reply,
            "conversation": result.conversation,
            "summary": "这是豆包的回复内容，请自然地向用户转述/消化，无需发送截图。conversation 为当前对话标题，后续追问可传同一标题回到此对话。",
            "upload": result.upload,
        }


class DoubaoSnapshotTool(_DoubaoToolBase):
    """直接截取豆包对话界面为长截图并发送，不提问、不改设置。"""

    name: str = "doubao_snapshot"

    description: str = (
        "直接截取豆包对话界面为长截图并发送到当前聊天，不提问、不换档位。"
        "适用于：对方想直接看豆包原始界面、或回复很长时，把界面截图甩给对方。"
        "conversation 参数控制截哪个会话：空（默认）截当前对话；传历史会话精确标题"
        "则进入该会话再截（标题用 doubao_history list 获取，未命中报错）。"
    )

    async def execute(
        self,
        conversation: Annotated[
            str,
            "对话定位：空（默认）截当前对话；历史会话精确标题则进入该会话再截（用 doubao_history list 获取标题，未命中报错）",
        ] = "",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：定位会话并直接截图发送。

        Args:

            conversation: 对话定位（空当前 / 精确标题进入）。

        Returns:

            tuple[bool, str | dict]: (是否成功, 结果或错误)。

        """

        config = (
            self.plugin.config
            if isinstance(self.plugin.config, AiUiSnapshotConfig)
            else None
        )

        if config is None:
            return False, "插件配置缺失，无法执行"

        stream_id = self.get_current_stream_id()

        result: AskResult = await capture_doubao_snapshot(
            stream_id=stream_id,
            conversation=conversation,
        )

        if not result.ok:
            return False, result.error or "截图失败"

        if not result.data_uri:
            return False, "截图失败"

        for piece in result.data_uri:
            if not piece.startswith("data:"):
                return False, "截图失败"

            sent = await send_api.send_image(
                strip_data_uri_prefix(piece),
                stream_id,
                processed_plain_text="[豆包界面截图]",
            )

            if not sent:
                return False, "截图已生成但发送失败"

        return True, {
            "sent": True,
            "conversation": result.conversation,
            "summary": "已截取豆包对话界面并发出，请用拟人口吻简单引述即可。conversation 为当前对话标题。",
        }


class DoubaoHistoryTool(_DoubaoToolBase):
    """豆包历史会话：列出 / 进入。"""

    name: str = "doubao_history"

    description: str = (
        "操作豆包的历史会话，像真人翻看之前的对话。"
        "action=list 列出侧边栏的历史会话标题；action=open 需提供 title 进入该会话，"
        "进入后工具会返回该会话的完整上下文与当前模型档位，之后可用 "
        "ask_doubao 继续在这个会话里对话。"
    )

    async def execute(
        self,
        action: Annotated[str, "操作：list（列出历史会话）/ open（进入指定会话）"],
        title: Annotated[str, "action=open 时的历史会话标题（list 返回的标题）"] = "",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行历史会话操作。

        Args:

            action: list / open。

            title: open 时的会话标题。

        Returns:

            tuple[bool, str | dict]: (是否成功, 结果或错误)。

        """

        try:
            actions = await self._doubao_actions()

        except Exception as exc:  # noqa: BLE001
            return False, f"浏览器会话不可用: {exc}"

        if action == "list":
            items = await actions.list_conversations()

            return True, {"histories": items}

        if action == "open":
            if not title:
                return False, "open 操作需提供 title"

            ok = await actions.open_conversation(title)

            if not ok:
                return False, f"未找到历史会话: {title}"

            context = await actions.get_conversation_text(scope="full")

            model = await actions.get_model()

            stream_id = self.get_current_stream_id()

            current_id = await actions.get_active_conversation_id()

            current_title = await actions.get_active_conversation_title()

            get_manager().set_active_conversation(
                stream_id, current_id, current_title or title, theme="doubao"
            )

            return True, {
                "opened": title,
                "model": model or "未知",
                "conversation": current_title or title,
                "context": context,
                "tip": "已进入该会话，可用 ask_doubao 继续对话。conversation 字段为当前对话标题，后续追问可传同一标题。",
            }

        return False, f"未知操作: {action}（可选 list/open）"


class DoubaoStateTool(_DoubaoToolBase):
    """查询豆包当前模型档位状态。"""

    name: str = "doubao_state"

    description: str = (
        "查询当前豆包对话的模型档位（快速/专家）。提问前调用可确认当前配置，"
        "或在需要时参考。豆包无独立深度思考/联网搜索开关：专家档即深度思考，"
        "联网由模型自动决策。"
    )

    async def execute(self) -> tuple[bool, str | dict[str, Any]]:
        """执行状态查询。

        Returns:

            tuple[bool, str | dict]: (是否成功, 模型档位状态)。

        """

        try:
            actions = await self._doubao_actions()

        except Exception as exc:  # noqa: BLE001
            return False, f"浏览器会话不可用: {exc}"

        model = await actions.get_model()

        return True, {
            "model": model or "未知",
            "tip": "豆包档位：快速=轻量默认档，专家=深度思考档；联网由豆包自动决策。",
        }


# 供插件装配导出的豆包工具类列表（文件末尾聚合，见 DOUBAO_TOOLS 赋值）


class DoubaoGenerateImageTool(_DoubaoToolBase):
    """用豆包图像生成技能生图并发送。"""

    name: str = "doubao_generate_image"

    description: str = (
        "用豆包的「图像生成」能力生成图片并直接发送到当前聊天。"
        "适合中文语境、生活化、创意插画类需求（豆包对中文提示词语义理解好）。"
        "豆包一次生成多张候选（通常 4 张），全部下载并依次发出。"
        "同步等待生成（通常 1 分钟内），完成后图片自动发出。\n"
        "支持原生 UI 参数精确配置：\n"
        "- image_id：参考图 media_id（图生图/改图），可空；\n"
        "- model：生图模型（仅限免费模型：'Seedream 4.5' 日常出图 / 'Seedream 4.0' 基础生图；付费升级模型需 VIP 不对模型开放）；\n"
        "- aspect_ratio：画面比例（'自动' / '1:1' / '16:9' / '9:16' / '3:4' / '4:3' / '2:3' / '3:2'）；\n"
        "- style：画面风格（32 种官方原生风格，默认'自动'，如'动漫'/'电影写真'/'水彩画'/'3D渲染'等）；\n"
        "- auto_confirm：若豆包提出参数调整建议，是否自动回复确认开始生成（默认 True）；\n"
        "- confirm_text：针对豆包提出的参数建议的自定义回复指令，为空且 auto_confirm=True 时默认回复'确认'。\n"
        "**参考图（重要）**：若用户要求基于某张已有图片生成（如'把这张图改成……''用这张图做……''参考这张图'等），"
        "必须把该图片的 media_id 传给 image_id，否则豆包看不到原图，只能凭文字凭空重画。\n"
        "提示词写具体画面（主体+场景+细节）。"
    )

    async def execute(
        self,
        prompt: Annotated[
            str,
            "图片描述：主体+场景+风格细节（如：一只戴墨镜的橘猫在沙滩上喝椰子）",
        ],
        image_id: Annotated[
            str,
            "参考图 media_id（图生图/改图，可多张用英文逗号分隔）；聊天图片占位符里的那段哈希。用户要求基于某张图生成/改图时必须传，否则豆包将凭文字凭空重画。纯文生图时留空",
        ] = "",
        model: Annotated[
            Literal["Seedream 4.5", "Seedream 4.0"],
            "生图模型（仅限免费模型：'Seedream 4.5' 日常出图 / 'Seedream 4.0' 基础生图；付费升级模型需 VIP 不开放选择）",
        ] = "Seedream 4.5",
        aspect_ratio: Annotated[
            Literal["自动", "1:1", "16:9", "9:16", "3:4", "4:3", "2:3", "3:2"],
            "画面比例，默认'自动'",
        ] = "自动",
        style: Annotated[
            Literal[
                "自动",
                "人像摄影",
                "电影写真",
                "中国风",
                "动漫",
                "3D渲染",
                "赛博朋克",
                "CG 动画",
                "水墨画",
                "油画",
                "古典",
                "水彩画",
                "卡通",
                "平面插画",
                "风景",
                "港风动漫",
                "像素风格",
                "荧光绘画",
                "彩铅画",
                "手办",
                "儿童绘画",
                "抽象",
                "锐笔插画",
                "二次元",
                "油墨印刷",
                "版画",
                "莫奈",
                "毕加索",
                "伦勃朗",
                "马蒂斯",
                "巴洛克",
                "复古动漫",
                "绘本",
            ],
            "生图风格（32 种官方原生风格），默认'自动'",
        ] = "自动",
        auto_confirm: Annotated[
            bool,
            "若豆包提出参数建议，是否自动回复确认开始生成（默认 True；设为 False 则返回建议内容等待大模型进一步决策）",
        ] = True,
        confirm_text: Annotated[
            str,
            "若豆包提出参数建议，自定义回复的确认或修改指令；为空且 auto_confirm=True 时默认回复'确认'",
        ] = "",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：激活图像生成技能 → 上传参考图 → 设置原生参数 → 提交描述 → 等图 → 批量下载 → 发送。

        Args:
            prompt: 图片描述。
            image_id: 参考图 media_id（图生图/改图，可空）。
            model: 生图模型。
            aspect_ratio: 画面比例。
            style: 生图风格。
            auto_confirm: 是否自动确认参数建议。
            confirm_text: 自定义确认/调整文案。

        Returns:
            tuple[bool, str | dict]: (是否成功, 结果或错误)。
        """
        if not prompt or not prompt.strip():
            return False, "图片描述不能为空"

        config = (
            self.plugin.config
            if isinstance(self.plugin.config, AiUiSnapshotConfig)
            else None
        )

        if config is None:
            return False, "插件配置缺失，无法执行"

        stream_id = self.get_current_stream_id()

        # 参考图：把 media_id（可多个）解析为本地文件路径供上传（图生图/改图）
        ref_paths: list[str] = []
        if image_id and image_id.strip():
            paths, err = await self._resolve_media_ids(image_id)
            if err:
                return False, err
            ref_paths = paths

        ok, paths, err, proposal = await generate_doubao_image(
            prompt.strip(),
            model=model,
            aspect_ratio=aspect_ratio,
            style=style,
            auto_confirm=auto_confirm,
            confirm_text=confirm_text,
            local_paths=ref_paths,
            stream_id=stream_id,
            timeout_s=config.web.reply_timeout,
        )

        if not ok:
            return False, err or "豆包生图失败"

        if not auto_confirm and proposal and not paths:
            return True, {
                "sent": False,
                "proposal": proposal,
                "summary": f"检测到豆包提出的参数建议：{proposal}，请进一步决策或指定 confirm_text 重新调用。",
            }

        # 批量读取本地文件逐张发送
        import base64

        sent_count = 0
        send_fail = False
        for path in paths:
            try:
                with open(path, "rb") as f:  # noqa: PTH123
                    img_b64 = base64.b64encode(f.read()).decode()
            except OSError as exc:
                logger.warning(f"读取生成图片失败 {path}: {exc}")
                continue
            sent = await send_api.send_image(
                img_b64, stream_id, processed_plain_text="[豆包生成图片]"
            )
            if sent:
                sent_count += 1
            else:
                send_fail = True
        if sent_count == 0:
            res: dict[str, Any] = {
                "sent": False,
                "paths": paths,
                "summary": "图片已生成但全部发送失败，文件在本地。",
            }
            if proposal:
                res["proposal"] = proposal
            return True, res
        partial = send_fail or sent_count < len(paths)
        res_data: dict[str, Any] = {
            "sent": True,
            "count": sent_count,
            "total": len(paths),
            "paths": paths,
            "model": model,
            "aspect_ratio": aspect_ratio,
            "style": style,
            "image_reference": bool(ref_paths),
            "summary": (
                f"已把豆包生成的 {sent_count}/{len(paths)} 张图片发到聊天"
                f"{'（部分发送失败）' if partial else ''}，"
                "请用拟人口吻简单描述画面即可。"
            ),
        }
        if proposal:
            res_data["proposal"] = proposal
        return True, res_data


class DoubaoGenerateVideoTool(_DoubaoToolBase):
    """提交豆包视频生成任务（后台等待，完成后系统唤醒）。"""

    name: str = "doubao_generate_video"

    description: str = (
        "用豆包的「视频生成」能力生成短视频。提交后立即返回（视频生成约需 2-5 分钟），"
        "任务转入后台等待；生成完成后你会收到系统消息通知（含视频本地路径 path），"
        "届时再根据语境决定是否用 doubao_send_video 把视频发给用户。\n"
        "支持原生 UI 参数精确配置：\n"
        "- image_id：参考图 media_id（图生视频），可空；\n"
        "- model：模型选择（仅限免费模型：'Seedance 2.0 Fast' 快速出片 / 'Seedance 2.0 Mini' 日常生成；付费升级模型需 VIP 不对模型开放）；\n"
        "- aspect_ratio：画面比例（'自动' / '16:9' / '9:16' / '3:4' / '4:3' / '1:1' / '21:9'）；\n"
        "- duration：视频时长（'4s' / '10s' / '15s'）；\n"
        "- auto_confirm：若豆包提出参数调整建议或确认卡片，是否自动回复确认开始生成（默认 True）；\n"
        "- confirm_text：针对豆包提出的参数卡片的自定义回复指令（如'按此生成'或'把镜头调慢一点'），为空且 auto_confirm=True 时默认回复'确认'。\n"
        "**图生视频（重要）**：若用户要求基于某张已有图片生成视频（如'保持原图''用这张图动起来''把这张图做成动画'等），"
        "必须把该图片的 media_id 传给 image_id（视频消息的封面图同理），否则豆包看不到原图，只能凭文字凭空重画导致形象不一致。\n"
        "提示词建议包含具体画面与运镜（主体+动作+镜头方向）。"
    )

    async def execute(
        self,
        prompt: Annotated[
            str, "视频描述：主体+动作+运镜（如：一只橘猫在海边奔跑，镜头跟随）"
        ],
        image_id: Annotated[
            str,
            "参考图 media_id（图生视频，可多张用英文逗号分隔）；聊天图片/视频封面占位符里的那段哈希。用户要求基于某张图生成/保持原图时必须传，否则豆包将凭文字凭空重画。纯文生视频时留空",
        ] = "",
        model: Annotated[
            Literal["Seedance 2.0 Fast", "Seedance 2.0 Mini"],
            "视频模型（仅限免费模型：'Seedance 2.0 Fast' 快速出片 / 'Seedance 2.0 Mini' 日常生成；付费升级模型需 VIP 不开放选择）",
        ] = "Seedance 2.0 Fast",
        aspect_ratio: Annotated[
            Literal["自动", "16:9", "9:16", "3:4", "4:3", "1:1", "21:9"],
            "视频画面比例，默认'自动'",
        ] = "自动",
        duration: Annotated[
            Literal["4s", "10s", "15s"],
            "视频时长，默认'10s'（可选 4s / 10s / 15s）",
        ] = "10s",
        auto_confirm: Annotated[
            bool,
            "若豆包提出参数建议或方向卡片，是否自动回复确认开始生成（默认 True；设为 False 则返回建议内容等待大模型进一步决策）",
        ] = True,
        confirm_text: Annotated[
            str,
            "若豆包提出参数建议卡片，自定义回复的确认或修改指令（如'确认'或'把背景改为傍晚'）；为空且 auto_confirm=True 时默认回复'确认'",
        ] = "",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：激活视频生成技能 → 上传参考图 → 设置原生参数 → 提交描述 → 转后台等待。

        Args:
            prompt: 视频描述。
            image_id: 参考图 media_id（图生视频，可空）。
            model: 视频模型。
            aspect_ratio: 画面比例。
            duration: 视频时长。
            auto_confirm: 是否自动确认参数建议。
            confirm_text: 自定义确认/调整文案。

        Returns:
            tuple[bool, str | dict]: (是否提交成功, 说明或结构化结果)。
        """
        if not prompt or not prompt.strip():
            return False, "视频描述不能为空"

        config = (
            self.plugin.config
            if isinstance(self.plugin.config, AiUiSnapshotConfig)
            else None
        )

        if config is None:
            return False, "插件配置缺失，无法执行"

        stream_id = self.get_current_stream_id()

        # 参考图：把 media_id（可多个）解析为本地文件路径供上传（图生视频）
        ref_paths: list[str] = []
        if image_id and image_id.strip():
            paths, err = await self._resolve_media_ids(image_id)
            if err:
                return False, err
            ref_paths = paths

        ok, msg, proposal = await submit_doubao_video(
            prompt.strip(),
            model=model,
            aspect_ratio=aspect_ratio,
            duration=duration,
            auto_confirm=auto_confirm,
            confirm_text=confirm_text,
            local_paths=ref_paths,
            stream_id=stream_id,
        )

        if not ok:
            return False, msg

        res_dict: dict[str, Any] = {
            "submitted": True if auto_confirm else False,
            "model": model,
            "aspect_ratio": aspect_ratio,
            "duration": duration,
            "image_reference": bool(ref_paths),
            "summary": (
                f"{msg} 在等待期间你可以正常聊天；收到完成通知后，"
                "用 doubao_send_video 发送视频（或视语境放弃发送）。"
            ),
        }
        if proposal:
            res_dict["proposal"] = proposal
        return True, res_dict


class DoubaoSendVideoTool(_DoubaoToolBase):
    """发送已生成的豆包视频文件到当前聊天。"""

    name: str = "doubao_send_video"

    description: str = (
        "把已生成完成的豆包视频文件发送到当前聊天。"
        "配合 doubao_generate_video 使用：视频生成完成的系统通知里会带"
        "文件路径（path），把该路径传入即可发送。"
    )

    async def execute(
        self,
        path: Annotated[str, "视频文件本地路径（生成完成通知中给出的 path）"],
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行：发送视频文件。

        Args:

            path: 视频文件本地路径。

        Returns:

            tuple[bool, str | dict]: (是否成功, 结果或错误)。

        """

        import pathlib as pl

        if not path or not pl.Path(path).is_file():
            return False, f"视频文件不存在: {path}"

        stream_id = self.get_current_stream_id()

        sent = await send_api.send_video(
            path, stream_id, processed_plain_text="[豆包生成视频]"
        )

        if not sent:
            return False, "视频发送失败"

        return True, {
            "sent": True,
            "summary": "视频已发出，请用拟人口吻简单介绍内容即可。",
        }


# 供插件装配导出的豆包工具类列表
DOUBAO_TOOLS: list[type[BaseTool]] = [
    AskDoubaoTool,
    DoubaoSnapshotTool,
    DoubaoHistoryTool,
    DoubaoStateTool,
    DoubaoGenerateImageTool,
    DoubaoGenerateVideoTool,
    DoubaoSendVideoTool,
]
