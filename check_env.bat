@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
echo ============================================
echo  SeeName - local GGUF environment check
echo ============================================
echo.
echo Inference uses Python and llama.cpp. Ollama is not required.
echo Model files: a supported GGUF main model plus matching mmproj.
echo FFmpeg is still required for video extraction and metadata.
echo.
where conda >nul 2>nul
if not errorlevel 1 (
    call conda run --no-capture-output -n seename python -B -c "import engine; print('Model directory:', engine.models_dir(False)); print('Usable model pairs:', engine.installed_models()); print('Binding initialized:', engine.start_server()); print('Backend:', engine.runtime_status()); engine.shutdown_server()"
) else (
    python -B -c "import engine; print('Model directory:', engine.models_dir(False)); print('Usable model pairs:', engine.installed_models()); print('Binding initialized:', engine.start_server()); print('Backend:', engine.runtime_status()); engine.shutdown_server()"
)
echo.
echo No model weights were loaded by this check.
pause
