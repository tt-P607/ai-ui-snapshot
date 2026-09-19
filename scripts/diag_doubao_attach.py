"""豆包附件失败最小复现：共享会话（无头）内上传小图全链路跟踪。

verify 场景 1（ok=False 且 error 为空）与场景 2（未找到文件输入）复现：
走与 ask_doubao 完全相同的会话获取路径（manager.get，无头），逐步打印
每一步状态：页面 URL、file input 数量、编辑器存在性、上传返回、提问返回。

用法（项目根目录，需已登录）：
    uv run python plugins/ai_ui_snapshot/scripts/diag_doubao_attach.py
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

from plugins.ai_ui_snapshot.services.base.browser_session import get_manager  # noqa: E402
from plugins.ai_ui_snapshot.services.doubao.actions import DoubaoActions  # noqa: E402

TEST_DIR = _PROJECT_ROOT / "tmp" / "doubao_attach_test"


async def main() -> int:
    """逐步跟踪无头共享会话内的附件上传链路。"""
    print("== 豆包附件失败最小复现（无头共享会话）==")
    manager = get_manager()
    session = await manager.get("verify_attach", theme="doubao")
    session.hold()
    manager.touch("verify_attach", theme="doubao")
    try:
        page = session.page
        print(f"页面 URL: {page.url}")
        state = await page.evaluate(
            """() => ({
                fileInputs: document.querySelectorAll('input[type="file"]').length,
                editor: Boolean(document.querySelector('div.tiptap.ProseMirror')),
                title: document.title,
                bodyHead: (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 120)
            })"""
        )
        print(f"状态: {state}")

        actions = DoubaoActions(page, touch_cb=lambda: manager.touch("verify_attach", theme="doubao"))
        # 新对话
        ok = await actions.new_chat()
        print(f"new_chat: {ok}, URL: {page.url}")
        await page.wait_for_timeout(2000)
        state2 = await page.evaluate("() => document.querySelectorAll('input[type=\"file\"]').length")
        print(f"新对话后 file input 数: {state2}")
        # 上传
        ok, msg = await actions.upload_file(str(TEST_DIR / "test_small.jpg"))
        print(f"upload: ok={ok} msg={msg}")
        if not ok:
            return 1
        # 提问
        ok, msg = await actions.ask("用一句话描述这张图")
        print(f"ask: ok={ok} msg={msg}")
        done, reply = await actions.wait_reply_done(timeout_s=120)
        print(f"reply_done: {done}")
        print(f"reply: {reply[:150]}")
        return 0 if done else 1
    finally:
        session.release()
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
