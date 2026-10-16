@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo  SeeName - environment check (by tanhua)
echo ============================================
echo.

if exist "%~dp0ffmpeg.exe" (echo [OK]   ffmpeg.exe found) else (echo [FAIL] ffmpeg.exe MISSING)
if exist "%~dp0runtime\ollama\ollama.exe" (echo [OK]   runtime\ollama\ollama.exe found) else (echo [FAIL] runtime\ollama\ollama.exe MISSING)
if exist "%~dp0models" (echo [OK]   models folder found) else (echo [WARN] models folder missing, will be created)

echo.
echo ---- bundled runtime version ----
if exist "%~dp0runtime\ollama\ollama.exe" "%~dp0runtime\ollama\ollama.exe" --version

echo.
echo ---- model store ----
if exist "%~dp0models" dir /b "%~dp0models\blobs" 2>nul | find /c /v "" > "%TEMP%\_th_count.txt"
if exist "%TEMP%\_th_count.txt" (
    set /p BLOBS=<"%TEMP%\_th_count.txt"
    call echo model blobs: %%BLOBS%%
    del "%TEMP%\_th_count.txt" 2>nul
)

echo.
echo ---- GUI self test ----
set "PY="
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
) do if not defined PY if exist %%P set "PY=%%P"

if not defined PY (
    echo [WARN] Python not found - skipped. Double-click SeeName.exe instead.
) else (
    "%PY%" "%~dp0main.py" --selftest
)

echo.
pause
