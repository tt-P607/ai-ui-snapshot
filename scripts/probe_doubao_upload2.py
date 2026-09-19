"""豆包附件上传诊断（probe8：注入前后 DOM diff，判定附件是否真实挂载）。

probe7 中预览探测为 0，用 DOM 快照对比法定位附件卡片真实位置：
- 注入前快照（输入区外壳的 outerHTML 摘要 + 全部元素计数）
- 注入后快照 → diff 出新增节点，dump 其完整路径与结构

用法（项目根目录，需已登录）：
    uv run python plugins/ai_ui_snapshot/scripts/probe_doubao_upload2.py [素材名]
素材名：small_img（默认）/ audio / video / big_img
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
TEST_DIR = _PROJECT_ROOT / "tmp" / "doubao_attach_test"

# 全 DOM 节点签名快照（tag+class 哈希集合，用于 diff 新增节点）
SNAPSHOT_SCRIPT = """() => {
    const m = new Map();
    document.querySelectorAll('*').forEach(el => {
        const key = el.tagName + '|' + (el.className || '').toString().slice(0, 80) + '|' + (el.id || '');
        m.set(key, (m.get(key) || 0) + 1);
    });
    return Array.from(m.entries()).map(([k, v]) => k + '#' + v);
}"""

# 新增节点详情采集：文件名/图片/src 特征元素
NEW_NODES_SCRIPT = """(fname) => {
    const out = {withFileName: [], newImgs: [], newMedia: []};
    document.querySelectorAll('*').forEach(el => {
        if (el.children.length === 0 && (el.innerText || '').trim() && (el.innerText || '').includes(fname)) {
            const chain = [];
            let cur = el;
            for (let i = 0; i < 6 && cur; i++) { chain.push(cur.tagName + '.' + (cur.className || '').toString().slice(0, 60)); cur = cur.parentElement; }
            out.withFileName.push({tag: el.tagName, text: (el.innerText || '').trim().slice(0, 50), chain});
        }
    });
    document.querySelectorAll('img, video, audio').forEach(el => {
        const r = el.getBoundingClientRect();
        const info = {tag: el.tagName, src: (el.src || '').slice(0, 80), visible: r.width > 0,
                      nearInput: Boolean(el.closest('[class*="input"], [class*="editor"], [class*="send"]'))};
        if (el.tagName === 'IMG' && el.naturalWidth > 0 && el.naturalWidth < 500 && r.width > 0 && r.width < 300) out.newImgs.push(info);
        if (el.tagName !== 'IMG') out.newMedia.push(info);
    });
    return out;
}"""


def _default_chrome_path() -> str:
    """探测正式版 Chrome 路径（找不到返回空）。"""
    for cand in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if pathlib.Path(cand).is_file():
            return cand
    return ""


async def main() -> int:
    """运行注入前后 DOM diff 诊断。"""
    material = sys.argv[1] if len(sys.argv) > 1 else "small_img"
    files = {
        "small_img": TEST_DIR / "test_small.jpg",
        "audio": TEST_DIR / "test_audio.mp3",
        "video": TEST_DIR / "test_video.mp4",
        "big_img": TEST_DIR / "test_photo.jpg",
    }
    path = files[material]
    chrome = _default_chrome_path()
    report: dict[str, Any] = {"material": material, "file": path.name}

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
                print("✗ 登录态失效")
                return 1
            print(f"✓ 登录态有效，注入 {path.name}")

            before = set(await page.evaluate(SNAPSHOT_SCRIPT))
            await page.set_input_files('input[type="file"]', str(path))
            # 等待 DOM 变化
            for _ in range(10):
                await page.wait_for_timeout(1000)
                after = set(await page.evaluate(SNAPSHOT_SCRIPT))
                added = after - before
                if added:
                    break
            report["added_nodes"] = sorted(added)[:30]
            report["removed_nodes"] = sorted(before - after)[:10]
            print(f"  DOM diff: +{len(added)} 新增 / -{len(before - after)} 移除")
            for n in list(added)[:10]:
                print(f"    + {n[:100]}")
            # 文件名/媒体元素探测
            report["new_elements"] = await page.evaluate(NEW_NODES_SCRIPT, path.stem[:20])
            ne = report["new_elements"]
            print(f"  含文件名元素: {len(ne.get('withFileName', []))}，小图: {len(ne.get('newImgs', []))}，音/视频: {len(ne.get('newMedia', []))}")
            await page.screenshot(path=str(REPORT_DIR / f"p8_{material}.png"))
        finally:
            await context.close()

    out = REPORT_DIR / "report8.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
