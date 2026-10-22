"""Capture-time parsing from metadata, filenames and manual input."""
from __future__ import annotations

import os
import re
from datetime import datetime


def parse_dt(creation: str):
    """解析媒体的拍摄时间标签，返回本地时间 datetime 或 None。"""
    if not creation:
        return None
    try:
        dt = datetime.fromisoformat(creation.replace('Z', '+00:00'))
        if dt.tzinfo:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        return None
_DT_PATS = (
    re.compile(r'(20\d{2})(\d{2})(\d{2})[_\-\s]?(\d{2})(\d{2})(\d{2})'),
    re.compile(r'(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})[-_. T]+(\d{1,2})[-_.:]?(\d{2})(\d{2})'),
    re.compile(r'(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})[-_. T]+(\d{2})(\d{2})'),
    re.compile(r'(20\d{2})(\d{2})(\d{2})'),
    re.compile(r'(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})'),
)


def dt_from_name(fn: str):
    """从文件名解析拍摄时间（按能拿到的精度）；解析不到返回 None。"""
    s = os.path.basename(fn)
    for pat in _DT_PATS:
        m = pat.search(s)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        try:
            if len(g) >= 6:
                return datetime(g[0], g[1], g[2], g[3], g[4], min(g[5], 59))
            if len(g) == 5:
                return datetime(g[0], g[1], g[2], g[3], min(g[4], 59))
            return datetime(g[0], g[1], g[2])
        except Exception:
            continue
    # 本软件的新格式只写到「天」：视20241220成都肖家河—剥洋葱
    m = re.match(r'^[视图](20\d{2})(\d{2})(\d{2})', s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            pass
    return None
_MAN_T_FMTS = (
    '%Y%m%d_%H%M', '%Y%m%d%H%M%S', '%Y%m%d%H%M', '%Y%m%d',
    '%Y-%m-%d_%H%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
    '%Y/%m/%d %H:%M:%S', '%Y/%m/%d %H:%M', '%Y/%m/%d',
)


def parse_manual_time(s: str):
    """解析用户手工输入的时间；支持 20251011_1430 / 202510111430 / 20251011 / 2025-10-11 14:30 等。"""
    t = (s or '').strip().replace('：', ':')
    if not t:
        return None
    if t.isdigit():
        # 纯数字按时长判定，避免 14 位格式"吞掉"12 位输入
        f = {8: '%Y%m%d', 12: '%Y%m%d%H%M', 14: '%Y%m%d%H%M%S'}.get(len(t))
        if not f:
            return None
        try:
            return datetime.strptime(t, f)
        except Exception:
            return None
    for f in _MAN_T_FMTS:
        try:
            return datetime.strptime(t, f)
        except Exception:
            continue
    return dt_from_name(t)
