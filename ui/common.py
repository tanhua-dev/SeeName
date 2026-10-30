# -*- coding: utf-8 -*-
"""Application labels, resource paths and display helpers."""
from __future__ import annotations

import os
import subprocess
import sys

import engine
import strings
from strings import T


APP_TITLE = '显影 SeeName'
# 副标题固定双语展示，不随界面语言切换
APP_SUB = '看见，即命名。/ See it. Name it.'
# tanhua 只作为作者/开发者署名保留，不再指代产品
APP_AUTHOR = '作者：tanhua' if strings.lang() == 'zh' else 'By tanhua'
APP_VER = 'v1.0'


def asset_path(name: str) -> str:
    """界面资源（logo 等）路径，兼容 PyInstaller 打包模式（含 macOS .app）。"""
    for r in engine._bundle_roots():
        p = os.path.join(r, 'assets', name)
        if os.path.exists(p):
            return p
    return ''


def open_path(p: str):
    """在系统文件管理器里打开文件/文件夹：Windows startfile，macOS open，
    Linux xdg-open。都失败就安静返回（不算错误）。"""
    try:
        if os.name == 'nt':
            os.startfile(p)          # noqa: S606 - 仅本机用户主动点击时调用
        elif sys.platform == 'darwin':
            subprocess.run(['open', p], timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(['xdg-open', p], timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def human_size(n) -> str:
    n = float(n or 0)
    if n < 1024:
        return '%.0f B' % n
    for u in ('KB', 'MB', 'GB', 'TB'):
        n /= 1024.0
        if n < 1024 or u == 'TB':
            return '%.2f %s' % (n, u)
    return '%.2f TB' % n


def cut(s: str, n: int) -> str:
    s = s or ''
    return s if len(s) <= n else s[:n - 1] + '…'


def fmt_dur(sec: float) -> str:
    sec = int(max(0, sec))
    if sec < 60:
        return T('%d 秒') % sec
    m, s = divmod(sec, 60)
    if m < 60:
        return T('%d 分 %d 秒') % (m, s)
    h, m = divmod(m, 60)
    return T('%d 时 %d 分') % (h, m)
