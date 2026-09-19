"""真实站点验证：历史会话进入 + Gemini 模型族关键词匹配。

走插件的浏览器会话层（BrowserSessionManager）+ 真实登录态 profile + 真实站点，
验证会话定位与模型切换在真实页面上确实生效（仿真页面验证见
``verify_conversation_switch.py``，两者互补）。

**运行前提**：bot 已关闭（同一 profile 双开 Chrome 会崩溃）。脚本只做定位与
切换，不提问、不发消息；结束时自动还原（回到站点首页、切回原模型族）。

用法：
    set PYTHONIOENCODING=utf-8 && uv run python scripts/verify_real_conversation_switch.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from typing import Any, Callable

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROJECT_ROOT = PLUGIN_ROOT.parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from services.base.browser_session import BrowserSessionManager  # noqa: E402
from services.deepseek.actions import BrowserActions  # noqa: E402
from services.doubao.actions import DoubaoActions  # noqa: E402
from services.gemini import constants as GC  # noqa: E402
from services.gemini.actions import GeminiActions  # noqa: E402

FAILED: list[str] = []
PASSED = 0
MISSING_TITLE = "验证用不存在的会话标题_zzz"
# 各站点默认入口（用于验证结束后还原页面位置）
SITE_URLS: dict[str, str] = {
    "gemini": "https://gemini.google.com/app",
    "doubao": "https://www.doubao.com/chat/",
    "deepseek": "https://chat.deepseek.com/",
}


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
    """探测系统 Chrome 路径。"""
    for cand in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if pathlib.Path(cand).is_file():
            return cand
    return ""


def proxy_url() -> str:
    """Gemini 访问代理（环境变量优先）。"""
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("ALL_PROXY")
        or "http://127.0.0.1:7890"
    )


async def verify_site(
    mgr: BrowserSessionManager,
    theme: str,
    factory: Callable[[Any], Any],
) -> None:
    """验证单个站点的历史会话进入行为。"""
    print(f"\n=== {theme} ===")
    session = await mgr.get(f"verify_{theme}", theme=theme)
    actions = factory(session.page)
    await session.page.wait_for_timeout(2000)

    cur_title = await actions.get_active_conversation_title()
    cur_id = await actions.get_active_conversation_id()
    titles = await actions.list_conversations()
    print(f"  活跃会话: {cur_title!r}  id={cur_id!r}  历史会话 {len(titles)} 条")
    check(f"{theme}: 能列出历史会话", len(titles) > 0, f"titles={titles[:3]}")
    check(
        f"{theme}: 初始标题不误报固定入口",
        cur_title not in ("发起新对话", "新对话", "搜索对话内容"),
        f"title={cur_title!r}",
    )

    # 1. 进入一个非当前的历史会话，切换必须真实生效
    target = next((t for t in titles if t != cur_title), "")
    if not target:
        print("  (跳过切换验证：只有一条历史会话)")
    else:
        url_before = session.page.url
        ok = await actions.open_conversation(target)
        check(f"{theme}: open_conversation 找到并打开 [{target}]", ok, f"ok={ok}")
        await session.page.wait_for_timeout(2000)
        now_title = await actions.get_active_conversation_title()
        now_id = await actions.get_active_conversation_id()
        check(
            f"{theme}: 切换后活跃会话确实变为 [{target}]",
            now_title == target,
            f"now={now_title!r} id={now_id!r} url={session.page.url}",
        )
        check(f"{theme}: 切换后页面 URL 已变化", session.page.url != url_before, f"url={session.page.url}")

        # 2. 重复进入同一会话：幂等，不导航
        url_same = session.page.url
        ok = await actions.open_conversation(target)
        check(f"{theme}: 重复进入同一会话返回 True", ok, f"ok={ok}")
        check(f"{theme}: 重复进入同一会话不导航", session.page.url == url_same, f"url={session.page.url}")

    # 3. 标题不存在：返回 False
    ok = await actions.open_conversation(MISSING_TITLE)
    check(f"{theme}: 不存在的标题返回 False", not ok, f"ok={ok}")

    # 4. 还原：回到站点默认入口（不把 bot 会话留在某个历史会话里）
    await session.page.goto(SITE_URLS[theme], wait_until="domcontentloaded", timeout=60000)
    await session.page.wait_for_timeout(2000)


async def probe_families(actions: GeminiActions, families: tuple[str, ...]) -> list[str]:
    """打开真实模型菜单，返回实际可用的模型族。"""
    page = actions.page
    await page.evaluate(GC.OPEN_MODEL_MENU_SCRIPT)
    await page.wait_for_timeout(1000)
    hit: list[str] = []
    detail: list[str] = []
    for family in families:
        state = await page.evaluate(GC.MODEL_ITEM_SELECTED_SCRIPT, family.lower())
        info = state if isinstance(state, dict) else {}
        if info.get("hit"):
            hit.append(family)
            detail.append(f"{family}->{info.get('text')}")
    await page.keyboard.press("Escape")
    print(f"  菜单族命中: {detail}")
    return hit


async def verify_gemini_model(session: Any, actions: GeminiActions) -> None:
    """验证 Gemini 模型族关键词匹配（真实菜单）。"""
    print("\n=== gemini 模型匹配（真实菜单）===")
    origin = await actions.get_model() or ""
    print(f"  当前模型: {origin!r}")

    families = ("Flash-Lite", "Flash", "Pro")
    available = await probe_families(actions, families)
    check("gemini: 至少命中一个模型族", bool(available), f"available={available}")
    origin_family = actions.model_family(origin)
    available_lower = [f.lower() for f in available]
    check(
        "gemini: 当前模型的族在菜单可用",
        bool(origin_family) and origin_family in available_lower,
        f"origin={origin!r} family={origin_family!r} available={available}",
    )

    for family in available:
        if origin_family == family.lower():
            ok, msg = await actions.set_model(family)
            check(f"gemini: set_model({family}) 同族直接成功", ok, f"ok={ok} msg={msg}")
            continue
        ok, msg = await actions.set_model(family)
        check(f"gemini: set_model({family}) 成功", ok, f"ok={ok} msg={msg}")
        now = await actions.get_model() or ""
        check(
            f"gemini: set_model({family}) 后读回同族",
            actions.model_family(now) == actions.model_family(family),
            f"now={now!r}",
        )

    # 切回原模型的族，恢复用户设置
    if origin_family in available_lower:
        restore_to = next((f for f in available if f.lower() == origin_family), origin_family)
        ok, msg = await actions.set_model(restore_to)
        now = await actions.get_model() or ""
        check(
            f"gemini: 已切回原模型的族（{origin_family}）",
            ok and actions.model_family(now) == origin_family,
            f"ok={ok} msg={msg} now={now!r}",
        )

    ok, msg = await actions.set_model("Claude")
    check("gemini: 未知模型族报错", (not ok) and "未找到模型" in msg, f"ok={ok} msg={msg}")


async def main() -> int:
    """执行真实站点验证。"""
    mgr = BrowserSessionManager(
        profile_root="data/ai_ui_snapshot_profile",
        theme="deepseek",
        headless=True,
        browser_path=chrome_path(),
        proxy_url=proxy_url(),
    )
    try:
        for theme, factory in (
            ("gemini", lambda p: GeminiActions(p)),
            ("doubao", lambda p: DoubaoActions(p)),
            ("deepseek", lambda p: BrowserActions(p)),
        ):
            try:
                await verify_site(mgr, theme, factory)
            except Exception as exc:  # noqa: BLE001 - 单站点失败不阻塞其余
                FAILED.append(f"{theme}: 异常 {exc}")
                print(f"  [FAIL] {theme}: 异常 {type(exc).__name__}: {exc}")

        session = mgr.sessions.get("gemini:verify_gemini")
        if session is not None:
            try:
                await verify_gemini_model(session, GeminiActions(session.page))
            except Exception as exc:  # noqa: BLE001 - 模型验证失败记录即可
                FAILED.append(f"gemini 模型验证异常 {exc}")
                print(f"  [FAIL] gemini 模型验证异常 {type(exc).__name__}: {exc}")
    finally:
        await mgr.close_all()

    print(f"\n== 真实站点结果 == 通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  !! {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
