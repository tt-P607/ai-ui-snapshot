"""豆包 DOM 三轮探测（最终补充：AI 消息块 / 滚动容器 / 模型菜单 / 加号菜单）。

二轮已确认：用户气泡 bg-g-send-msg-bubble-bg、会话项 a.group/conversation-item、
工具栏按钮带 data-dbx-name。本轮补齐：
1. 消息列表容器与滚动容器（用户消息行向上找 overflow 祖先）
2. AI 消息块结构（用户行的兄弟块，md- 前缀 markdown 组件）
3. 完成标记真实文案（含"生成"的文本元素全量 dump）
4. "豆包 快速"模型菜单（含深度思考/联网入口）
5. 加号按钮菜单（附件/联网搜索等）

用法（项目根目录，需已登录）：
    uv run python plugins/ai_ui_snapshot/scripts/probe_doubao_dom3.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from typing import Any

import playwright.async_api as pw

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _PLUGIN_ROOT.parent.parent
for _p in (str(_PLUGIN_ROOT), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from plugins.ai_ui_snapshot.scripts.login_doubao import _is_doubao_ready  # noqa: E402
from plugins.ai_ui_snapshot.services.doubao.constants import SITE_URL  # noqa: E402

_PROFILE_ROOT = pathlib.Path(
    __import__("os").environ.get("AI_UI_SNAPSHOT_PROFILE_ROOT", _PROJECT_ROOT / "data/ai_ui_snapshot_profile")
)
PROFILE_DIR = _PROFILE_ROOT / "doubao"
REPORT_DIR = _PLUGIN_ROOT / "scripts" / "doubao_probe_report"


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


# 1. 消息结构：从用户气泡行向上找滚动祖先 + 兄弟消息块 dump
PROBE_MESSAGE_TREE_SCRIPT = """() => {
    // 用户消息气泡（二轮确认的选择器）
    const bubble = document.querySelector('[class*="send-msg-bubble"]');
    if (!bubble) return {error: 'no user bubble'};
    // 消息行（justify-end 的行）与消息列表
    let row = bubble;
    while (row && !(row.className || '').toString().includes('justify-end')) row = row.parentElement;
    let list = row ? row.parentElement : null;
    // 继续向上找滚动容器（overflow auto/scroll 且内容更高）
    const scrollInfo = [];
    let cur = list;
    let scroller = null;
    for (let i = 0; i < 12 && cur; i++) {
        const st = getComputedStyle(cur);
        const isScroll = (st.overflowY === 'auto' || st.overflowY === 'scroll')
            && cur.scrollHeight > cur.clientHeight + 20;
        scrollInfo.push({
            tag: cur.tagName, classes: (cur.className || '').toString().slice(0, 120),
            overflowY: st.overflowY, scrollHeight: cur.scrollHeight, clientHeight: cur.clientHeight,
            isScroll
        });
        if (isScroll && !scroller) scroller = cur;
        cur = cur.parentElement;
    }
    // 列表中的兄弟消息块（用户行 + AI 块，最多 6 个）
    const blocks = [];
    if (list) {
        Array.from(list.children).slice(0, 6).forEach(b => {
            blocks.push({
                tag: b.tagName,
                classes: (b.className || '').toString().slice(0, 150),
                textPreview: (b.innerText || '').replace(/\\s+/g, ' ').slice(0, 100),
                childCount: b.children.length,
                // 首层子元素 class（md-box-root 等 markdown 组件线索）
                childClasses: Array.from(b.children).slice(0, 4).map(c =>
                    c.tagName + '.' + (c.className || '').toString().slice(0, 100))
            });
        });
    }
    // 全页 md- 前缀组件统计（AI 回复 markdown 渲染器）
    const mdClasses = {};
    document.querySelectorAll('[class*="md-"]').forEach(el => {
        Array.from((el.className || '').toString().split(/\\s+/)).filter(c => c.startsWith('md-')).forEach(c => {
            mdClasses[c] = (mdClasses[c] || 0) + 1;
        });
    });
    return {rowFound: Boolean(row), listClasses: list ? (list.className || '').toString().slice(0, 200) : '',
            scrollerFound: Boolean(scroller), scrollInfo: scrollInfo.slice(0, 10),
            blocks, mdClasses};
}"""

# 2. 完成标记：文本含"生成"且长度 < 30 的元素（真实文案）
PROBE_MARKER_SCRIPT = """() => {
    const hits = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
        const t = (node.textContent || '').replace(/\\s+/g, ' ').trim();
        if (t && t.length <= 30 && /生成/.test(t)) {
            const el = node.parentElement;
            hits.push({
                text: t,
                tag: el ? el.tagName : '',
                classes: el ? (el.className || '').toString().slice(0, 150) : '',
                parentClasses: el && el.parentElement ? (el.parentElement.className || '').toString().slice(0, 100) : ''
            });
        }
    }
    return hits.slice(0, 12);
}"""

# 3. 弹出菜单 dump（模型菜单 / 加号菜单通用：role=menu/menuitem/dialog/listbox/option）
PROBE_MENU_SCRIPT = """() => {
    const sels = ['[role="menu"]', '[role="menuitem"]', '[role="dialog"]', '[role="listbox"]',
                  '[role="option"]', '[class*="popover"]', '[class*="dropdown"]', '[class*="popup"]'];
    const found = [];
    const seen = new Set();
    sels.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => {
            if (seen.has(el)) return;
            seen.add(el);
            const r = el.getBoundingClientRect();
            if (r.width === 0) return;
            found.push({
                via: sel, tag: el.tagName,
                text: (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 150),
                ariaLabel: el.getAttribute('aria-label') || '',
                classes: (el.className || '').toString().slice(0, 150),
                dataAttrs: Object.keys(el.dataset || {}).slice(0, 10)
            });
        });
    });
    return found.slice(0, 25);
}"""


async def main() -> int:
    """运行三轮探测，报告落盘 report3.json。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    chrome = _default_chrome_path()
    print("== 豆包 DOM 探测（三轮） ==")
    report: dict[str, Any] = {"sections": {}}

    async with pw.async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(PROFILE_DIR),
            "headless": False,
            "viewport": {"width": 1440, "height": 900},
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
            if not await _is_doubao_ready(page):
                print("  ✗ 登录态失效")
                return 1
            print("  ✓ 登录态有效")

            # 进入最近一次有回复的对话（列表第 2 个会话项，避开"主对话"）
            try:
                items = page.locator("a.group\\/conversation-item")
                if await items.count() >= 2:
                    await items.nth(1).click()
                    await page.wait_for_timeout(2500)
            except Exception:  # noqa: BLE001
                pass
            report["sections"]["url"] = page.url

            # 1. 消息树 + md 组件 + 完成标记
            report["sections"]["message_tree"] = await page.evaluate(PROBE_MESSAGE_TREE_SCRIPT)
            report["sections"]["markers"] = await page.evaluate(PROBE_MARKER_SCRIPT)
            await page.screenshot(path=str(REPORT_DIR / "p3_01_messages.png"))
            print("  ✓ 消息树/完成标记采集完成")

            # 2. 模型菜单：点"豆包 快速"
            try:
                await page.get_by_text("豆包", exact=True).first.click(timeout=3000)
                await page.wait_for_timeout(1200)
                report["sections"]["model_menu"] = await page.evaluate(PROBE_MENU_SCRIPT)
                await page.screenshot(path=str(REPORT_DIR / "p3_02_model_menu.png"))
                print("  ✓ 模型菜单采集完成")
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(500)
            except Exception as exc:  # noqa: BLE001
                report["sections"]["model_menu"] = {"error": str(exc)}
            # 3. 加号菜单：工具栏空文本按钮（+）
            try:
                plus = page.locator("button[data-dbx-name]").filter(has_text="")
                # 加号在最左侧（"对话"按钮之前），尝试逐个点击空文本按钮
                for i in range(await plus.count()):
                    txt = (await plus.nth(i).inner_text()).strip()
                    if txt == "":
                        await plus.nth(i).click(timeout=2000)
                        break
                await page.wait_for_timeout(1200)
                report["sections"]["plus_menu"] = await page.evaluate(PROBE_MENU_SCRIPT)
                await page.screenshot(path=str(REPORT_DIR / "p3_03_plus_menu.png"))
                print("  ✓ 加号菜单采集完成")
                await page.keyboard.press("Escape")
            except Exception as exc:  # noqa: BLE001
                report["sections"]["plus_menu"] = {"error": str(exc)}

        finally:
            await context.close()

    out = REPORT_DIR / "report3.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== 三轮探测完成 ==\n  报告: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
