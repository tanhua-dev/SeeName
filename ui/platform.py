# -*- coding: utf-8 -*-
"""Platform setup and PyInstaller temporary-directory cleanup."""
from __future__ import annotations

import os
import sys
import time


def clean_stale_mei(max_age_min: float = 15.0) -> tuple[int, int]:
    """清扫 PyInstaller onefile 遗留的 _MEI* 解压目录。

    单文件 exe 每次启动会把解释器解到 %TEMP%\\_MEI随机号。v3.16 起退出时
    用 os._exit 绕过引导器的删目录代码（那会在 DLL 被占用时弹
    「Failed to remove temporary directory」警告框），所以每次运行都会
    留下解压目录，靠本函数在下次启动时兜底清扫（删掉能删的部分）。

    只删同时满足的：① 名字以 _MEI 开头 ② 是目录
                    ③ 最后修改时间在 max_age_min 分钟之前（避免误删
                      正在运行的其他实例）
                    ④ 不是本次运行正在用的目录（sys._MEIPASS）
    删不掉的（仍被占用）直接跳过，不影响主流程。
    返回 (已删除, 删不掉)。
    """
    import shutil
    import tempfile

    tmp = tempfile.gettempdir()
    cur = os.path.abspath(getattr(sys, '_MEIPASS', '') or '')
    now = time.time()
    gone = kept = 0
    try:
        names = [n for n in os.listdir(tmp) if n.upper().startswith('_MEI')]
    except OSError:
        return (0, 0)
    for n in names:
        p = os.path.join(tmp, n)
        try:
            if not os.path.isdir(p) or os.path.abspath(p) == cur:
                continue
            if now - os.path.getmtime(p) < max_age_min * 60.0:
                continue
            shutil.rmtree(p, ignore_errors=True)
            if os.path.exists(p):
                kept += 1
            else:
                gone += 1
        except OSError:
            kept += 1
    return (gone, kept)


def enable_dpi():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
