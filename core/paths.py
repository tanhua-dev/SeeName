"""Application and bundled-resource locations for source and frozen builds."""
from __future__ import annotations

import os
import sys


IS_MAC = (sys.platform == 'darwin')
IS_WIN = (sys.platform == 'win32' or os.name == 'nt')


def _app_dir() -> str:
    """软件根目录（兼容 PyInstaller 打包后的单文件模式）。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _user_data_dir() -> str:
    """可写的用户数据目录（配置 / 台账 / 报告 / 缓存 / 日志都写这里）。

    Windows 下沿用便携模式：全部放软件目录旁边，整个文件夹拷走就能用。
    macOS 下 .app  bundle 里写不进去（也不该写——放在 /Applications 里会因
    权限/签名失败，且升级即丢），所以改成系统的
    ~/Library/Application Support/SeeName。
    在线下载的模型另存到各平台的用户数据目录；不写进程序目录。
    """
    if IS_MAC and getattr(sys, 'frozen', False):
        p = os.path.expanduser('~/Library/Application Support/SeeName')
    else:
        p = APP_DIR
    try:
        os.makedirs(p, exist_ok=True)
    except Exception:
        pass
    return p


APP_NAME = '显影 SeeName'
APP_DIR = _app_dir()
USER_DATA = _user_data_dir()
DATA_DIR = os.path.join(USER_DATA, 'data')
RUNTIME_DIR = os.path.join(APP_DIR, 'runtime', 'ollama')
CONFIG_PATH = os.path.join(USER_DATA, 'config.json')


def _bundle_roots() -> list:
    """PyInstaller 运行时可能的数据根目录。

    Windows 单文件：都在 sys._MEIPASS。
    macOS .app bundle：数据被 BUNDLE 放进 Contents/Resources，而 _MEIPASS
    指向 Contents/MacOS，所以要把 Resources 也列进来。
    """
    roots = []
    mp = getattr(sys, '_MEIPASS', '')
    if mp:
        roots.append(mp)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots.append(here)
    if getattr(sys, 'frozen', False):
        exe = os.path.dirname(os.path.abspath(sys.executable))
        roots.append(exe)
        roots.append(os.path.normpath(os.path.join(exe, '..', 'Resources')))
    return roots
