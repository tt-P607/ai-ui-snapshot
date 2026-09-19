"""豆包截图冒烟脚本（独立于插件运行时，人工确认产物质量）。

复用登录态 profile，走真实插件类 DoubaoActions 全链路：新对话 → 提问 →
等回复 → 截图（含浏览器外壳装饰），产物落盘 scripts/verify_doubao_*.png。

用法（项目根目录，需已登录）：
    uv run python plugins/ai_ui_snapshot/scripts/verify_doubao_screenshot.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import time
from typing import Any

import playwright.async_api as pw

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _PLUGIN_ROOT.parent.parent
for _p in (str(_PLUGIN_ROOT), str(_PROJECT_ROOT), str(_PROJECT_ROOT / "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 探测/验证脚本独立于插件运行时：使用 importlib 避免被插件静态检查器误判为绝对导入
import importlib  # noqa: E402

DoubaoActions = importlib.import_module("ai_ui_snapshot.services.doubao.actions").DoubaoActions  # noqa: E402
SITE_URL = importlib.import_module("ai_ui_snapshot.services.doubao.constants").SITE_URL  # noqa: E402

_PROFILE_ROOT = pathlib.Path(
    __import__("os").environ.get("AI_UI_SNAPSHOT_PROFILE_ROOT", _PROJECT_ROOT / "data/ai_ui_snapshot_profile")
)
PROFILE_DIR = _PROFILE_ROOT / "doubao"
OUT_DIR = _PLUGIN_ROOT / "scripts"
QUESTION = "用一句话介绍你自己"
REPLY_TIMEOUT_S = 180


def _default_chrome_path() -> str:
    """探测正式版 Chrome 路径（找不到返回空）。"""
    candidates = (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    )
    for cand in candidates:
        if pathlib.Path(cand).is_file():
            return cand
    return ""


async def main() -> int:
    """运行冒烟：提问 → 回复 → 截图 → 历史列表。"""
    chrome = _default_chrome_path()
    print("== 豆包冒烟验证 ==")
    if not PROFILE_DIR.exists():
        print("  ✗ 未找到登录态，请先运行 scripts/login_doubao.py")
        return 1

    async with pw.async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(PROFILE_DIR),
            "headless": False,
            "viewport": {"width": 1440, "height": 900},
            "device_scale_factor": 2,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-infobars"],
            "ignore_default_args": ["--enable-automation"],
        }
        if chrome:
            launch_kwargs["executable_path"] = chrome
        context = await p.chromium.launch_persistent_context(**launch_kwargs)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)

            actions = DoubaoActions(
                page,
                max_screenshot_height=8000,
                decoration_enabled=True,
                decoration_theme="auto",
            )

            # 1. 状态读取
            model = await actions.get_model()
            print(f"  当前档位: {model or '未知'}")

            # 2. 新对话 + 提问 + 等回复
            print(f"  提问: {QUESTION}")
            if not await actions.new_chat():
                print("  ⚠ 新对话点击未命中（继续在当前会话提问）")
            await page.wait_for_timeout(1500)
            ok, msg = await actions.ask(QUESTION)
            if not ok:
                print(f"  ✗ 提问失败: {msg}")
                return 1
            t0 = time.monotonic()
            done, reply = await actions.wait_reply_done(timeout_s=REPLY_TIMEOUT_S)
            cost = time.monotonic() - t0
            if not done:
                print(f"  ✗ 等待回复超时（{REPLY_TIMEOUT_S}s）；已取文本: {reply[:120]}")
                return 1
            print(f"  ✓ 回复完成（{cost:.0f}s，{len(reply)} 字）")
            print(f"  回复预览: {reply[:150]}")

            # 3. 截图（含外壳装饰）
            data_uris = await actions.screenshot("conversation")
            if not data_uris:
                print("  ✗ 截图失败")
                return 1
            import base64

            for idx, uri in enumerate(data_uris):
                raw = base64.b64decode(uri.split(",", 1)[1])
                out = OUT_DIR / f"verify_doubao_{idx}.png"
                out.write_bytes(raw)
                print(f"  ✓ 截图落盘: {out.relative_to(_PROJECT_ROOT)}（{len(raw) // 1024} KB）")

            # 4. 会话标题与历史列表
            title = await actions.get_active_conversation_title()
            conv_id = await actions.get_active_conversation_id()
            histories = await actions.list_conversations()
            print(f"  当前会话: {title}（ID: {conv_id}）")
            print(f"  历史会话({len(histories)}): {histories[:5]}")
            print("\n== 冒烟通过 ==")
            return 0
        finally:
            await context.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
