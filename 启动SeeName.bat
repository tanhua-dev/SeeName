@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "%~dp0SeeName.exe" (
    start "" "%~dp0SeeName.exe"
    exit /b
)

set "PY="
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
) do if not defined PY if exist %%P set "PY=%%P"

if not defined PY (
    for %%P in (pythonw.exe python.exe) do (
        for /f "delims=" %%F in ('where %%P 2^>nul') do if not defined PY set "PY=%%F"
    )
)

if not defined PY (
    echo.
    echo  [X] Python not found.
    echo      - If SeeName.exe exists in this folder, just double-click it.
    echo      - Otherwise run this folder's check_env.bat to see what is missing.
    echo.
    pause
    exit /b 1
)

start "" "%PY%" "%~dp0main.py"
