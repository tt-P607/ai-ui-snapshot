"""豆包 DOM 四轮探测（收官：AI 消息块 / 虚拟滚动 / 模型菜单 / 加号菜单）。

三轮确认：v_list_row 虚拟列表行、完成标记"AI 生成可能有误 注意核实"、
md-box-root 为 AI 回复 markdown 根组件。本轮修正确点击目标，补齐：
1. AI 消息块完整结构（md-box-root 向上链 + 行容器）
2. 虚拟滚动容器（v_list 相关全量 class）
3. 模型菜单（正确点击"快速"模型按钮）
4. 加号菜单（编辑器容器左侧第一个圆形按钮）
5. AI 消息 hover 操作行（复制/分享按钮）

用法（项目根目录，需已登录）：
    uv run python plugins/ai_ui_snapshot/scripts/probe_doubao_dom4.py
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


# 1. AI 消息块结构：md-box-root 向上 12 层 + 虚拟列表容器探测
PROBE_AI_BLOCK_SCRIPT = """() => {
    const md = document.querySelector('[class*="md-box-root"]');
    if (!md) return {error: 'no md-box-root'};
    const chain = [];
    let cur = md;
    let scroller = null;
    for (let i = 0; i < 12 && cur; i++) {
        const st = cur.tagName === 'DIV' || cur.tagName === 'MAIN' ? getComputedStyle(cur) : null;
        const isScroll = st && (st.overflowY === 'auto' || st.overflowY === 'scroll')
            && cur.scrollHeight > cur.clientHeight + 20;
        chain.push({
            tag: cur.tagName,
            classes: (cur.className || '').toString().slice(0, 140),
            isScroll: Boolean(isScroll)
        });
        if (isScroll && !scroller) scroller = cur;
        cur = cur.parentElement;
    }
    // AI 消息行：md-box-root 最高层祖先中含 data/message 特征或 v_list_row 的直接子层
    const vlist = document.querySelector('[class*="v_list"]');
    const vlistInfo = vlist ? {
        classes: (vlist.className || '').toString().slice(0, 200),
        childCount: vlist.children.length,
        childClasses: Array.from(vlist.children).slice(0, 6).map(c =>
            c.tagName + '.' + (c.className || '').toString().slice(0, 120))
    } : null;
    // 全页 v_list 系 class 统计
    const vClasses = {};
    document.querySelectorAll('[class*="v_list"]').forEach(el => {
        Array.from((el.className || '').toString().split(/\\s+/)).filter(c => c.includes('v_list')).forEach(c => {
            vClasses[c] = (vClasses[c] || 0) + 1;
        });
    });
    return {chain, scrollerFound: Boolean(scroller), vlistInfo, vClasses};
}"""

# 2. 弹出菜单 dump（三轮同款）
PROBE_MENU_SCRIPT = """() => {
    const sels = ['[role="menu"]', '[role="menuitem"]', '[role="dialog"]', '[role="listbox"]',
                  '[role="option"]', '[class*="popover"]', '[class*="dropdown"]'];
    const found = [];
    const seen = new Set();
    sels.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => {
            if (seen.has(el)) return;
            seen.add(el);
            if (el.getBoundingClientRect().width === 0) return;
            found.push({
                via: sel, tag: el.tagName,
                text: (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 200),
                ariaLabel: el.getAttribute('aria-label') || '',
                classes: (el.className || '').toString().slice(0, 120),
                dataAttrs: Object.keys(el.dataset || {}).slice(0, 10)
            });
        });
    });
    return found.slice(0, 30);
}"""

# 3. AI 消息 hover 后的操作行（复制/分享）：md 祖先行附近的小按钮
PROBE_HOVER_ACTIONS_SCRIPT = """() => {
    const hits = [];
    document.querySelectorAll('button, [role="button"], [role="menuitem"]').forEach(el => {
        const aria = el.getAttribute('aria-label') || '';
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (/复制|分享|重新生成|收藏|赞|踩|朗读|编辑/.test(aria) || (/^(复制|分享|重新生成)$/.test(t) && t)) {
            hits.push({
                tag: el.tagName, text: t.slice(0, 20), ariaLabel: aria,
                classes: (el.className || '').toString().slice(0, 120),
                visible: el.getBoundingClientRect().width > 0
            });
        }
    });
    return hits.slice(0, 20);
}"""


async def main() -> int:
    """运行四轮探测，报告落盘 report4.json。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    chrome = _default_chrome_path()
    print("== 豆包 DOM 探测（四轮·收官） ==")
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

            # 进入"收到"对话（有完整问答）
            try:
                items = page.locator("a.group\\/conversation-item")
                count = await items.count()
                for i in range(min(count, 4)):
                    if "收到" in (await items.nth(i).inner_text()):
                        await items.nth(i).click()
                        break
                await page.wait_for_timeout(2500)
            except Exception:  # noqa: BLE001
                pass
            report["sections"]["url"] = page.url

            # 1. AI 消息块 + 虚拟列表
            report["sections"]["ai_block"] = await page.evaluate(PROBE_AI_BLOCK_SCRIPT)
            print("  ✓ AI 消息块/虚拟列表采集完成")

            # 2. 模型菜单：点击含"快速"的模型按钮（非侧栏标题）
            try:
                model_btn = page.locator("button").filter(has_text="快速").last
                await model_btn.click(timeout=4000)
                await page.wait_for_timeout(1200)
                report["sections"]["model_menu"] = await page.evaluate(PROBE_MENU_SCRIPT)
                await page.screenshot(path=str(REPORT_DIR / "p4_01_model_menu.png"))
                print("  ✓ 模型菜单采集完成")
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(600)
            except Exception as exc:  # noqa: BLE001
                report["sections"]["model_menu"] = {"error": str(exc)[:300]}

            # 3. 加号菜单：编辑器左侧第一个按钮（父容器内按钮扫描定位 +）
            try:
                found = False
                btns = page.locator(
                    "div.tiptap.ProseMirror >> xpath=ancestor::div[5] >> button"
                )
                n = await btns.count()
                for i in range(n):
                    b = btns.nth(i)
                    aria = await b.get_attribute("aria-label")
                    txt = (await b.inner_text()).strip()
                    if (aria and ("上传" in aria or "附件" in aria or "+" in aria)) or txt == "+":
                        await b.click(timeout=3000)
                        found = True
                        break
                if not found and n > 0:
                    await btns.first.click(timeout=3000)
                    found = True
                await page.wait_for_timeout(1200)
                report["sections"]["plus_menu"] = await page.evaluate(PROBE_MENU_SCRIPT)
                report["sections"]["plus_click_target"] = {"found": found}
                await page.screenshot(path=str(REPORT_DIR / "p4_02_plus_menu.png"))
                print("  ✓ 加号菜单采集完成")
                await page.keyboard.press("Escape")
            except Exception as exc:  # noqa: BLE001
                report["sections"]["plus_menu"] = {"error": str(exc)[:300]}

            # 4. hover AI 消息采集操作行
            try:
                md = page.locator('[class*="md-box-root"]').last
                await md.hover(timeout=3000)
                await page.wait_for_timeout(800)
                report["sections"]["hover_actions"] = await page.evaluate(PROBE_HOVER_ACTIONS_SCRIPT)
                await page.screenshot(path=str(REPORT_DIR / "p4_03_hover.png"))
                print("  ✓ hover 操作行采集完成")
            except Exception as exc:  # noqa: BLE001
                report["sections"]["hover_actions"] = {"error": str(exc)[:300]}

        finally:
            await context.close()

    out = REPORT_DIR / "report4.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== 四轮探测完成 ==\n  报告: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
