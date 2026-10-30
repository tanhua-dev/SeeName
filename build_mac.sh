#!/bin/sh
# 显影 SeeName —— macOS 打包脚本（在 Mac 上运行）
#
# 用法：
#   chmod +x build_mac.sh
#   ./build_mac.sh                     # 完整流程：装依赖 → 下 ffmpeg/ollama → 打 .app
#   ./build_mac.sh --dmg               # 额外打一个 dmg（便于分发）
#   ./build_mac.sh --models /path/models   # 把模型库拷到 dist 旁边（完整版）
#   ./build_mac.sh --no-download       # 不下载 ffmpeg/ollama（已有 runtime/ 与 PATH）
#
# 产物：dist/SeeName.app（双击即用；或拷到 /Applications）
#
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"

MAKE_DMG=0
MODELS=""
DOWNLOAD=1
for a in "$@"; do
    case "$a" in
        --dmg) MAKE_DMG=1 ;;
        --models) ;;
        --no-download) DOWNLOAD=0 ;;
        *) if [ -d "$a" ]; then MODELS="$a"; fi ;;
    esac
done
# --models 的取值在上面的循环里拿不到（独立参数），重新解析一次
PREV=""
for a in "$@"; do
    if [ "$PREV" = "--models" ]; then MODELS="$a"; fi
    PREV="$a"
done

ARCH="$(uname -m)"
echo "==> 架构: $ARCH   系统: $(sw_vers -productVersion)"

# ---------------------------------------------------------------- 1. venv
PY=python3
if [ ! -d .venv-mac ]; then
    echo "==> 创建 .venv-mac"
    $PY -m venv .venv-mac
fi
. .venv-mac/bin/activate
PY=.venv-mac/bin/python
echo "==> Python: $($PY -V)"

echo "==> 安装依赖（pillow / piexif / pyinstaller）"
$PY -m pip install --upgrade pip >/dev/null
$PY -m pip install pillow piexif pyinstaller >/dev/null

# tkinter 自检：macOS 自带的 python3（Command Line Tools）没有 Tk
$PY - <<'EOF' || { echo "!! 当前 Python 没有 tkinter。请用 python.org 的安装包（自带 Tk），"; echo "!! 或先执行: brew install python-tk"; exit 1; }
import tkinter  # noqa: F401
EOF

# ---------------------------------------------------------------- 2. 外部二进制
mkdir -p runtime/ollama

if [ "$DOWNLOAD" = "1" ]; then
    # ffmpeg 静态构建（evermeet.cx，只含 ffmpeg 单文件）
    if ! command -v ffmpeg >/dev/null 2>&1 && [ ! -f ffmpeg ]; then
        echo "==> 下载 ffmpeg（macOS 静态构建）"
        curl -L --fail -o /tmp/ffmpeg.zip https://evermeet.cx/ffmpeg/getrelease/zip
        rm -rf /tmp/ffmpeg_un && mkdir -p /tmp/ffmpeg_un
        unzip -q -o /tmp/ffmpeg.zip -d /tmp/ffmpeg_un
        cp /tmp/ffmpeg_un/ffmpeg ./ffmpeg
        chmod +x ./ffmpeg
    fi

    # Ollama 官方 darwin 包，只需要里面的 ollama 可执行文件
    if [ ! -f runtime/ollama/ollama ]; then
        echo "==> 下载 Ollama（官方 darwin 包，仅取其中的 ollama 可执行文件）"
        curl -L --fail -o /tmp/Ollama-darwin.zip https://ollama.com/download/Ollama-darwin.zip
        rm -rf /tmp/ollama_un && mkdir -p /tmp/ollama_un
        unzip -q -o /tmp/Ollama-darwin.zip -d /tmp/ollama_un
        OLLAMA_BIN="$(find /tmp/ollama_un -name ollama -type f | head -1)"
        if [ -z "$OLLAMA_BIN" ]; then
            echo "!! 解包后没找到 ollama 可执行文件"; exit 1
        fi
        cp "$OLLAMA_BIN" runtime/ollama/ollama
        chmod +x runtime/ollama/ollama
    fi
else
    echo "==> 跳过下载（--no-download）：请确认 ./ffmpeg 与 ./runtime/ollama/ollama 已就绪"
fi

test -x runtime/ollama/ollama || { echo "!! 缺少 runtime/ollama/ollama"; exit 1; }

# ---------------------------------------------------------------- 3. 打包
echo "==> PyInstaller 打包（--windowed → SeeName.app）"
rm -rf dist build
$PY -m PyInstaller --noconfirm --clean --windowed --name SeeName \
    --icon assets/logo.icns \
    --add-data "data/districts.csv:data" \
    --add-data "data/cities_world.csv:data" \
    --add-data "assets:assets" \
    --add-data "help_zh.md:." \
    --add-data "help_en.md:." \
    --add-binary "runtime/ollama/ollama:runtime/ollama" \
    main.py

APP="dist/SeeName.app"
test -d "$APP" || { echo "!! 打包失败，没有 $APP"; exit 1; }

# 自带运行时在 bundle 里必须可执行（PyInstaller 有时会丢执行位）
chmod +x "$APP/Contents/MacOS/runtime/ollama/ollama" 2>/dev/null || true

# Apple Silicon 要求可执行文件有签名；没有开发者账号就做 ad-hoc 签名，
# 能避免「已损坏，无法打开」的误报（仍会有「来自互联网」的提示）
echo "==> ad-hoc 签名（避免 Apple Silicon 上误报损坏）"
codesign --force --sign - "$APP" 2>/dev/null || echo "   （codesign 失败可忽略，仅影响首次打开体验）"

# ---------------------------------------------------------------- 4. 模型库
if [ -n "$MODELS" ]; then
    echo "==> 拷贝模型库: $MODELS -> dist/models"
    rm -rf dist/models
    mkdir -p dist/models
    cp -R "$MODELS"/. dist/models/
fi

# ---------------------------------------------------------------- 5. dmg
if [ "$MAKE_DMG" = "1" ]; then
    echo "==> 生成 dmg"
    rm -f dist/SeeName.dmg
    hdiutil create -volname "SeeName" -srcfolder dist -ov -format UDZO dist/SeeName.dmg
    ls -la dist/SeeName.dmg
fi

echo ""
echo "==> 完成：$(pwd)/$APP"
if [ -z "$MODELS" ]; then
    echo "    还没带模型库。任选其一："
    echo "      a)  ./build_mac.sh --models /path/to/models     # 拷一份进去（推荐，离线可用）"
    echo "      b)  首次启动后在软件里「模型管理…」下载 qwen3-vl:4b-instruct"
    echo "    模型库与 Windows 版通用（就是 ollama 的 models 目录，约 3.1GB）。"
fi
