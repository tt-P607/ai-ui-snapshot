"""豆包 DOM 二轮探测（针对性补采一轮探测的漏项）。

一轮探测确认：tiptap 编辑器、file input accept、dbx-web-theme 主题键、
loading-container 生成信号、URL 数字会话 ID。本轮补采：
1. 消息容器（文档级滚动假设 + 按"用户问题文本"反查消息块 class 链）
2. 回复完成标记（正则模糊匹配 + CSS 伪元素 content 检查）
3. 新对话按钮（放宽文本匹配）
4. 历史会话面板真实结构（a[href*="/chat/"] 全量 dump）
5. 输入区开关（视口底部区域所有按钮，含"深度思考"等）
6. 消息 hover 操作行（复制/分享按钮）

用法（项目根目录，需已登录）：
    uv run python plugins/ai_ui_snapshot/scripts/probe_doubao_dom2.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import time
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
QUESTION = "请只回复两个字：收到"


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


# ---------------------------------------------------------------------------
# 二轮探测脚本集
# ---------------------------------------------------------------------------

# 1. 滚动结构：文档级滚动 + 全部可滚动候选（放宽阈值）
PROBE_SCROLL_SCRIPT = """() => {
    const doc = document.scrollingElement || document.documentElement;
    const docScroll = {
        tag: doc.tagName,
        scrollHeight: doc.scrollHeight, clientHeight: doc.clientHeight,
        bodyScrollHeight: document.body.scrollHeight,
        bodyClientHeight: document.body.clientHeight,
        bodyOverflow: getComputedStyle(document.body).overflowY
    };
    // 放宽：clientHeight > 100 的可滚动 div（前 5 个，按 scrollHeight 降序）
    const scrollers = Array.from(document.querySelectorAll('div, main, section'))
        .filter(el => {
            const st = getComputedStyle(el);
            return (st.overflowY === 'auto' || st.overflowY === 'scroll')
                && el.scrollHeight > el.clientHeight && el.clientHeight > 100;
        })
        .sort((a, b) => b.scrollHeight - a.scrollHeight)
        .slice(0, 5)
        .map(el => ({
            tag: el.tagName,
            classes: (el.className || '').toString().slice(0, 200),
            scrollHeight: el.scrollHeight, clientHeight: el.clientHeight
        }));
    return {docScroll, scrollers};
}"""

# 2. 消息块反查：以用户问题文本与 AI 回复特征定位消息容器 class 链
PROBE_MESSAGE_BLOCKS_SCRIPT = """(question) => {
    const chain = (el, n) => {
        const out = [];
        let cur = el;
        for (let i = 0; i < n && cur; i++) {
            out.push(cur.tagName + '.' + (cur.className || '').toString().slice(0, 100));
            cur = cur.parentElement;
        }
        return out;
    };
    // 含问题文本的最深元素（用户消息块）
    let userMsg = null;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
        const t = (node.textContent || '').trim();
        if (t === question) {
            userMsg = {textHit: true, chain: chain(node.parentElement, 8)};
            break;
        }
    }
    // "收到"/AI 生成特征文本所在块（AI 消息块）；同时兼容完成标记伪元素 Possibility
    let aiMarker = null;
    const walker2 = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while ((node = walker2.nextNode())) {
        const t = (node.textContent || '').trim();
        if (t === '收到' || /内容由?.{0,3}AI.{0,3}生成|AI 生成|AI生成/.test(t)) {
            aiMarker = {text: t.slice(0, 40), chain: chain(node.parentElement, 8)};
            break;
        }
    }
    // 用户与 AI 消息块的公共祖先（消息列表容器）
    let listContainer = null;
    if (userMsg && aiMarker) {
        const uEl = document.evaluate(`//text()[normalize-space()='${question}']`, document, null,
            XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
        const aTexts = Array.from(document.querySelectorAll('*')).filter(el =>
            el.children.length === 0 && /AI\\s*生成/.test(el.textContent || ''));
        if (uEl && aTexts.length) {
            let uAnc = uEl.parentElement, aAnc = aTexts[aTexts.length - 1];
            const uSet = new Set();
            while (uAnc) { uSet.add(uAnc); uAnc = uAnc.parentElement; }
            while (aAnc && !uSet.has(aAnc)) aAnc = aAnc.parentElement;
            if (aAnc) listContainer = chain(aAnc, 1);
        }
    }
    // 完成标记伪元素检查：class 含 copyright/footer/disclaimer 的 ::after content
    const pseudoMarkers = [];
    document.querySelectorAll('[class*="copyright"], [class*="footer"], [class*="disclaimer"], [class*="ai-generate"], [class*="benchmark"]').forEach(el => {
        for (const pseudo of ['::after', '::before']) {
            const c = getComputedStyle(el, pseudo).content;
            if (c && c !== 'none' && c !== 'normal' && c.length > 2) {
                pseudoMarkers.push({classes: (el.className || '').toString().slice(0, 150), pseudo, content: c.slice(0, 60)});
            }
        }
    });
    return {userMsg, aiMarker, listContainer, pseudoMarkers: pseudoMarkers.slice(0, 10)};
}"""

# 3. 新对话按钮（放宽：文本以"新对话"开头即可）
PROBE_NEW_CHAT_SCRIPT = """() => {
    const btns = [];
    document.querySelectorAll('button, [role="button"], a, div[tabindex]').forEach(el => {
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (/^新对话|^开启新对话/.test(t)) {
            btns.push({
                tag: el.tagName, text: t.slice(0, 40),
                classes: (el.className || '').toString().slice(0, 200),
                ariaLabel: el.getAttribute('aria-label') || '',
                dataAttrs: Object.keys(el.dataset || {}).slice(0, 8),
                visible: el.getBoundingClientRect().width > 0
            });
        }
    });
    return btns;
}"""

# 4. 历史会话（a[href*="/chat/"] 全量 + 侧栏容器探测）
PROBE_HISTORY_SCRIPT = """() => {
    const links = Array.from(document.querySelectorAll('a[href*="/chat/"]')).slice(0, 12).map(a => {
        return {
            href: a.getAttribute('href'),
            text: (a.innerText || '').replace(/\\s+/g, ' ').slice(0, 60),
            classes: (a.className || '').toString().slice(0, 150),
            dataAttrs: Object.keys(a.dataset || {}).slice(0, 8),
            visible: a.getBoundingClientRect().width > 0
        };
    });
    // 包裹这些链接的列表容器（第一个链接向上 5 层）
    let listWrap = null;
    const first = document.querySelector('a[href*="/chat/"]');
    if (first) {
        const chain = [];
        let cur = first;
        for (let i = 0; i < 5 && cur; i++) {
            chain.push(cur.tagName + '.' + (cur.className || '').toString().slice(0, 100));
            cur = cur.parentElement;
        }
        listWrap = chain;
    }
    // 侧栏内含"历史对话/对话历史"文本的入口按钮
    const historyBtns = [];
    document.querySelectorAll('button, [role="button"], a, span, div').forEach(el => {
        if (el.children.length > 2) return;
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (/历史对话|对话历史/.test(t) && t.length <= 12) {
            historyBtns.push({tag: el.tagName, text: t, classes: (el.className || '').toString().slice(0, 150),
                              ariaLabel: el.getAttribute('aria-label') || '', visible: el.getBoundingClientRect().width > 0});
        }
    });
    return {links, listWrap, historyBtns: historyBtns.slice(0, 8)};
}"""

# 5. 输入区开关（编辑器容器向下找工具栏：视口底部 240px 内全部按钮/开关）
PROBE_TOOLBAR_SCRIPT = """() => {
    const vh = window.innerHeight;
    const out = [];
    document.querySelectorAll('button, [role="button"], [role="switch"], [class*="switch"]').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.top > vh - 240 && r.width > 0) {
            const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
            out.push({
                tag: el.tagName, text: t.slice(0, 40),
                ariaLabel: el.getAttribute('aria-label') || '',
                ariaChecked: el.getAttribute('aria-checked') || '',
                ariaExpanded: el.getAttribute('aria-expanded') || '',
                classes: (el.className || '').toString().slice(0, 150),
                dataAttrs: Object.keys(el.dataset || {}).slice(0, 8)
            });
        }
    });
    return out.slice(0, 30);
}"""

# 6. 消息操作行（hover 出现的复制/分享等：全页含这些文案的小元素）
PROBE_ACTIONS_SCRIPT = """() => {
    const out = [];
    document.querySelectorAll('button, [role="button"], [role="menuitem"], span, div').forEach(el => {
        if (el.children.length > 2) return;
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (t && t.length <= 6 && /^(复制|分享|重新生成|赞|踩|继续|更多|朗读)$/.test(t)) {
            out.push({
                tag: el.tagName, text: t,
                classes: (el.className || '').toString().slice(0, 150),
                ariaLabel: el.getAttribute('aria-label') || '',
                visible: el.getBoundingClientRect().width > 0
            });
        }
    });
    return out.slice(0, 20);
}"""


async def main() -> int:
    """运行二轮探测，报告落盘 report2.json。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    chrome = _default_chrome_path()
    print("== 豆包 DOM 探测（二轮） ==")
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

            # 进入新对话
            try:
                await page.get_by_text("新对话", exact=False).first.click(timeout=3000)
                await page.wait_for_timeout(2000)
            except Exception:  # noqa: BLE001
                pass

            # 1. 滚动结构 + 新对话按钮 + 输入区工具栏（静态）
            report["sections"]["scroll"] = await page.evaluate(PROBE_SCROLL_SCRIPT)
            report["sections"]["new_chat"] = await page.evaluate(PROBE_NEW_CHAT_SCRIPT)
            report["sections"]["toolbar_static"] = await page.evaluate(PROBE_TOOLBAR_SCRIPT)
            await page.screenshot(path=str(REPORT_DIR / "p2_01_static.png"))

            # 2. 发送消息等回复（等 loading 消失 + 8s 文本稳定，不看完成标记）
            print("  发送测试消息...")
            editor = page.locator("div.tiptap.ProseMirror[contenteditable='true']").first
            await editor.click()
            await page.fill(
                "div.tiptap.ProseMirror[contenteditable='true']", QUESTION
            )
            await page.keyboard.press("Enter")
            # 等生成开始（loading-container 出现）再等消失
            await page.wait_for_timeout(2500)
            deadline = time.monotonic() + 150
            last_len, stable = -1, 0
            while time.monotonic() < deadline:
                len_now = await page.evaluate("() => document.body.innerText.length")
                loading = await page.evaluate(
                    "() => { const els = document.querySelectorAll('[class*=loading-container]');"
                    "return Array.from(els).some(e => e.getBoundingClientRect().width > 0); }"
                )
                stable = stable + 1 if (len_now == last_len and not loading) else 0
                if stable >= 4:
                    break
                last_len = len_now
                await asyncio.sleep(2.0)
            print(f"  回复稳定（body 长度 {last_len}）")

            # 3. 消息块反查 + 操作行 + 完成标记伪元素
            report["sections"]["message_blocks"] = await page.evaluate(PROBE_MESSAGE_BLOCKS_SCRIPT, QUESTION)
            report["sections"]["actions"] = await page.evaluate(PROBE_ACTIONS_SCRIPT)
            report["sections"]["toolbar_after"] = await page.evaluate(PROBE_TOOLBAR_SCRIPT)
            report["sections"]["url_after_reply"] = page.url
            await page.screenshot(path=str(REPORT_DIR / "p2_02_after_reply.png"))
            print("  ✓ 消息块/操作行采集完成")

            # 4. 历史会话（自动点击"历史对话"入口后采集链接）
            try:
                await page.get_by_text("历史对话", exact=False).first.click(timeout=3000)
                await page.wait_for_timeout(2000)
            except Exception:  # noqa: BLE001
                pass
            report["sections"]["history"] = await page.evaluate(PROBE_HISTORY_SCRIPT)
            await page.screenshot(path=str(REPORT_DIR / "p2_03_history.png"))
            print("  ✓ 历史会话采集完成")

        finally:
            await context.close()

    out = REPORT_DIR / "report2.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== 二轮探测完成 ==\n  报告: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
