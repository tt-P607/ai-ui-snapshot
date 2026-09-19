"""豆包图像/视频生成技能探测（probe5：技能交互流与产物信号）。

目标：摸清"图像生成 / 视频生成"两个技能按钮的完整交互链，为
DoubaoActions.generate_image / generate_video 提供依据：
1. 点击技能按钮后输入区/URL 变化（是否进入专属模式）
2. 提交描述后生成完成的 DOM 信号（图片 img / 视频 video）
3. 生成产物的下载方式（下载按钮/右键菜单/expect_download）
4. 技能面板真实结构（按钮 class/data 属性，用于稳定定位）

用法（项目根目录，需已登录）：
    uv run python plugins/ai_ui_snapshot/scripts/probe_doubao_skill.py
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
from plugins.ai_ui_snapshot.services.doubao.constants import INPUT_SELECTOR, SITE_URL  # noqa: E402

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


# 技能按钮全量 dump（含"图像生成/视频生成"的按钮结构）
PROBE_SKILL_BUTTONS_SCRIPT = """() => {
    const out = [];
    document.querySelectorAll('button, [role="button"]').forEach(el => {
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (/^(图像生成|视频生成|帮我写作|PPT 生成|深入研究)$/.test(t)) {
            out.push({
                tag: el.tagName, text: t,
                classes: (el.className || '').toString().slice(0, 150),
                ariaLabel: el.getAttribute('aria-label') || '',
                dataAttrs: Object.keys(el.dataset || {}).slice(0, 10),
                dataAttrValues: {
                    skillId: el.getAttribute('data-skill-id') || el.getAttribute('data-skillId') || '',
                    componentType: el.getAttribute('data-component-type') || ''
                },
                visible: el.getBoundingClientRect().width > 0
            });
        }
    });
    return out;
}"""

# 点击技能按钮后的输入区变化（占位符/新增元素/发送按钮状态）
PROBE_AFTER_SKILL_SCRIPT = """() => {
    const ed = document.querySelector('div.tiptap.ProseMirror');
    const holder = ed ? ed.closest('[class*="min-h"]') || ed.parentElement : null;
    const placeHolder = document.querySelector('[data-placeholder], [class*="placeholder"]');
    return {
        editorExists: Boolean(ed),
        holderText: holder ? (holder.innerText || '').slice(0, 120) : '',
        placeholderText: placeHolder ? (placeHolder.getAttribute('data-placeholder')
            || (placeHolder.innerText || '').slice(0, 60)) : '',
        url: location.href,
        // 输入区新增的技能指示标签（chip/badge/tag）
        chips: Array.from(document.querySelectorAll('[class*="chip"], [class*="badge"], [class*="tag"]'))
            .filter(el => el.getBoundingClientRect().width > 0)
            .slice(0, 5)
            .map(el => ({text: (el.innerText || '').slice(0, 40), classes: (el.className || '').toString().slice(0, 100)}))
    };
}"""

# 生成产物检测（对话区内 img[非头像/表情] 与 video 元素）
PROBE_MEDIA_SCRIPT = """() => {
    const main = document.querySelector('[class*="v_list"]') || document.body;
    const imgs = Array.from(main.querySelectorAll('img'))
        .filter(im => im.complete && im.naturalWidth > 200)
        .slice(0, 6)
        .map(im => ({
            src: (im.src || '').slice(0, 120),
            isBlob: im.src.startsWith('blob:'),
            w: im.naturalWidth, h: im.naturalHeight,
            classes: (im.className || '').toString().slice(0, 100),
            alt: (im.getAttribute('alt') || '').slice(0, 60)
        }));
    const videos = Array.from(main.querySelectorAll('video'))
        .slice(0, 4)
        .map(v => ({
            src: ((v.currentSrc || v.src) || '').slice(0, 120),
            isBlob: (v.currentSrc || v.src || '').startsWith('blob:'),
            duration: Math.round(v.duration || 0),
            classes: (v.className || '').toString().slice(0, 100)
        }));
    return {imgs, videos};
}"""

# 生成产物周边操作元素（下载/保存/预览大图按钮）
PROBE_MEDIA_ACTIONS_SCRIPT = """() => {
    const hits = [];
    document.querySelectorAll('button, [role="button"], a[download]').forEach(el => {
        const aria = el.getAttribute('aria-label') || '';
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (/下载|保存|download/i.test(aria) || (/^(下载|保存)$/.test(t) && t)) {
            const r = el.getBoundingClientRect();
            hits.push({
                tag: el.tagName, text: t.slice(0, 20), ariaLabel: aria,
                href: el.getAttribute('href') ? (el.getAttribute('href') || '').slice(0, 100) : '',
                classes: (el.className || '').toString().slice(0, 100),
                visible: r.width > 0
            });
        }
    });
    return hits.slice(0, 12);
}"""


async def _skill_flow(page: pw.Page, skill_text: str, prompt: str, media_wait_s: int) -> dict[str, Any]:
    """执行一次技能流：点技能按钮 → 采变化 → 发描述 → 等产物 → 采产物。

    Args:
        page: 已登录页面。
        skill_text: 技能按钮文本（图像生成 / 视频生成）。
        prompt: 提交的描述文本。
        media_wait_s: 等待产物出现的最大秒数。

    Returns:
        dict[str, Any]: 各阶段采集结果。
    """
    out: dict[str, Any] = {"skill": skill_text}
    # 1. 新对话
    try:
        await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
    except Exception:  # noqa: BLE001
        pass
    # 2. 点击技能按钮（用 button 定位，避免命中侧栏 span）
    skill_btn = page.locator("button").filter(has_text=skill_text).last
    if await skill_btn.count() == 0:
        out["error"] = f"未找到技能按钮 {skill_text}"
        return out
    await skill_btn.click(timeout=5000)
    await page.wait_for_timeout(1500)
    out["after_skill"] = await page.evaluate(PROBE_AFTER_SKILL_SCRIPT)
    await page.screenshot(path=str(REPORT_DIR / f"p5_skill_{skill_text[:2]}.png"))
    # 3. 输入描述发送
    editor = page.locator(INPUT_SELECTOR).first
    await editor.click()
    await page.fill(INPUT_SELECTOR, prompt)
    await page.keyboard.press("Enter")
    # 4. 轮询等产物（img 自然宽 > 200 或 video 出现）
    deadline = time.monotonic() + media_wait_s
    media: dict[str, Any] = {}
    while time.monotonic() < deadline:
        media = await page.evaluate(PROBE_MEDIA_SCRIPT)
        # 视频技能等 video；图像技能等大图（视频卡片也有 poster img，宽>200 误判，图像技能只认 img 且无 video）
        if skill_text == "视频生成":
            if media.get("videos"):
                break
        elif media.get("imgs"):
            break
        await asyncio.sleep(2.5)
    out["media_final"] = media
    out["media_timeout"] = not (media.get("imgs") or media.get("videos"))
    # 5. 稳定后采操作按钮
    await page.wait_for_timeout(2000)
    out["media_actions"] = await page.evaluate(PROBE_MEDIA_ACTIONS_SCRIPT)
    await page.screenshot(path=str(REPORT_DIR / f"p5_media_{skill_text[:2]}.png"))
    return out


async def main() -> int:
    """运行技能探测，报告落盘 report5.json。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    chrome = _default_chrome_path()
    print("== 豆包技能探测（图像/视频生成） ==")
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

            # 0. 静态：技能按钮结构
            report["sections"]["skill_buttons"] = await page.evaluate(PROBE_SKILL_BUTTONS_SCRIPT)
            print(f"  ✓ 技能按钮结构（{len(report['sections']['skill_buttons'])} 个）")

            # 1. 图像生成流
            print("  [1/2] 图像生成技能流...")
            report["sections"]["image_flow"] = await _skill_flow(
                page, "图像生成", "一只戴墨镜的橘猫在沙滩上喝椰子", 150
            )
            print(f"  ✓ 图像流完成（timeout={report['sections']['image_flow'].get('media_timeout')}）")

            # 2. 视频生成流
            print("  [2/2] 视频生成技能流...")
            report["sections"]["video_flow"] = await _skill_flow(
                page, "视频生成", "一只橘猫在海边奔跑，镜头跟随", 240
            )
            print(f"  ✓ 视频流完成（timeout={report['sections']['video_flow'].get('media_timeout')}）")

        finally:
            await context.close()

    out = REPORT_DIR / "report5.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== 技能探测完成 ==\n  报告: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
