"""豆包非媒体功能全量验证（生图/生视频豁免；共享会话现为强制有头模式）。

场景（全部真实执行，除注明外）：
A. 附件剩余三场景：大图 20MB 拦截（本地）/ full 范围 / 对话定位追问
B. 纯文字提问（共享会话，有头）：档位默认、回复、会话标题
C. doubao_snapshot：当前对话长截图（data_uri 非空）
D. doubao_history：list 列表 / open 进入（上下文含此前内容）
E. doubao_state：档位读取
F. reset_browser(site=db)：会话销毁与重建

用法（项目根目录，需已登录）：
    uv run python plugins/ai_ui_snapshot/scripts/verify_doubao_full.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _PLUGIN_ROOT.parent.parent
for _p in (str(_PLUGIN_ROOT), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from plugins.ai_ui_snapshot.services.service import (  # noqa: E402
    ask_doubao,
    capture_doubao_snapshot,
)

TEST_DIR = _PROJECT_ROOT / "tmp" / "doubao_attach_test"
PASSED: list[str] = []
FAILED: list[str] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    mark = "✓" if ok else "✗"
    print(f"  {mark} {name}" + (f" | {detail[:90]}" if detail else ""))


async def main() -> int:
    """运行非媒体全量验证。"""
    print("== 豆包非媒体全量验证 ==")

    # A1. 大图 20MB 本地拦截（不访问网页）
    print("\n[A1] 大图 >20MB 前置拦截...")
    r = await ask_doubao(
        "看看这张图",
        stream_id="verify_full",
        local_path=str(TEST_DIR / "test_photo.jpg"),
        timeout_s=30,
    )
    _record("big_image_rejected", not r.ok and "20MB" in r.error, r.error[:70] if not r.ok else "")

    # B. 纯文字提问（共享会话，有头）
    print("\n[B] 纯文字提问（共享会话-有头）...")
    r_text = await ask_doubao(
        "用一句话说明什么是二分查找",
        stream_id="verify_full",
        new_chat=True,
        timeout_s=180,
    )
    _record("text_ask", r_text.ok and len(r_text.reply) > 10,
            r_text.reply[:70] if r_text.ok else r_text.error)
    title = r_text.conversation if r_text.ok else ""

    # A2. full 范围（共享会话沿用当前对话）
    print("\n[A2] full 信息范围...")
    r = await ask_doubao(
        "我刚才问了你什么？只复述问题本身",
        stream_id="verify_full",
        return_scope="full",
        timeout_s=180,
    )
    _record("full_scope", r.ok and "二分" in (r.reply or ""),
            r.reply[:70] if r.ok else r.error)

    # A3. 对话定位（回到 B 的标题追问）
    if title:
        print(f"\n[A3] 对话定位（回到「{title}」）...")
        r = await ask_doubao(
            "把你刚才的回答压缩成十个字以内",
            stream_id="verify_full",
            conversation=title,
            timeout_s=180,
        )
        _record("conversation_locate", r.ok and len(r.reply or "") > 0,
                (r.reply or r.error)[:70])
    else:
        _record("conversation_locate", False, "无标题可定位（上一步失败）")

    # C. snapshot（共享会话当前对话）
    print("\n[C] doubao_snapshot 截图...")
    r_snap = await capture_doubao_snapshot(stream_id="verify_full")
    ok_snap = r_snap.ok and len(r_snap.data_uri) > 0 and r_snap.data_uri[0].startswith("data:image")
    _record("snapshot", ok_snap,
            f"{len(r_snap.data_uri)} 张, {len(r_snap.data_uri[0]) // 1024 if ok_snap else 0}KB级" if ok_snap else (r_snap.error or "空结果"))

    # D. history（直接走 actions；共享会话已建立）
    print("\n[D] doubao_history list/open...")
    from plugins.ai_ui_snapshot.services.base.browser_session import get_manager
    from plugins.ai_ui_snapshot.services.doubao.actions import DoubaoActions

    manager = get_manager()
    session = await manager.get("verify_full", theme="doubao")
    session.hold()
    try:
        actions = DoubaoActions(
            session.page,
            touch_cb=lambda: manager.touch("verify_full", theme="doubao"),
        )
        items = await actions.list_conversations()
        _record("history_list", len(items) > 0, f"{len(items)} 项: {items[:3]}")
        # open：进入列表中的标题（优先找非当前项）
        target = next((t for t in items if t != title), items[0] if items else "")
        if target:
            ok_open = await actions.open_conversation(target)
            ctx = await actions.get_conversation_text(scope="full") if ok_open else ""
            _record("history_open", ok_open and len(ctx) > 0,
                    f"进入「{target}」，上下文 {len(ctx)} 字")
        else:
            _record("history_open", False, "无可进入的会话")
    finally:
        session.release()

    # E. state（档位读取）
    print("\n[E] doubao_state 档位...")
    session = await manager.get("verify_full", theme="doubao")
    session.hold()
    try:
        actions = DoubaoActions(session.page)
        model = await actions.get_model()
        _record("state_model", model in ("快速", "专家"), f"当前档位: {model or '未知'}")
    finally:
        session.release()

    # F. reset_browser（close 后再 get 应重建）
    print("\n[F] reset_browser(doubao)...")
    await manager.close("verify_full", theme="doubao")
    session = await manager.get("verify_full", theme="doubao")
    session.hold()
    try:
        ready = await DoubaoActions(session.page).ensure_logged_in(timeout_s=30)
        _record("reset_and_rebuild", ready, "重建后登录预检通过")
    finally:
        session.release()

    # 汇总
    print(f"\n== 汇总: {len(PASSED)} 通过 / {len(FAILED)} 失败 ==")
    for x in FAILED:
        print(f"  ✗ {x}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
