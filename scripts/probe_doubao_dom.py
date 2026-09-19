"""豆包 DOM 探测脚本（Phase 0 核心交付物，结果供 Phase 2 校准选择器）。

复用登录态 profile 打开豆包网页，dump 关键 DOM 结构与文案，输出 JSON 报告
与截图到 ``plugins/ai_ui_snapshot/scripts/doubao_probe_report/``，用于校准
``services/doubao/constants.py`` 中待确认的选择器。

用法（项目根目录，先完成登录）：
    uv run python plugins/ai_ui_snapshot/scripts/login_doubao.py   # 仅首次
    uv run python plugins/ai_ui_snapshot/scripts/probe_doubao_dom.py

探测项（对应 constants 待确认清单）：
1. 输入框周边结构：textarea 定位、发送按钮、附件上传按钮（回形针）与
   input[type=file] 的 accept 白名单
2. 输入区开关：深度思考 / 联网搜索等开关按钮的定位方式与状态读法
3. 消息容器：对话滚动容器、用户消息块、AI 消息块、思考块、代码块的
   class 链与 data-* 属性
4. 历史会话：侧边栏结构、会话项选择器、会话标题读法
5. 分享功能：页面有无分享/公开链接入口
6. URL 结构：会话 ID 在 URL 中的格式
7. 生成中信号：提问后出现的停止按钮 / 加载指示器
8. 主题：明暗主题切换方式与 localStorage 键

脚本为全自动流程：检测登录态 → 尝试新对话 → 静态采集 → 自动发送测试消息
采集生成中信号与回复区 DOM → 自动点开历史对话面板采集 → 汇总落盘。
任何一步失败都会跳过并继续（报告 sections 中留 null），不阻塞整体流程。
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import time
from typing import Any

import playwright.async_api as pw

# 兼容直接运行：把项目根与插件根加入 sys.path（探测脚本独立于插件运行时）
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
POLL_INTERVAL_S = 2.0


def _default_chrome_path() -> str:
    """探测系统中已安装的正式版 Chrome 路径（找不到返回空）。

    Returns:
        str: Chrome 可执行文件路径；未找到时返回空字符串。
    """
    candidates = (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    )
    for cand in candidates:
        if pathlib.Path(cand).is_file():
            return cand
    return ""


# ---------------------------------------------------------------------------
# 探测脚本集：每项返回可直接 JSON 序列化的结果
# ---------------------------------------------------------------------------

# 1. 输入框周边结构（输入区容器内的按钮/开关/file input 及 accept 白名单）。
# 豆包输入框实测（2026-08）为 tiptap ProseMirror contenteditable 富文本，
# 不是公开情报中的 textarea.semi-input-textarea（已过时）。
PROBE_INPUT_AREA_SCRIPT = """() => {
    const wrap = (s) => {
        const el = document.querySelector(s);
        return el ? {
            selector: s, tag: el.tagName, classes: (el.className || '').toString().slice(0, 200),
            ariaLabel: el.getAttribute('aria-label') || '',
            dataAttrs: Object.keys(el.dataset || {}).slice(0, 10),
            placeholder: el.getAttribute('placeholder') || '',
            visible: el.getBoundingClientRect().width > 0
        } : null;
    };
    const fileInput = document.querySelector('input[type="file"]');
    const result = {
        textarea: wrap('textarea.semi-input-textarea') || wrap('textarea'),
        editor: wrap('div.tiptap.ProseMirror[contenteditable="true"]'),
        fileInput: null,
        inputAreaButtons: [],
        inputAreaClasses: []
    };
    if (fileInput) {
        result.fileInput = {
            accept: fileInput.getAttribute('accept') || '',
            classes: (fileInput.className || '').toString().slice(0, 200),
            parentClasses: (fileInput.parentElement && fileInput.parentElement.className || '').toString().slice(0, 200)
        };
    }
    // 输入区容器：编辑器向上找 5 层，收集其中所有可交互元素
    const ed = document.querySelector('div.tiptap.ProseMirror[contenteditable="true"]')
        || document.querySelector('textarea');
    if (ed) {
        let container = ed;
        for (let i = 0; i < 5; i++) container = container.parentElement || container;
        result.inputAreaClasses.push((container.className || '').toString().slice(0, 300));
        container.querySelectorAll('button, [role="button"], [role="switch"]').forEach(el => {
            const t = (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 50);
            if (!t && !el.getAttribute('aria-label') && !el.querySelector('svg')) return;
            result.inputAreaButtons.push({
                tag: el.tagName,
                text: t,
                ariaLabel: el.getAttribute('aria-label') || '',
                ariaChecked: el.getAttribute('aria-checked') || '',
                classes: (el.className || '').toString().slice(0, 150),
                visible: el.getBoundingClientRect().width > 0
            });
        });
    }
    return result;
}"""

# 2. 消息容器结构（对话区滚动容器、用户/AI 消息块的 class 链与 data 属性）
PROBE_MESSAGES_SCRIPT = """() => {
    // 候选滚动容器：常见 overflow 滚动的长容器
    const scrollers = Array.from(document.querySelectorAll('div, main, section'))
        .filter(el => {
            const st = getComputedStyle(el);
            return (st.overflowY === 'auto' || st.overflowY === 'scroll')
                && el.scrollHeight > el.clientHeight && el.clientHeight > 300;
        })
        .sort((a, b) => b.scrollHeight - a.scrollHeight)
        .slice(0, 3)
        .map(el => ({
            tag: el.tagName,
            classes: (el.className || '').toString().slice(0, 250),
            dataAttrs: Object.keys(el.dataset || {}).slice(0, 10),
            scrollHeight: el.scrollHeight, clientHeight: el.clientHeight
        }));
    // data-testid 线索（豆包存在 chat_list_thread_item，消息区可能同风格）
    const testids = Array.from(document.querySelectorAll('[data-testid]'))
        .map(el => el.getAttribute('data-testid'))
        .filter((v, i, a) => a.indexOf(v) === i)
        .slice(0, 40);
    // 候选消息块：按常见关键词 class 过滤
    const msgCandidates = [];
    document.querySelectorAll('div[data-testid], section[data-testid]').forEach(el => {
        const tid = el.getAttribute('data-testid') || '';
        if (/(message|msg|chat|conv|reply|answer|benchmark|receive|send)/i.test(tid)) {
            msgCandidates.push({
                testid: tid,
                tag: el.tagName,
                classes: (el.className || '').toString().slice(0, 200),
                textPreview: (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 80)
            });
        }
    });
    // AI 回复完成标记所在元素（实测 2026-08：标记文案为"内容由 AI 生成"，
    // 公开情报的"内容由豆包 AI 生成"已过时；两者都探测）
    let genMarker = null;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
        const c = node.textContent || '';
        if (c.includes('内容由 AI 生成') || c.includes('内容由豆包 AI 生成')) {
            const el = node.parentElement;
            genMarker = {
                markerText: c.includes('内容由豆包 AI 生成') ? '内容由豆包 AI 生成' : '内容由 AI 生成',
                tag: el ? el.tagName : '',
                classes: el ? (el.className || '').toString().slice(0, 200) : '',
                ancestorChain: []
            };
            let anc = el;
            for (let i = 0; i < 6 && anc; i++) {
                genMarker.ancestorChain.push(anc.tagName + '.' + (anc.className || '').toString().slice(0, 80));
                anc = anc.parentElement;
            }
            break;
        }
    }
    return {scrollers, testids, msgCandidates: msgCandidates.slice(0, 20), genMarker};
}"""

# 3. 新对话按钮探测（class 结构）
PROBE_NEW_CHAT_SCRIPT = """() => {
    const btns = [];
    document.querySelectorAll('button, [role="button"], a, div[tabindex]').forEach(el => {
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (/^(新对话|新的对话|开启新对话)$/.test(t)) {
            btns.push({
                tag: el.tagName, text: t,
                classes: (el.className || '').toString().slice(0, 200),
                ariaLabel: el.getAttribute('aria-label') || '',
                visible: el.getBoundingClientRect().width > 0
            });
        }
    });
    return btns;
}"""

# 4. 历史会话面板（点开前后的结构对比数据：会话项、标题读法）
PROBE_HISTORY_SCRIPT = """() => {
    const items = Array.from(document.querySelectorAll('[data-testid="chat_list_thread_item"]'));
    const historyArea = {
        itemSelectorHit: items.length,
        items: items.slice(0, 5).map(el => {
            const a = el.closest('a');
            return {
                classes: (el.className || '').toString().slice(0, 200),
                href: a ? a.getAttribute('href') : '',
                innerLinks: Array.from(el.querySelectorAll('a')).map(x => x.getAttribute('href')).slice(0, 2),
                titleText: (el.innerText || '').split('\\n')[0].slice(0, 60)
            };
        }),
        // 历史侧栏容器候选（会话项向上 4 层的 class 链）
        itemAncestors: []
    };
    if (items[0]) {
        let anc = items[0];
        for (let i = 0; i < 4 && anc; i++) {
            historyArea.itemAncestors.push(anc.tagName + '.' + (anc.className || '').toString().slice(0, 100));
            anc = anc.parentElement;
        }
    }
    // 侧边栏所有 data-testid 项（泛探测，防 chat_list_thread_item 失效）
    const sidebarTestids = Array.from(document.querySelectorAll('[data-testid]'))
        .map(el => el.getAttribute('data-testid'))
        .filter((v, i, a) => a.indexOf(v) === i && /(chat|list|thread|conv|history|session)/i.test(v));
    historyArea.relatedTestids = sidebarTestids;
    return historyArea;
}"""

# 5. 分享功能探测（页面可见按钮/菜单中文案含"分享"）
PROBE_SHARE_SCRIPT = """() => {
    const hits = [];
    document.querySelectorAll('button, [role="button"], [role="menuitem"], a, span, div').forEach(el => {
        if (el.children.length > 3) return;
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (t && t.length <= 12 && /分享|share/i.test(t)) {
            hits.push({
                tag: el.tagName, text: t,
                classes: (el.className || '').toString().slice(0, 150),
                ariaLabel: el.getAttribute('aria-label') || '',
                visible: el.getBoundingClientRect().width > 0
            });
        }
    });
    return hits.slice(0, 15);
}"""

# 6. 生成中信号探测（发送消息后立即采集：停止按钮/加载指示/disabled 状态）
PROBE_GENERATING_SCRIPT = """() => {
    const results = {stopButtons: [], loadingIndicators: [], sendButtonState: null};
    document.querySelectorAll('button, [role="button"]').forEach(el => {
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        const aria = el.getAttribute('aria-label') || '';
        if (/停止|stop/i.test(t) || /停止/i.test(aria)) {
            results.stopButtons.push({text: t, ariaLabel: aria, classes: (el.className || '').toString().slice(0, 150)});
        }
    });
    document.querySelectorAll('[class*="loading"], [class*="spin"], [class*="generating"], [class*="typing"]').forEach(el => {
        if (el.getBoundingClientRect().width > 0) {
            results.loadingIndicators.push({
                classes: (el.className || '').toString().slice(0, 150),
                visible: true
            });
        }
    });
    return results;
}"""

# 7. 主题与 localStorage 键（明暗主题相关键值对）
PROBE_THEME_SCRIPT = """() => {
    const themeKeys = [];
    for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && /(theme|dark|light|appearance|mode)/i.test(k)) {
            themeKeys.push({key: k, value: (localStorage.getItem(k) || '').slice(0, 200)});
        }
    }
    const darkAttr = document.documentElement.getAttribute('data-theme')
        || document.documentElement.getAttribute('theme') || '';
    const htmlClass = document.documentElement.className || '';
    return {
        themeKeys,
        htmlDataTheme: darkAttr,
        htmlClass: htmlClass.slice(0, 100),
        colorScheme: getComputedStyle(document.documentElement).colorScheme || ''
    };
}"""

# 8. 页面标题与 URL 格式
PROBE_META_SCRIPT = """() => {
    return {
        title: document.title,
        url: location.href,
        lang: document.documentElement.lang || ''
    };
}"""


async def _wait_login(page: pw.Page) -> bool:
    """等待已登录就绪（复用 login_doubao 的三重就绪判定）。"""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if await _is_doubao_ready(page):
            return True
        await asyncio.sleep(POLL_INTERVAL_S)
    return False


async def _shot(page: pw.Page, name: str) -> str:
    """全页截图落盘并返回相对路径。"""
    path = REPORT_DIR / f"{name}.png"
    await page.screenshot(path=str(path), full_page=False)
    return str(path.relative_to(_PROJECT_ROOT))


async def _ask_and_wait(page: pw.Page, prompt: str, report: dict[str, Any]) -> None:
    """向豆包发一条消息并粗等回复完成（探测生成中信号 + 回复区 DOM 用）。

    Args:
        page: 已登录的豆包页面。
        prompt: 测试消息文本。
        report: 探测报告字典（生成中信号写入 report["generating_signals"]）。
    """
    # 输入框为 tiptap ProseMirror 富文本（非 textarea），fill 对
    # contenteditable 生效需配合聚焦；先点击聚焦再 fill
    editor = page.locator("div.tiptap.ProseMirror[contenteditable='true']").first
    await editor.click()
    await editor.fill(prompt)
    await editor.press("Enter")
    # 发送后立刻采样生成中信号（300ms 与 2s 两个时间点）
    await page.wait_for_timeout(300)
    sig_fast = await page.evaluate(PROBE_GENERATING_SCRIPT)
    await page.wait_for_timeout(1700)
    sig_slow = await page.evaluate(PROBE_GENERATING_SCRIPT)
    report["generating_signals"] = {"at_300ms": sig_fast, "at_2s": sig_slow}
    # 粗等回复完成（"内容由 AI 生成"出现 + 文本 8s 稳定）
    last_text, stable = "", 0
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        text = await page.evaluate("() => document.body.innerText.length")
        stable = stable + 1 if text == last_text and "内容由 AI 生成" in await page.evaluate(
            "() => document.body.innerText"
        ) else 0
        if stable >= 4:
            return
        last_text = text
        await asyncio.sleep(2.0)


async def main() -> int:
    """运行豆包 DOM 探测流程，输出报告。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    chrome = _default_chrome_path()
    print("== 豆包 DOM 探测 ==")
    print(f"  报告目录: {REPORT_DIR}")
    if not PROFILE_DIR.exists():
        print("  ✗ 未找到豆包登录态目录，请先运行 scripts/login_doubao.py 登录")
        return 1

    report: dict[str, Any] = {"meta": {}, "sections": {}}

    async with pw.async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(PROFILE_DIR),
            "headless": False,
            "viewport": {"width": 1440, "height": 900},
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
            ],
            "ignore_default_args": ["--enable-automation"],
        }
        if chrome:
            launch_kwargs["executable_path"] = chrome
        context = await p.chromium.launch_persistent_context(**launch_kwargs)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)

            if not await _wait_login(page):
                print("  ✗ 登录态失效，请重新运行 scripts/login_doubao.py")
                return 1
            print("  ✓ 登录态有效")

            # 尝试进入新对话（干净环境）
            try:
                new_btn = page.get_by_text("新对话", exact=True).first
                await new_btn.click(timeout=3000)
                await page.wait_for_timeout(1500)
            except Exception:  # noqa: BLE001 - 新对话按钮定位失败不阻塞
                pass

            # 第 1 步：静态结构采集
            report["meta"] = await page.evaluate(PROBE_META_SCRIPT)
            report["sections"]["input_area"] = await page.evaluate(PROBE_INPUT_AREA_SCRIPT)
            report["sections"]["messages_static"] = await page.evaluate(PROBE_MESSAGES_SCRIPT)
            report["sections"]["new_chat"] = await page.evaluate(PROBE_NEW_CHAT_SCRIPT)
            report["sections"]["share"] = await page.evaluate(PROBE_SHARE_SCRIPT)
            report["sections"]["theme"] = await page.evaluate(PROBE_THEME_SCRIPT)
            report["sections"]["url_after_new_chat"] = page.url
            await _shot(page, "01_initial")
            print("  ✓ 静态结构采集完成")

            # 第 2 步：发送消息采集生成中信号与回复 DOM（全自动，无需人工）
            await _ask_and_wait(page, "你好，请用一句话介绍你自己", report)
            report["sections"]["messages_after_reply"] = await page.evaluate(PROBE_MESSAGES_SCRIPT)
            await _shot(page, "02_after_reply")
            print("  ✓ 回复区 DOM 采集完成")

            # 第 3 步：历史对话面板（自动点击"历史对话"展开后采集）
            try:
                history_btn = page.get_by_text("历史对话", exact=True).first
                await history_btn.click(timeout=3000)
                await page.wait_for_timeout(1500)
            except Exception:  # noqa: BLE001 - 展开失败时仍采样现状
                pass
            report["sections"]["history"] = await page.evaluate(PROBE_HISTORY_SCRIPT)
            report["sections"]["url_after_history"] = page.url
            await _shot(page, "03_history")
            print("  ✓ 历史会话面板采集完成")

            # 第 4 步：开关状态（深度思考等，输入区二次采样补充时序状态）
            report["sections"]["toggles"] = await page.evaluate(PROBE_INPUT_AREA_SCRIPT)
            await _shot(page, "04_toggles")
            print("  ✓ 输入区开关采集完成（含在 input_area 结果中）")

        finally:
            await context.close()

    out = REPORT_DIR / "report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n== 探测完成 ==")
    print(f"  报告: {out}")
    print(f"  截图: {REPORT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
