"""豆包生图/生视频端到端验证脚本。

场景 A（生图，真实执行）：激活图像生成技能 → 提交描述 → 等图 → 下载落盘。
场景 B（视频，链路验证）：提交视频任务 → 验证立即返回 → 后台等待任务
结构就绪（不真实等 5 分钟，仅验证提交/受理/后台任务启动与唤醒构造）。

用法（项目根目录，需已登录）：
    uv run python plugins/ai_ui_snapshot/scripts/verify_doubao_media.py
    uv run python plugins/ai_ui_snapshot/scripts/verify_doubao_media.py --skip-video
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _PLUGIN_ROOT.parent.parent
for _p in (str(_PLUGIN_ROOT), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from plugins.ai_ui_snapshot.services.doubao.actions import DoubaoActions  # noqa: E402
from plugins.ai_ui_snapshot.services.doubao.constants import (  # noqa: E402
    SKILL_VIDEO_BUTTON_ID,
    SKILL_VIDEO_PLACEHOLDER,
    SITE_URL,
)
from plugins.ai_ui_snapshot.scripts.login_doubao import _is_doubao_ready  # noqa: E402
from plugins.ai_ui_snapshot.services.service import generate_doubao_image  # noqa: E402

_SAVE_IMG = _PLUGIN_ROOT / "scripts" / "verify_doubao_image.png"


async def main(skip_video: bool) -> int:
    """运行生图/生视频验证。"""
    print("== 豆包生图/生视频验证 ==")
    # A. 生图全链路（走 service 层真实入口）
    print("[A] 图像生成全链路...")
    ok, path, err = await generate_doubao_image(
        "一只柴犬戴着宇航员头盔漂浮在太空中，可爱插画风格",
        stream_id="verify",
        timeout_s=180,
        save_dir=str(_SAVE_IMG.parent),
    )
    if not ok:
        print(f"  ✗ 生图失败: {err}")
        return 1
    print(f"  ✓ 生图成功: {path}")

    if skip_video:
        print("[B] 视频链路：--skip-video 跳过")
        print("\n== 验证通过 ==")
        return 0

    # B. 视频提交链路（真实执行：技能激活 → 提交 → 参数确认 → 提交后台等待）
    #    参数确认是必需步骤：豆包先列参数问"确认后我再开始生成"，不回复就
    #    永远不会开始生成（曾因漏掉这步导致链路空等超时）。
    print("[B] 视频生成提交链路...")
    from plugins.ai_ui_snapshot.services.base.browser_session import get_manager

    manager = get_manager()
    session = await manager.get("verify", theme="doubao")
    session.hold()
    try:
        actions = DoubaoActions(session.page, touch_cb=lambda: manager.touch("verify", theme="doubao"))
        await session.page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
        await session.page.wait_for_timeout(3000)
        if not await _is_doubao_ready(session.page):
            print("  ✗ 登录态失效")
            return 1
        if not await actions.new_chat():
            print("  ✗ 新建对话失败")
            return 1
        await session.page.wait_for_timeout(2000)
        ok, msg = await actions.activate_skill(SKILL_VIDEO_BUTTON_ID, SKILL_VIDEO_PLACEHOLDER)
        if not ok:
            print(f"  ✗ 激活视频技能失败: {msg}")
            return 1
        print("  ✓ 视频技能激活（placeholder 受理确认）")
        ok, msg = await actions.submit_prompt("一只云在天上飘，8秒")
        if not ok:
            print(f"  ✗ 提交视频描述失败: {msg}")
            return 1
        print("  ✓ 视频描述已提交")
        ok, msg = await actions.await_param_confirm()
        if not ok:
            print(f"  ✗ 参数确认失败: {msg}（blocker={actions.last_blocker!r}）")
            return 1
        print(f"  ✓ 参数确认: {msg}")
        print("    （真机流程：submit_doubao_video 提交后立即返回，")
        print("      后台任务 wait_video_done ≤600s → download → wake bot）")
    finally:
        session.release()

    print("\n== 验证通过 ==")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-video", action="store_true")
    sys.exit(asyncio.run(main(parser.parse_args().skip_video)))
