"""豆包视频生成后台等待与主动唤醒。

视频生成耗时约 5 分钟，远超一次工具调用的合理等待时长。方案：
1. ``generate_doubao_video`` 工具提交描述后立即返回（"已开始生成"）
2. 本模块用 task_manager 起守护任务，在独立浏览器上轮询
   ``wait_video_done``（最长 video_timeout_s）
3. 完成后下载视频到本地，随后**直接唤醒**目标聊天流（见
   :func:`wake_stream_with_video`），让 LLM bot 自主决定是否用
   ``doubao_send_video`` 把视频发出去
4. 结束时经 closer 回调关闭媒体浏览器上下文

唤醒为何要"强提及"：系统通知若只当普通群消息注入，会先被
``neo_default_chatter:preprocess`` 链拦截——概率门按提及分给加成、
``neo_interest_filter`` 按提及分算兴趣值（未提及=0 分，实测 0.60 < 阈值
0.72 被拦），导致视频生成完了 bot 永远不知道。注入时带上
``extra.at_users`` 指向 bot 自身，使通知被识别为"明确 @ 了 bot"
（强提及），兴趣值随之达标放行，同时吃概率门的强提及加成。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.stream_api import get_stream
from src.app.plugin_system.types import Message, MessageType

logger = get_logger("ai_ui_snapshot.doubao_video_wake")

# 视频保存目录（与其他豆包产物一致放 profile 下）
VIDEO_SAVE_DIR = "data/ai_ui_snapshot_profile/doubao/videos"


async def start_video_background_wait(
    stream_id: str,
    actions: Any,
    detail: str,
    closer: Any | None = None,
    video_timeout_s: int = 600,
    lock: Any | None = None,
) -> bool:
    """启动后台守护任务等待视频完成并唤醒 bot（立即返回）。

    Args:
        stream_id: 目标聊天流 ID（唤醒目标）。
        actions: 绑定媒体浏览器的 DoubaoActions。
        detail: 结果摘要前缀（视频描述）。
        closer: 浏览器关闭回调（任务结束时调用）。
        video_timeout_s: 等待完成的超时秒数。
        lock: 媒体互斥锁（提交方持行中移交；任务结束后由本任务释放）。

    Returns:
        bool: 是否成功提交后台任务。
    """
    from src.kernel.concurrency import get_task_manager

    try:
        get_task_manager().create_task(
            wait_video_and_wake(stream_id, actions, detail, closer, video_timeout_s, lock),
            name=f"doubao_video_{stream_id[:16]}",
            daemon=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - 提交失败
        logger.error(f"提交豆包视频后台任务失败: {exc}", exc_info=True)
        return False


async def _start_stream_loop(stream_id: str) -> bool:
    """启动目标流的驱动器任务（立即处理已注入的未读消息）。

    框架公开的 ``api.stream_api`` 尚未提供流循环启动能力，此处直连
    ``src.core.transport.distribution.stream_loop_manager``（同
    ``ndfc_kokoro_bridge`` / ``cross_stream_relay`` / ``anima_chatter``
    的官方插件先例）；封装在本函数内，避免内部依赖扩散。

    Args:
        stream_id: 目标聊天流 ID。

    Returns:
        bool: 是否成功启动（循环已在运行时由框架直接返回 True）。
    """
    try:
        from src.core.transport.distribution.stream_loop_manager import (
            get_stream_loop_manager,
        )

        loop_manager = get_stream_loop_manager()
        if not loop_manager.is_running:
            await loop_manager.start()
        return await loop_manager.start_stream_loop(stream_id)
    except Exception as exc:  # noqa: BLE001 - 唤醒失败不影响已落盘的视频
        logger.warning(f"启动流循环失败 stream={stream_id[:12]}: {exc}")
        return False


async def wake_stream_with_video(stream_id: str, ok: bool, detail: str, video_path: str) -> bool:
    """向目标聊天流注入"视频生成完成"系统消息并直接唤醒 bot。

    注入消息带 ``extra.at_users``（指向 bot 自身）使其被识别为强提及，
    从而通过 ``neo_default_chatter:preprocess`` 链里的概率门与
    ``neo_interest_filter`` 兴趣值筛（未标记的系统通知会被当普通群消息
    拦下，实测兴趣值 0.60 < 阈值 0.72）；注入后再启动流循环，
    让 bot 立刻看到通知并决策是否发送视频。

    Args:
        stream_id: 目标聊天流 ID。
        ok: 生成是否成功。
        detail: 结果摘要（失败原因或视频描述/时长）。
        video_path: 已下载的视频本地路径（失败时为空）。

    Returns:
        bool: 是否成功注入唤醒。
    """
    try:
        chat_stream = await get_stream(stream_id)
        if chat_stream is None:
            logger.warning(f"视频唤醒失败：流不在内存 stream={stream_id[:12]}")
            return False
        if ok:
            content = (
                f"[豆包视频生成完成] {detail}。视频已保存到本地 {video_path}，"
                "请你根据对话语境决定是否用发送工具把视频发给用户（可先用一两句话自然告知）。"
            )
        else:
            content = f"[豆包视频生成失败] {detail}。请酌情告知用户（或静默放弃）。"
        # 强提及标记：让通知稳定通过概率门与兴趣值筛（否则会被当普通群消息拦截）
        bot_id = str(chat_stream.bot_id or "")
        extra: dict[str, Any] = {"sender_role": "system"}
        raw_data: dict[str, Any] | None = None
        if bot_id:
            extra["at_users"] = [{"user_id": bot_id}]
            # self_id 供流循环层的提及判定直接取值，避免其回退查流缓存（私有属性）
            raw_data = {"self_id": bot_id}
        trigger = Message(
            message_id=f"doubao_video_{uuid.uuid4().hex[:12]}",
            time=time.time(),
            content=content,
            processed_plain_text=content,
            message_type=MessageType.TEXT,
            sender_id="system",
            sender_name="系统",
            platform=chat_stream.platform or "",
            chat_type=chat_stream.chat_type if hasattr(chat_stream, "chat_type") else "private",
            stream_id=stream_id,
            raw_data=raw_data,
            **extra,
        )
        chat_stream.context.add_unread_message(trigger)
        woken = await _start_stream_loop(stream_id)
        logger.info(
            f"已注入豆包视频结果消息 stream={stream_id[:12]} ok={ok} "
            f"强提及={'是' if bot_id else '否'} 唤醒={woken}"
        )
        return True
    except Exception as exc:  # noqa: BLE001 - 唤醒失败
        logger.warning(f"注入视频唤醒消息失败: {exc}")
        return False


async def wait_video_and_wake(
    stream_id: str,
    actions: Any,
    detail: str,
    closer: Any | None = None,
    video_timeout_s: int = 600,
    lock: Any | None = None,
) -> None:
    """后台守护任务：等待视频完成 → 下载 → 唤醒 bot → 关浏览器 → 释放锁。

    异常不向外抛（守护任务）；无论成败最后执行 closer 关闭媒体浏览器，
    并释放提交方移交的媒体互斥锁。

    Args:
        stream_id: 目标聊天流 ID。
        actions: 绑定媒体浏览器的 DoubaoActions。
        detail: 结果摘要前缀。
        closer: 浏览器关闭回调（同步或协程均可）。
        video_timeout_s: 等待完成的超时秒数。
        lock: 媒体互斥锁（提交方移交；任务结束时释放）。
    """
    try:
        ok, info = await actions.wait_video_done(timeout_s=video_timeout_s)
        if not ok:
            await wake_stream_with_video(stream_id, False, f"等待超时（>{video_timeout_s}s）", "")
            return
        video_path = await actions.download_generated_video(VIDEO_SAVE_DIR) or ""
        if not video_path:
            await wake_stream_with_video(
                stream_id, False, "视频已生成但下载失败（可稍后人工在豆包页面查看）", ""
            )
            return
        dur = info.get("duration", 0) if isinstance(info, dict) else 0
        await wake_stream_with_video(
            stream_id, True, f"{detail}（时长约 {dur}s）" if dur else detail, video_path
        )
    except Exception as exc:  # noqa: BLE001 - 守护任务吞异常
        logger.error(f"豆包视频后台等待异常: {exc}", exc_info=True)
        try:
            await wake_stream_with_video(stream_id, False, f"后台任务异常: {exc}", "")
        except Exception:  # noqa: BLE001
            pass
    finally:
        if closer is not None:
            try:
                result = closer()
                if hasattr(result, "__await__"):
                    await result
            except Exception:  # noqa: BLE001 - 关闭异常忽略
                pass
        # 释放提交方移交的媒体互斥锁（后台任务结束后才允许新的媒体任务）
        if lock is not None:
            try:
                lock.release()
            except Exception:  # noqa: BLE001 - 重复释放等异常忽略
                pass
