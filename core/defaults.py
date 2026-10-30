"""Shared defaults; importing this module performs no I/O."""
from __future__ import annotations

import re


MODEL = 'Qwen3VL-4B-Instruct-Q4_K_M'
DEFAULT_PORT = 11500        # 自带运行时的私有端口（不抢占系统 Ollama 的 11434）
CTX = 2048
KEEP_ALIVE = '15m'
PIX_BUDGET = 950_000        # 整张宫格的目标像素预算
GUTTER = 6                  # 宫格分隔白边
CAND_WIDTH = 512            # 抽候选帧的宽度
VID_EXT = ('.mp4', '.mov', '.mkv', '.avi', '.m4v', '.3gp', '.wmv')
# Recognize both the old timestamp prefix and the current 视/图 date prefix.
ALREADY_NAMED = re.compile(r'^(\d{8}_\d{4}(_|\.)|[视图]\d{8})')
# The open-source build carries no private directory presets.
PRESETS = {}
PHOTO_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.dng', '.tif', '.tiff')
