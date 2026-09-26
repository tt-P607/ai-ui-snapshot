"""浏览器会话共享与页面隔离测试。"""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _PLUGIN_ROOT.parent.parent
for import_root in (_PLUGIN_ROOT, _PROJECT_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from services.base.browser_session import (  # noqa: E402
    BrowserSessionManager,
    SiteBrowser,
)
from services.base import browser_session as browser_session_module  # noqa: E402


class FakePage:
    """记录导航与关闭状态的 Playwright 页面替身。"""

    def __init__(self) -> None:
        self.closed = False
        self.urls: list[str] = []

    async def goto(self, url: str, **_: object) -> None:
        """记录导航地址。"""
        self.urls.append(url)

    async def wait_for_timeout(self, _: int) -> None:
        """跳过真实等待。"""

    def is_closed(self) -> bool:
        """返回页面是否关闭。"""
        return self.closed

    async def close(self) -> None:
        """关闭页面。"""
        self.closed = True


class FakeContext:
    """为每次请求创建独立页面的浏览器上下文替身。"""

    def __init__(self) -> None:
        self.created_pages: list[FakePage] = []
        self.closed = False

    async def new_page(self) -> FakePage:
        """创建并记录新页面。"""
        page = FakePage()
        self.created_pages.append(page)
        return page

    async def close(self) -> None:
        """关闭上下文。"""
        self.closed = True


class FakePlaywright:
    """记录停止状态的 Playwright 替身。"""

    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        """停止 Playwright。"""
        self.stopped = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("site", "uses_proxy"),
    [("gemini", True), ("doubao", False), ("deepseek", False)],
)
async def test_proxy_only_applies_to_gemini(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    site: str,
    uses_proxy: bool,
) -> None:
    """Gemini 使用配置代理，国内站点保持直连。"""
    launch = AsyncMock(return_value=FakeContext())
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch_persistent_context=launch),
        stop=AsyncMock(),
    )
    factory = MagicMock(return_value=SimpleNamespace(start=AsyncMock(return_value=playwright)))
    monkeypatch.setattr("playwright.async_api.async_playwright", factory)
    monkeypatch.setattr(
        browser_session_module,
        "resolve_browser_path",
        MagicMock(return_value=""),
    )
    manager = BrowserSessionManager(
        profile_root=str(tmp_path),
        proxy_url="http://proxy.example:8080",
    )

    site_browser = await manager._launch_site_browser(site)
    launch_kwargs = launch.await_args.kwargs
    if uses_proxy:
        assert launch_kwargs["proxy"] == {"server": "http://proxy.example:8080"}
    else:
        assert "proxy" not in launch_kwargs

    await site_browser.context.close()
    await site_browser.playwright.stop()


@pytest.mark.asyncio
async def test_same_site_streams_share_context_but_isolate_pages(tmp_path) -> None:
    """同站点多个 stream 应共享 context，但各自拥有独立页面。"""
    context = FakeContext()
    playwright = FakePlaywright()
    manager = BrowserSessionManager(profile_root=str(tmp_path))
    launch = AsyncMock(return_value=SiteBrowser(context=context, playwright=playwright))
    manager._launch_site_browser = launch
    manager._ensure_cleanup_task = lambda: None

    first = await manager.get("stream-a", theme="doubao")
    second = await manager.get("stream-b", theme="doubao")

    assert launch.await_count == 1
    assert first.context is second.context is context
    assert first.page is not second.page
    assert first.page.urls == ["https://www.doubao.com/chat/"]
    assert second.page.urls == ["https://www.doubao.com/chat/"]

    await manager.close("stream-a", theme="doubao")

    assert first.page.closed is True
    assert second.page.closed is False
    assert context.closed is False

    await manager.close_all()

    assert second.page.closed is True
    assert context.closed is True
    assert playwright.stopped is True


@pytest.mark.asyncio
async def test_temporary_page_does_not_replace_stream_page(tmp_path) -> None:
    """媒体临时页面关闭后不应影响普通对话页面。"""
    context = FakeContext()
    manager = BrowserSessionManager(profile_root=str(tmp_path))
    manager._launch_site_browser = AsyncMock(
        return_value=SiteBrowser(context=context, playwright=FakePlaywright())
    )
    manager._ensure_cleanup_task = lambda: None

    session = await manager.get("stream-a", theme="doubao")
    media_page = await manager.open_page("doubao")
    await manager.close_page(media_page)

    assert media_page.closed is True
    assert session.page.closed is False
    assert session.page is not media_page

    await manager.close_all()


@pytest.mark.asyncio
async def test_new_conversation_page_preserves_previous_page(tmp_path) -> None:
    """同一 stream 新建对话页面时应保留旧页面。"""
    context = FakeContext()
    manager = BrowserSessionManager(profile_root=str(tmp_path))
    manager._launch_site_browser = AsyncMock(
        return_value=SiteBrowser(context=context, playwright=FakePlaywright())
    )
    manager._ensure_cleanup_task = lambda: None

    session = await manager.get("stream-a", theme="gemini")
    previous_page = session.page
    updated = await manager.new_session_page("stream-a", theme="gemini")

    assert updated is session
    assert session.page is not previous_page
    assert previous_page.closed is False
    assert len(session.pages) == 2

    await manager.close("stream-a", theme="gemini")

    assert previous_page.closed is True
    assert session.page.closed is True
    await manager.close_all()
