@echo off
setlocal
where uv >nul 2>nul
if errorlevel 1 (
    echo uv was not found. Install uv and make sure it is available on PATH.
    pause
    exit /b 1
)
call uv run --project "%~dp0..\..\.." python "%~dp0login_gemini.py" --open-only %*
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%