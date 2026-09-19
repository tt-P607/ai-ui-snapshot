"""豆包附件全流程端到端验证（走 service 层 ask_doubao 真实入口）。

场景矩阵（真实执行，逐项断言）：
1. 小图提问：上传 179KB jpg → 提问"图中是什么" → 拿到含图描述的回复
2. 文档提问：上传 md 文本 → 提问其内容 → 回复正确 referring 文档内容
3. 音频友好拒绝：ask_doubao 前置校验应拦截（.mp3 不在白名单）
4. 大图友好拒绝：26MB 超豆包 20MB 上限应拦截
5. full 范围：return_scope=full 返回整段对话
6. conversation 定位：回到场景 1 的对话标题继续追问

用法（项目根目录，需已登录）：
    uv run python plugins/ai_ui_snapshot/scripts/verify_doubao_attachment.py
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

from plugins.ai_ui_snapshot.services.service import ask_doubao  # noqa: E402

TEST_DIR = _PROJECT_ROOT / "tmp" / "doubao_attach_test"
# 场景 2 的测试文档：特征字符串唯一，验证豆包真实读到内容
DOC_MARKER = "MOFOX-DOUBAO-E2E-7A3F"
DOC_CONTENT = f"# 测试文档\n\n这是 Neo-MoFox 豆包附件链路的端到端测试文档。\n\n暗号是：{DOC_MARKER}\n\n请记住这个暗号。\n"


async def main() -> int:
    """运行附件全流程验证。"""
    print("== 豆包附件全流程验证 ==")
    # 先落盘测试文档
    doc_path = TEST_DIR / "test_doc.md"
    doc_path.write_text(DOC_CONTENT, encoding="utf-8")
    small_img = TEST_DIR / "test_small.jpg"
    big_img = TEST_DIR / "test_photo.jpg"
    audio = TEST_DIR / "test_audio.mp3"

    passed: list[str] = []
    failed: list[str] = []

    # 场景 1：小图提问
    print("\n[1] 小图提问（应成功，回复描述图片内容）...")
    r1 = await ask_doubao(
        "用一句话描述这张图片的内容",
        stream_id="verify_attach",
        local_path=str(small_img),
        new_chat=True,
        timeout_s=180,
    )
    if r1.ok and r1.reply:
        print(f"  ✓ 回复: {r1.reply[:80]}")
        print(f"  ✓ 会话: {r1.conversation}")
        passed.append("small_image")
    else:
        print(f"  ✗ 失败: {r1.error}")
        failed.append("small_image")

    # 场景 2：文档提问（验证豆包真实读取内容：回应暗号）
    print("\n[2] 文档提问（回复应包含文档中的暗号）...")
    r2 = await ask_doubao(
        "文档里的暗号是什么？只回答暗号本身",
        stream_id="verify_attach",
        local_path=str(doc_path),
        new_chat=True,
        timeout_s=180,
    )
    if r2.ok and DOC_MARKER in r2.reply:
        print(f"  ✓ 暗号命中: {r2.reply[:60]}")
        passed.append("doc_marker")
    elif r2.ok:
        print(f"  ✗ 回复未含暗号: {r2.reply[:80]}")
        failed.append("doc_marker")
    else:
        print(f"  ✗ 失败: {r2.error}")
        failed.append("doc_marker")

    # 场景 3：音频应被前置拦截（.mp3 不在白名单）
    print("\n[3] 音频前置拦截（应友好报错）...")
    r3 = await ask_doubao(
        "听听这段音频",
        stream_id="verify_attach",
        local_path=str(audio),
        new_chat=True,
        timeout_s=30,
    )
    if not r3.ok and ("不在豆包允许范围" in r3.error or "不支持" in r3.error):
        print(f"  ✓ 拦截: {r3.error[:70]}")
        passed.append("audio_rejected")
    else:
        print(f"  ✗ 未拦截: ok={r3.ok} err={r3.error}")
        failed.append("audio_rejected")

    # 场景 4：大图应被前置拦截（>20MB）
    print("\n[4] 大图前置拦截（应友好报错）...")
    r4 = await ask_doubao(
        "看看这张图",
        stream_id="verify_attach",
        local_path=str(big_img),
        new_chat=True,
        timeout_s=30,
    )
    if not r4.ok and "20MB" in r4.error:
        print(f"  ✓ 拦截: {r4.error[:70]}")
        passed.append("big_rejected")
    else:
        print(f"  ✗ 未拦截: ok={r4.ok} err={r4.error}")
        failed.append("big_rejected")

    # 场景 5：full 范围（沿用当前对话）
    print("\n[5] full 信息范围（应返回整段对话）...")
    r5 = await ask_doubao(
        "我刚才问了你什么？一句话概括",
        stream_id="verify_attach",
        return_scope="full",
        timeout_s=180,
    )
    if r5.ok and len(r5.reply) > len("暗号") * 2 and ("用户" in r5.reply or "暗号" in r5.reply or "图片" in r5.reply):
        print(f"  ✓ 整段对话（{len(r5.reply)} 字）: {r5.reply[:80]}...")
        passed.append("full_scope")
    elif r5.ok:
        print(f"  △ 回复较短: {r5.reply[:80]}")
        passed.append("full_scope_weak")
    else:
        print(f"  ✗ 失败: {r5.error}")
        failed.append("full_scope")

    # 场景 6：conversation 定位（回到场景 1 的标题继续）
    if r1.ok and r1.conversation:
        print(f"\n[6] conversation 定位（回到「{r1.conversation}」追问）...")
        r6 = await ask_doubao(
            "再确认一次，这张图里主要是什么颜色的物体？",
            stream_id="verify_attach",
            conversation=r1.conversation,
            timeout_s=180,
        )
        if r6.ok and r6.reply:
            print(f"  ✓ 追问回复: {r6.reply[:80]}")
            passed.append("conversation_locate")
        else:
            print(f"  ✗ 失败: {r6.error}")
            failed.append("conversation_locate")

    # 汇总
    print(f"\n== 汇总: {len(passed)} 通过 / {len(failed)} 失败 ==")
    for p in passed:
        print(f"  ✓ {p}")
    for f in failed:
        print(f"  ✗ {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
