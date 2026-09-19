"""历史会话切换与 Gemini 模型匹配验证脚本（无需登录、不触网）。

用真实 Chrome 承载"仿真站点页面"（按真实域名与 DOM 结构生成，请求经路由
拦截在本地应答），直接调用插件生产代码：站点 JS 脚本常量（services/*/constants）
与动作类（GeminiActions / BrowserActions / DoubaoActions）。因此可验证：

1. 生产 JS 脚本的语法与匹配逻辑（在真实浏览器引擎中执行，语法错误会直接抛出）
2. Gemini 模型关键词匹配（多版本并存取最高版本、同族跳过、未知关键词报错）
3. 三个站点的 open_conversation：同会话跳过、切换成功、切换无效不阻断（只记日志）
4. set_thinking 扩展思考开关（与 set_model 共用模型菜单选择器）

用法（插件目录或项目根均可）：
    set PYTHONIOENCODING=utf-8 && uv run python scripts/verify_conversation_switch.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile
from typing import Any

import playwright.async_api as pw

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROJECT_ROOT = PLUGIN_ROOT.parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from services.deepseek.actions import BrowserActions  # noqa: E402
from services.doubao.actions import DoubaoActions  # noqa: E402
from services.gemini.actions import GeminiActions  # noqa: E402
from services.deepseek import constants as DS  # noqa: E402
from services.doubao import constants as DC  # noqa: E402
from services.gemini import constants as GC  # noqa: E402

FAILED: list[str] = []
PASSED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    """记录一条断言结果。"""
    global PASSED
    if cond:
        PASSED += 1
        print(f"  [OK]   {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}  {detail}")


def chrome_path() -> str:
    """探测系统 Chrome 路径（找不到返回空，退回 Playwright Chromium）。"""
    for cand in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if pathlib.Path(cand).is_file():
            return cand
    return ""


# ---------------------------------------------------------------------------
# 仿真站点页面（结构与真实站点探测结论一致）
# ---------------------------------------------------------------------------

_GEMINI_JS = """
(function () {
    window.__clicks = [];
    window.__menuOpen = false;
    const btn = document.querySelector("button[aria-label*='打开模式选择器']");
    const menu = document.getElementById('model-menu');
    const items = Array.from(document.querySelectorAll("[role='menuitem']"));
    function setLabel() {
        btn.setAttribute('aria-label', '打开模式选择器，当前模式为“' + base + (thinking ? ' 扩展' : '') + '”');
    }
    function closeMenu() { window.__menuOpen = false; menu.style.display = 'none'; }
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { closeMenu(); }
    });
    btn.addEventListener('click', function () {
        window.__clicks.push('switch-button');
        window.__menuOpen = !window.__menuOpen;
        menu.style.display = window.__menuOpen ? 'block' : 'none';
    });
    items.forEach(function (el) {
        el.addEventListener('click', function () {
            window.__clicks.push(el.innerText.trim());
            if (el.innerText.indexOf('扩展思考') >= 0) {
                thinking = !thinking;
                if (thinking) { el.classList.add('selected'); } else { el.classList.remove('selected'); }
                closeMenu();
                setLabel();
                return;
            }
            base = el.innerText.trim();
            setLabel();
            closeMenu();
        });
    });
})();
"""

_GEMINI_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  mat-mdc-list-item, mat-mdc-list-item a { display: block; width: 220px; height: 30px; }
  #model-menu { display: none; }
  [role='menuitem'] { display: block; width: 200px; height: 24px; }
</style></head>
<body class="light-theme" data-cur="__CUR__">
  <span id="model-state" data-base="__BASE__" data-thinking="__THINK__"></span>
  <button class="input-area-switch" aria-label="打开模式选择器，当前模式为“__LABEL__”">模型</button>
  <div id="model-menu" role="menu">
__MENU_ITEMS__
  </div>
  <mat-nav-list>
__CONV_ITEMS__
  </mat-nav-list>
  <script>
    var base = document.getElementById('model-state').getAttribute('data-base');
    var thinking = document.getElementById('model-state').getAttribute('data-thinking') === '1';
  </script>
  <script>__JS__</script>
</body></html>
"""


def gemini_html(cur_id: str, model: str, thinking: bool) -> str:
    """构造仿真 Gemini 页面：模型菜单（含多版本）+ 历史会话侧边栏。"""
    base = model.replace(" 扩展", "").strip() or "3.7 Flash"
    menu_texts = ["3.5 Flash-Lite", "3.6 Flash", "3.7 Flash", "3.1 Pro", "扩展思考"]
    items = []
    for text in menu_texts:
        cls = ""
        if text == "扩展思考" and thinking:
            cls = ' class="selected"'
        items.append(f'    <div role="menuitem"{cls}>{text}</div>')
    convs = [("aaa111aaa111", "会话甲"), ("bbb222bbb222", "会话乙"), ("ccc333ccc333", "死链会话")]
    rows = []
    # 侧边栏固定操作项：真实页面在首页也带选中标记，必须先于会话项且被跳过
    rows.append(
        '    <mat-mdc-list-item class="mat-mdc-list-item is-active">'
        '<a href="javascript:;">发起新对话</a></mat-mdc-list-item>'
    )
    for cid, title in convs:
        cls = "mat-mdc-list-item" + (" is-active" if cid == cur_id else "")
        href = "javascript:;" if title == "死链会话" else f"/app/{cid}"
        rows.append(f'    <mat-mdc-list-item class="{cls}"><a href="{href}">{title}</a></mat-mdc-list-item>')
    html = _GEMINI_HTML
    html = html.replace("__CUR__", cur_id)
    html = html.replace("__BASE__", base)
    html = html.replace("__THINK__", "1" if thinking else "0")
    html = html.replace("__LABEL__", base + (" 扩展" if thinking else ""))
    html = html.replace("__MENU_ITEMS__", "\n".join(items))
    html = html.replace("__CONV_ITEMS__", "\n".join(rows))
    html = html.replace("__JS__", _GEMINI_JS)
    return html


_DS_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  [class*='dc04ec1d'] a { display: block; width: 220px; height: 30px; }
</style></head>
<body>
  <div class="dc04ec1d" style="position:absolute;left:0;top:0;width:260px">
__CONV_ITEMS__
  </div>
  <div class="ds-messages">
__MSGS__
  </div>
</body></html>
"""


def deepseek_html(cur_id: str) -> str:
    """构造仿真 DeepSeek 页面：侧边栏历史项（href 含会话 UUID）+ 消息列表。"""
    convs = [
        ("11111111-1111-1111-1111-111111111111", "旧会话甲"),
        ("22222222-2222-2222-2222-222222222222", "旧会话乙"),
    ]
    rows = [
        f'    <a href="/a/chat/s/{cid}">{title}</a>' for cid, title in convs
    ]
    # 死链项：可被标题匹配并点击，但点击不导航（href 无会话 ID），会话不变
    rows.append('    <a href="javascript:;">死链会话</a>')
    count = 2 if cur_id.startswith("11111111") else 5
    msgs = "\n".join(f'    <div class="ds-message">消息 {i}</div>' for i in range(count))
    html = _DS_HTML
    html = html.replace("__CONV_ITEMS__", "\n".join(rows))
    html = html.replace("__MSGS__", msgs)
    return html


_DB_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  a[class*='conversation-item'] { display: block; width: 220px; height: 30px; }
</style></head>
<body>
  <div class="sidebar">
__CONV_ITEMS__
  </div>
</body></html>
"""


def doubao_html(cur_id: str) -> str:
    """构造仿真豆包页面：侧边栏会话项（href=/chat/<数字ID>）。"""
    convs = [("111111", "豆包会话甲"), ("222222", "豆包会话乙")]
    rows = []
    for cid, title in convs:
        active = ' aria-current="page"' if cid == cur_id else ""
        rows.append(f'    <a class="conversation-item" href="/chat/{cid}"{active}>{title}</a>')
    # 死链项：href 合法但点击被拦截，会话不切换
    rows.append(
        '    <a class="conversation-item" href="/chat/999999"'
        ' onclick="return false">豆包死链会话</a>'
    )
    return _DB_HTML.replace("__CONV_ITEMS__", "\n".join(rows))


# ---------------------------------------------------------------------------
# 验证流程
# ---------------------------------------------------------------------------


async def verify_gemini(page: Any) -> None:
    """Gemini：模型关键词匹配 + 历史会话切换。"""
    print("\n=== Gemini ===")

    await page.goto("https://gemini.google.com/app/aaa111aaa111", wait_until="domcontentloaded")
    actions = GeminiActions(page)

    # --- 模型菜单脚本：多版本取最高版、lite 与 flash 不混淆 ---
    state = await page.evaluate(GC.MODEL_ITEM_SELECTED_SCRIPT, "flash")
    check(
        "MODEL_ITEM_SELECTED_SCRIPT: flash 命中最高版本 3.7 Flash",
        isinstance(state, dict) and state.get("hit") and state.get("text") == "3.7 Flash",
        f"got={state}",
    )
    state = await page.evaluate(GC.MODEL_ITEM_SELECTED_SCRIPT, "flash-lite")
    check(
        "MODEL_ITEM_SELECTED_SCRIPT: flash-lite 命中 3.5 Flash-Lite",
        isinstance(state, dict) and state.get("text") == "3.5 Flash-Lite",
        f"got={state}",
    )
    state = await page.evaluate(GC.MODEL_ITEM_SELECTED_SCRIPT, "pro")
    check(
        "MODEL_ITEM_SELECTED_SCRIPT: pro 命中 3.1 Pro",
        isinstance(state, dict) and state.get("text") == "3.1 Pro",
        f"got={state}",
    )
    state = await page.evaluate(GC.MODEL_ITEM_SELECTED_SCRIPT, "claude")
    check(
        "MODEL_ITEM_SELECTED_SCRIPT: 未知关键词不命中",
        isinstance(state, dict) and not state.get("hit"),
        f"got={state}",
    )

    # --- set_model：已在该族 → 跳过（不开菜单、不点击） ---
    await page.evaluate("() => { window.__clicks = []; }")
    ok, msg = await actions.set_model("Flash")
    clicks = await page.evaluate("() => window.__clicks.slice()")
    check("set_model: 已是 Flash 族 → 直接成功", ok and "3.7 Flash" in msg, f"ok={ok} msg={msg}")
    check("set_model: 已在该族时不打开菜单", clicks == [], f"clicks={clicks}")

    # --- set_model：跨族切换 ---
    ok, msg = await actions.set_model("Pro")
    clicks = await page.evaluate("() => window.__clicks.slice()")
    check("set_model: 切到 Pro 成功", ok and msg == "3.1 Pro", f"ok={ok} msg={msg}")
    check("set_model: 打开菜单并点到 3.1 Pro", clicks == ["switch-button", "3.1 Pro"], f"clicks={clicks}")
    check("set_model: get_model 读到 3.1 Pro", (await actions.get_model()) == "3.1 Pro")

    # --- set_model：按族回到 Flash（取最高版本，忽略 3.6） ---
    ok, msg = await actions.set_model("Flash")
    clicks = await page.evaluate("() => window.__clicks.slice()")
    check("set_model: 按族切 Flash 命中 3.7 Flash", ok and msg == "3.7 Flash", f"ok={ok} msg={msg}")
    check("set_model: 未误点 3.6 Flash", "3.6 Flash" not in clicks, f"clicks={clicks}")

    ok, msg = await actions.set_model("Flash-Lite")
    check("set_model: 切 Flash-Lite 成功", ok and msg == "3.5 Flash-Lite", f"ok={ok} msg={msg}")

    # --- set_model：完整模型名（含版本号）也能用 ---
    ok, msg = await actions.set_model("3.7 Flash")
    check("set_model: 带版本号全名仍可用", ok and msg == "3.7 Flash", f"ok={ok} msg={msg}")

    # --- set_model：未知关键词 / 扩展思考 ---
    ok, msg = await actions.set_model("Claude")
    check("set_model: 未知模型报错", (not ok) and "未找到模型" in msg, f"ok={ok} msg={msg}")
    ok, msg = await actions.set_model("扩展思考")
    check("set_model: 扩展思考走独立开关提示", (not ok) and "独立开关" in msg, f"ok={ok} msg={msg}")

    # --- 扩展思考开关（共用菜单选择器） ---
    ok, msg = await actions.set_thinking(True)
    check("set_thinking: 开启成功", ok and await actions.get_thinking(), f"ok={ok} msg={msg}")
    ok, msg = await actions.set_thinking(False)
    check("set_thinking: 关闭成功", ok and not await actions.get_thinking(), f"ok={ok} msg={msg}")

    # --- open_conversation：已在目标会话 → 直接成功且不导航 ---
    before_url = page.url
    ok = await actions.open_conversation("会话甲")
    check("open_conversation: 已在会话甲 → True", ok)
    check("open_conversation: 已在会话甲时不导航", page.url == before_url, f"url={page.url}")

    # --- open_conversation：切换成功 ---
    ok = await actions.open_conversation("会话乙")
    check("open_conversation: 切到会话乙 → True", ok)
    check("open_conversation: URL 已切换到会话乙", page.url.endswith("bbb222bbb222"), f"url={page.url}")

    # --- open_conversation：重复调用幂等 ---
    nav_before = page.url
    ok = await actions.open_conversation("会话乙")
    check("open_conversation: 重复调用幂等 → True", ok and page.url == nav_before, f"ok={ok} url={page.url}")

    # --- open_conversation：标题不存在 ---
    ok = await actions.open_conversation("不存在的会话")
    check("open_conversation: 标题不存在 → False", not ok)

    # --- open_conversation：点击后会话不切换（死链）→ 不阻断，超时只记日志 ---
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    ok = await actions.open_conversation("死链会话")
    cost = loop.time() - t0
    check("open_conversation: 切换无效时仍返回 True（不阻断后续动作）", ok, f"ok={ok}")
    check("open_conversation: 确实等待了轮询窗口", cost >= 7.0, f"cost={cost:.1f}s")

    # --- 活跃会话读取脚本 ---
    await page.goto("https://gemini.google.com/app/bbb222bbb222", wait_until="domcontentloaded")
    check("ACTIVE_CONVERSATION_ID_SCRIPT 读取会话 ID", (await actions.get_active_conversation_id()) == "bbb222bbb222")
    check("ACTIVE_CONVERSATION_TITLE_SCRIPT 读取活跃标题", (await actions.get_active_conversation_title()) == "会话乙")
    titles = await actions.list_conversations()
    check("HISTORY_LIST_SCRIPT 列出会话标题", titles == ["会话甲", "会话乙", "死链会话"], f"titles={titles}")

    # --- 首页（无会话 ID）：固定入口项不得被当成活跃会话标题 ---
    await page.goto("https://gemini.google.com/app/", wait_until="domcontentloaded")
    check(
        "ACTIVE_CONVERSATION_TITLE_SCRIPT 首页不返回固定入口名",
        (await actions.get_active_conversation_title()) == "",
        f"got={await actions.get_active_conversation_title()!r}",
    )


async def verify_deepseek(page: Any) -> None:
    """DeepSeek：历史会话切换（含侧边栏展开、指纹兜底）。"""
    print("\n=== DeepSeek ===")
    actions = BrowserActions(page)

    await page.goto(
        "https://chat.deepseek.com/a/chat/s/11111111-1111-1111-1111-111111111111",
        wait_until="domcontentloaded",
    )
    titles = await actions.list_conversations()
    check(
        "HISTORY_LIST_SCRIPT 列出会话标题",
        titles == ["旧会话甲", "旧会话乙", "死链会话"],
        f"titles={titles}",
    )
    check(
        "ACTIVE_CONVERSATION_TITLE_SCRIPT 按 URL 反查标题",
        (await actions.get_active_conversation_title()) == "旧会话甲",
        f"got={await actions.get_active_conversation_title()}",
    )

    before_url = page.url
    ok = await actions.open_conversation("旧会话甲")
    check("open_conversation: 已在旧会话甲 → True", ok)
    check("open_conversation: 已在旧会话甲时不导航", page.url == before_url, f"url={page.url}")

    ok = await actions.open_conversation("旧会话乙")
    check("open_conversation: 切到旧会话乙 → True", ok, f"url={page.url}")
    check(
        "open_conversation: URL 已切换到旧会话乙",
        page.url.endswith("22222222-2222-2222-2222-222222222222"),
        f"url={page.url}",
    )

    ok = await actions.open_conversation("不存在的会话")
    check("open_conversation: 标题不存在 → False", not ok)

    # --- open_conversation：点击后会话不切换（死链）→ 不阻断，超时只记日志 ---
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    ok = await actions.open_conversation("死链会话")
    cost = loop.time() - t0
    check("open_conversation: 切换无效时仍返回 True（不阻断后续动作）", ok, f"ok={ok}")
    check("open_conversation: 确实等待了轮询窗口", cost >= 7.0, f"cost={cost:.1f}s")
    check(
        "open_conversation: 切换无效时页面未被误导航",
        page.url.endswith("22222222-2222-2222-2222-222222222222"),
        f"url={page.url}",
    )

    hit = await page.evaluate(DS.HISTORY_OPEN_SCRIPT, "不存在的会话")
    check("HISTORY_OPEN_SCRIPT 未命中时返回 ok=False", isinstance(hit, dict) and not hit.get("ok"), f"hit={hit}")


async def verify_doubao(page: Any) -> None:
    """豆包：历史会话切换（含 href 精确点击与 ID 校验）。"""
    print("\n=== 豆包 ===")
    actions = DoubaoActions(page)

    await page.goto("https://www.doubao.com/chat/111111", wait_until="domcontentloaded")
    titles = await actions.list_conversations()
    check(
        "HISTORY_LIST_SCRIPT 列出会话标题",
        titles == ["豆包会话甲", "豆包会话乙", "豆包死链会话"],
        f"titles={titles}",
    )
    check("ACTIVE_TITLE_SCRIPT 读取活跃标题", (await actions.get_active_conversation_title()) == "豆包会话甲")

    hit = await page.evaluate(DC.HISTORY_ITEM_HIT_SCRIPT, "豆包会话乙")
    check(
        "HISTORY_ITEM_HIT_SCRIPT 返回会话 ID 与 href",
        isinstance(hit, dict) and hit.get("id") == "222222" and hit.get("href") == "/chat/222222",
        f"hit={hit}",
    )
    hit = await page.evaluate(DC.HISTORY_ITEM_HIT_SCRIPT, "豆包")
    check("HISTORY_ITEM_HIT_SCRIPT 包含匹配回退可用", isinstance(hit, dict) and hit.get("id") == "111111", f"hit={hit}")
    hit = await page.evaluate(DC.HISTORY_ITEM_HIT_SCRIPT, "没有这个会话")
    check("HISTORY_ITEM_HIT_SCRIPT 未命中返回空", isinstance(hit, dict) and not hit.get("id"), f"hit={hit}")

    before_url = page.url
    ok = await actions.open_conversation("豆包会话甲")
    check("open_conversation: 已在豆包会话甲 → True", ok)
    check("open_conversation: 已在豆包会话甲时不导航", page.url == before_url, f"url={page.url}")

    ok = await actions.open_conversation("豆包会话乙")
    check("open_conversation: 切到豆包会话乙 → True", ok, f"url={page.url}")
    check("open_conversation: URL 已切换到会话乙", page.url.endswith("/chat/222222"), f"url={page.url}")

    navs: list[str] = []
    page.on("framenavigated", lambda f: navs.append(f.url if f == page.main_frame else ""))
    ok = await actions.open_conversation("没有这个会话")
    check("open_conversation: 标题不存在 → False", not ok)
    check("open_conversation: 标题不存在时不跳转页面", navs == [], f"navs={navs}")

    # --- open_conversation：点击后会话不切换（死链）→ 不阻断，超时只记日志 ---
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    ok = await actions.open_conversation("豆包死链会话")
    cost = loop.time() - t0
    check("open_conversation: 切换无效时仍返回 True（不阻断后续动作）", ok, f"ok={ok}")
    check("open_conversation: 确实等待了轮询窗口", cost >= 7.0, f"cost={cost:.1f}s")


async def main() -> int:
    """启动真实浏览器并执行全部验证。"""
    chrome = chrome_path()
    tmp_profile = tempfile.mkdtemp(prefix="aiui_verify_")
    print(f"== 真实浏览器验证 == Chrome={chrome or 'Playwright Chromium'} profile={tmp_profile}")
    async with pw.async_playwright() as p:
        kwargs: dict[str, Any] = {
            "user_data_dir": tmp_profile,
            "headless": True,
            "viewport": {"width": 1280, "height": 900},
        }
        if chrome:
            kwargs["executable_path"] = chrome
        context = await p.chromium.launch_persistent_context(**kwargs)

        async def route_handler(route: Any) -> None:
            url = route.request.url
            if "gemini.google.com/app" in url:
                cur = url.rsplit("/", 1)[-1]
                if not cur.isalnum():
                    cur = ""
                await route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=gemini_html(cur, "3.7 Flash", False),
                )
            elif "chat.deepseek.com/a/chat/s/" in url:
                await route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=deepseek_html(url.rsplit("/", 1)[-1]),
                )
            elif "www.doubao.com/chat/" in url:
                await route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=doubao_html(url.rsplit("/", 1)[-1]),
                )
            else:
                await route.abort()

        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.route("**/*", route_handler)
            await verify_gemini(page)
            await verify_deepseek(page)
            await verify_doubao(page)
        finally:
            await context.close()

    print(f"\n== 结果 == 通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  !! {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
