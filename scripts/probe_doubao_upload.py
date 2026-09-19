"""豆包附件上传探测（probe7：全类型素材上传链路）。

目标：用 4 类真实素材摸清上传交互与预览 DOM，校准 upload_file：
1. 页面上 input[type=file] 的数量与各自 accept（图片输入 vs 文件输入）
2. 注入后输入区的附件预览卡片结构（图片/音频/视频各自形态）
3. 超大文件（26MB 大图）的行为（接受/拒绝/toast）
4. 视频是否真的不行（accept 声称无视频，但主输入可能接受）

用法（项目根目录，需已登录）：
    uv run python plugins/ai_ui_snapshot/scripts/probe_doubao_upload.py
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
TEST_DIR = _PROJECT_ROOT / "tmp" / "doubao_attach_test"

# input[type=file] 全量清单（数量/accept/可见性/父容器）
PROBE_FILE_INPUTS = """() => {
    return Array.from(document.querySelectorAll('input[type="file"]')).map((inp, i) => ({
        idx: i,
        accept: inp.getAttribute('accept') || '',
        multiple: inp.hasAttribute('multiple'),
        visible: inp.getBoundingClientRect().width > 0,
        parentClasses: (inp.parentElement && inp.parentElement.className || '').toString().slice(0, 150),
        // 向上找带 data-testid 或 data-dbx-name 的可定位祖先
        locatorHint: (() => {
            let cur = inp;
            for (let k = 0; k < 6 && cur; k++) {
                const tid = cur.getAttribute && (cur.getAttribute('data-testid') || cur.getAttribute('data-dbx-name'));
                if (tid) return cur.tagName + '[' + (cur.getAttribute('data-testid') ? 'data-testid=' + tid : 'data-dbx-name=' + tid) + ']';
                cur = cur.parentElement;
            }
            return '';
        })()
    }));
}"""

# 输入区附件预览探测：编辑器容器兄弟区域内的预览卡片（img/audio/video/文件名）
PROBE_PREVIEW = """() => {
    const ed = document.querySelector('div.tiptap.ProseMirror[contenteditable="true"]');
    if (!ed) return {error: 'no editor'};
    // 输入区外壳：编辑器向上 6 层的大容器（附件预览挂这层底部）
    let shell = ed;
    for (let i = 0; i < 6; i++) shell = shell.parentElement || shell;
    const children = [];
    Array.from(shell.querySelectorAll('img, video, audio, [class*="file"], [class*="attachment"], [class*="upload"]')).forEach(el => {
        if (el.closest('.tiptap')) return;  // 跳过编辑器内部
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return;
        children.push({
            tag: el.tagName,
            src: ((el.src || el.getAttribute('href') || '') + '').slice(0, 100),
            text: (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 60),
            classes: (el.className || '').toString().slice(0, 120)
        });
    });
    return {shellClasses: (shell.className || '').toString().slice(0, 200), previews: children.slice(0, 10)};
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
    """运行附件上传探测，报告落盘 report7.json。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    chrome = _default_chrome_path()
    report: dict[str, Any] = {"sections": {}}
    print("== 豆包附件上传探测 ==")

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

            # 0. input[type=file] 全量清单
            inputs = await page.evaluate(PROBE_FILE_INPUTS)
            report["sections"]["file_inputs"] = inputs
            print(f"  ✓ file input 共 {len(inputs)} 个:")
            for it in inputs:
                print(f"    [{it['idx']}] accept={it['accept'][:60]} multiple={it['multiple']} hint={it['locatorHint'][:50]}")

            # 逐素材上传（每个都开新对话，互不污染）
            cases = [
                ("small_img", TEST_DIR / "test_small.jpg"),
                ("audio", TEST_DIR / "test_audio.mp3"),
                ("video", TEST_DIR / "test_video.mp4"),
                ("big_img", TEST_DIR / "test_photo.jpg"),
            ]
            for name, path in cases:
                print(f"\n  --[{name}] {path.name} ({path.stat().st_size // 1024}KB)")
                # 新对话
                await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2500)
                case_out: dict[str, Any] = {"file": path.name}
                # 注入到第一个 input（探测：豆包可能多 input 分流）
                target_idx = 0 if name != "audio" else 0
                try:
                    await page.evaluate(
                        """(idx) => {
                            const inputs = document.querySelectorAll('input[type="file"]');
                            if (idx < inputs.length) inputs[idx].classList.add('mofox-target');
                        }""",
                        target_idx,
                    )
                    await page.set_input_files("input.mofox-target", str(path))
                    case_out["injected"] = True
                except Exception as exc:  # noqa: BLE001
                    case_out["injected"] = False
                    case_out["inject_error"] = str(exc)[:200]
                # 等预览出现（最长 12s）
                deadline = time.monotonic() + 12
                preview: dict[str, Any] = {}
                while time.monotonic() < deadline:
                    preview = await page.evaluate(PROBE_PREVIEW)
                    if preview.get("previews"):
                        break
                    await page.wait_for_timeout(1000)
                case_out["preview"] = preview
                # toast/报错检测
                case_out["toast"] = await page.evaluate(
                    """() => {
                        const t = Array.from(document.querySelectorAll('[class*="toast"], [class*="message"], [role="alert"]'))
                            .filter(el => el.getBoundingClientRect().width > 0)
                            .map(el => (el.innerText || '').trim().slice(0, 80))
                            .filter(Boolean);
                        return t;
                    }"""
                )
                n = len(preview.get("previews", []))
                print(f"    注入={'✓' if case_out.get('injected') else '✗'} 预览={n} 个 toast={case_out['toast'][:2]}")
                report["sections"][f"case_{name}"] = case_out
                await page.screenshot(path=str(REPORT_DIR / f"p7_{name}.png"))
                # 清理目标标记
                await page.evaluate("() => document.querySelectorAll('.mofox-target').forEach(e => e.classList.remove('mofox-target'))")

        finally:
            await context.close()

    out = REPORT_DIR / "report7.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== 附件探测完成 ==\n  报告: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
