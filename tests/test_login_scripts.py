"""手工浏览器入口的配置复用、占用检查与登录流程回归测试。"""

from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

if TYPE_CHECKING:
    from scripts.login_common import AiUiSnapshotConfig

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _PLUGIN_ROOT.parent.parent
for import_root in (_PROJECT_ROOT, _PLUGIN_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

login_common = importlib.import_module("scripts.login_common")


@pytest.fixture
def browser_config(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AiUiSnapshotConfig:
    """提供独立配置并禁止真实启动浏览器及查询进程。"""
    config = login_common.AiUiSnapshotConfig()
    config.web.web_profile_dir = str(tmp_path / "profile with spaces")
    config.web.proxy_url = "http://proxy.example:8080"
    config.screenshot.browser_path = str(tmp_path / "Chrome Application" / "chrome.exe")
    monkeypatch.setattr(login_common, "load_plugin_config", lambda: config)
    monkeypatch.setattr(login_common, "browser_pids", MagicMock(return_value=[]))
    monkeypatch.setattr(login_common.subprocess, "Popen", MagicMock())
    return config


@pytest.mark.asyncio
@pytest.mark.parametrize("site", ["deepseek", "gemini", "doubao"])
async def test_open_only_reuses_browser_profile_and_site_proxy(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    browser_config: AiUiSnapshotConfig,
    site: str,
) -> None:
    """任意工作目录下沿用浏览器和资料目录，仅 Gemini 使用代理。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [f"login_{site}.py", "--open-only"])
    verify = AsyncMock()
    wait = AsyncMock()
    ready = AsyncMock(return_value=True)
    monkeypatch.setattr(login_common, "verify_login", verify)
    monkeypatch.setattr(login_common, "wait_browser_closed", wait)
    url = "https://chat.example/"

    result = await login_common.run_login_flow(site=site, url=url, is_ready=ready)

    expected = [
        browser_config.screenshot.browser_path,
        f"--user-data-dir={pathlib.Path(browser_config.web.web_profile_dir) / site}",
    ]
    if site == "gemini":
        expected.append("--proxy-server=http://proxy.example:8080")
    expected.append(url)
    assert result == 0
    login_common.subprocess.Popen.assert_called_once_with(expected)
    login_common.subprocess.Popen.return_value.terminate.assert_not_called()
    verify.assert_not_awaited()
    wait.assert_not_awaited()
    ready.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_only_rejects_occupied_profile(
    monkeypatch: pytest.MonkeyPatch,
    browser_config: AiUiSnapshotConfig,
) -> None:
    """Windows 下资料目录已被占用时不启动第二个浏览器。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["login_doubao.py", "--open-only"])
    monkeypatch.setattr(login_common, "browser_pids", MagicMock(return_value=[123]))

    result = await login_common.run_login_flow(
        site="doubao", url="https://chat.example/", is_ready=AsyncMock(),
    )

    assert result == 1
    login_common.browser_pids.assert_called_once_with(
        pathlib.Path(browser_config.web.web_profile_dir) / "doubao",
    )
    login_common.subprocess.Popen.assert_not_called()


@pytest.mark.asyncio
async def test_open_only_does_not_require_powershell_on_other_platforms(
    monkeypatch: pytest.MonkeyPatch,
    browser_config: AiUiSnapshotConfig,
) -> None:
    """非 Windows Python 入口不调用 Windows 进程检查。"""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "argv", ["login_deepseek.py", "--open-only"])

    result = await login_common.run_login_flow(
        site="deepseek", url="https://chat.example/", is_ready=AsyncMock(),
    )

    assert result == 0
    login_common.browser_pids.assert_not_called()


@pytest.mark.asyncio
async def test_open_only_reports_missing_browser(
    monkeypatch: pytest.MonkeyPatch,
    browser_config: AiUiSnapshotConfig,
) -> None:
    """未找到 Chrome 时返回失败，不回退到自动化浏览器。"""
    monkeypatch.setattr(sys, "argv", ["login_gemini.py", "--open-only"])
    monkeypatch.setattr(login_common, "resolve_browser_path", MagicMock(return_value=""))

    result = await login_common.run_login_flow(
        site="gemini", url="https://chat.example/", is_ready=AsyncMock(),
    )

    assert result == 1
    login_common.subprocess.Popen.assert_not_called()


@pytest.mark.asyncio
async def test_open_only_reports_browser_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
    browser_config: AiUiSnapshotConfig,
) -> None:
    """Chrome 启动失败时返回非零退出码。"""
    monkeypatch.setattr(sys, "argv", ["login_doubao.py", "--open-only"])
    monkeypatch.setattr(login_common.subprocess, "Popen", MagicMock(side_effect=OSError))

    result = await login_common.run_login_flow(
        site="doubao", url="https://chat.example/", is_ready=AsyncMock(),
    )

    assert result == 1


@pytest.mark.asyncio
async def test_default_login_still_waits_and_verifies(
    monkeypatch: pytest.MonkeyPatch,
    browser_config: AiUiSnapshotConfig,
) -> None:
    """未传 --open-only 时仍等待窗口关闭并校验登录态。"""
    monkeypatch.setattr(sys, "argv", ["login_doubao.py"])
    wait = AsyncMock(return_value=True)
    verify = AsyncMock(return_value=True)
    monkeypatch.setattr(login_common, "wait_browser_closed", wait)
    monkeypatch.setattr(login_common, "verify_login", verify)

    result = await login_common.run_login_flow(
        site="doubao", url="https://chat.example/", is_ready=AsyncMock(),
    )

    assert result == 0
    wait.assert_awaited_once()
    verify.assert_awaited_once()


def test_profile_process_check_normalizes_slashes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """资料目录占用检查兼容两种斜杠且不将路径视为通配表达式。"""
    process = MagicMock(stdout="123\nnot-a-pid\n456\n")
    run = MagicMock(return_value=process)
    monkeypatch.setattr(login_common.subprocess, "run", run)

    assert login_common.browser_pids(tmp_path / "profile [test]") == [123, 456]
    script = run.call_args.args[0][-1]
    assert "$p=$p.Replace('/', '\\')" in script
    assert "$_.CommandLine.Replace('/', '\\').IndexOf($p" in script
    assert "-like" not in script


@pytest.mark.parametrize("site", ["deepseek", "gemini", "doubao"])
def test_bat_launchers_use_script_relative_project_and_preserve_exit_code(
    site: str,
) -> None:
    """BAT 入口用自身位置定位项目，并将启动结果返回调用方。"""
    script = (_PLUGIN_ROOT / "scripts" / f"open_{site}.bat").read_text(encoding="ascii")

    assert 'call uv run --project "%~dp0..\\..\\.." python' in script
    assert f'"%~dp0login_{site}.py" --open-only %*' in script
    assert 'set "result=%errorlevel%"' in script
    assert "exit /b %result%" in script


@pytest.mark.skipif(sys.platform != "win32", reason="BAT execution requires Windows")
@pytest.mark.parametrize("site", ["deepseek", "gemini", "doubao"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_bat_execution_from_another_directory(
    tmp_path: pathlib.Path,
    site: str,
    exit_code: int,
) -> None:
    """真实 cmd 执行可正确传递含空格路径和 uv 的退出码。"""
    project = tmp_path / "project with spaces"
    scripts = project / "plugins" / "example" / "scripts"
    scripts.mkdir(parents=True)
    launcher = scripts / f"open_{site}.bat"
    launcher.write_bytes((_PLUGIN_ROOT / "scripts" / launcher.name).read_bytes())
    tools = tmp_path / "stub tools"
    tools.mkdir()
    (tools / "uv.cmd").write_text(
        f"@echo off\necho UV_ARGUMENTS:%*\nexit /b {exit_code}\n",
        encoding="ascii",
    )
    environment = dict(os.environ)
    environment["PATH"] = str(tools) + os.pathsep + environment["PATH"]

    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(launcher), "--help"],
        cwd=tmp_path,
        env=environment,
        input="\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == exit_code
    assert f'--project "{scripts}\\..\\..\\.."' in result.stdout
    assert f'python "{scripts / f"login_{site}.py"}" --open-only --help' in result.stdout