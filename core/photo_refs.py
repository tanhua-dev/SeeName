"""Legacy adjacent-photo GPS helpers, retained for compatibility; batch processing does not use them."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from strings import T
from .defaults import (
    PHOTO_EXT,
)
from .media import (
    _photo_exif,
)
from .paths import (
    DATA_DIR,
)
from .plans import (
    load_json,
    save_json,
)
from .timestamps import (
    dt_from_name,
)


GPS_NEAR_H = 12.0       # ≤12h：同一天/跨夜半天内，借到「市+镇」
GPS_FAR_H = 72.0        # 12~72h：只借「市」，精确度降级换正确率；更远不借
_PHOTO_CACHE = None
_PHOTO_CACHE_FILE = 'photo_gps_cache.json'


def _photo_cache() -> dict:
    global _PHOTO_CACHE
    if _PHOTO_CACHE is None:
        _PHOTO_CACHE = load_json(os.path.join(DATA_DIR, _PHOTO_CACHE_FILE), {})
        if not isinstance(_PHOTO_CACHE, dict):
            _PHOTO_CACHE = {}
    return _PHOTO_CACHE


def _photo_cache_save():
    if _PHOTO_CACHE is not None:
        try:
            save_json(os.path.join(DATA_DIR, _PHOTO_CACHE_FILE), _PHOTO_CACHE)
        except Exception:
            pass


def default_photo_dirs(video_dir: str) -> list:
    """默认参考照片目录：视频目录本身 + 它的上一级（都只看一层，不递归）。

    上一级若是盘符根目录（如 D:\\）则跳过，避免去扫整个盘的根。
    """
    out = []
    d = os.path.abspath(video_dir)
    if os.path.isdir(d):
        out.append(d)
    up = os.path.dirname(d)
    if up and up != d and os.path.isdir(up) and not _is_drive_root(up):
        out.append(up)
    return out


def _is_drive_root(path: str) -> bool:
    p = os.path.abspath(path)
    return os.path.dirname(p) == p or (len(p) <= 3 and p[1:2] == ':')


def scan_photo_refs(video_dir: str, files: list, dirs=None, extra_days: float = 3.0,
                    on_log=None) -> list:
    """扫描参考照片，返回 [(拍摄时间, (lat,lon), 文件名)]，只含带 GPS 的。

    只为「时间上跟视频挨得着」的照片读 EXIF；结果缓存到 data 目录，二次运行很快。
    """
    say = on_log or (lambda s: None)
    ds = dirs if dirs else default_photo_dirs(video_dir)
    tms = []
    for f in files:
        d = dt_from_name(f)
        if d:
            tms.append(d)
    if not tms:
        return []
    lo = min(tms) - timedelta(days=extra_days)
    hi = max(tms) + timedelta(days=extra_days)

    cands = []
    for d in ds:
        try:
            names = os.listdir(d)
        except Exception:
            continue
        for fn in names:
            if os.path.splitext(fn)[1].lower() not in PHOTO_EXT:
                continue
            p = os.path.join(d, fn)
            nd = dt_from_name(fn)
            if nd and not (lo <= nd <= hi):
                continue                      # 先按文件名日期砍掉绝大部分
            cands.append(p)

    cache = _photo_cache()
    refs, hit, miss = [], 0, 0
    for p in cands:
        key = os.path.basename(p)
        v = cache.get(key)
        if v is None:
            dt, gps = _photo_exif(p)
            v = [dt.strftime('%Y-%m-%d %H:%M:%S') if dt else None,
                 gps[0] if gps else None, gps[1] if gps else None]
            cache[key] = v
            miss += 1
        else:
            hit += 1
        if v[1] is None or v[2] is None:
            continue
        dt = None
        if v[0]:
            try:
                dt = datetime.strptime(v[0], '%Y-%m-%d %H:%M:%S')
            except Exception:
                dt = None
        if dt is None:
            dt = dt_from_name(os.path.basename(p))
        if dt is None:
            continue
        refs.append((dt, (v[1], v[2]), os.path.basename(p)))
    if miss:
        _photo_cache_save()
    say(T('=== 参考照片：扫描 %d 张（缓存命中 %d），其中 %d 张带 GPS')
        % (len(cands), hit, len(refs)))
    return refs


def fill_missing_gps(meta: dict, refs=None, near_h: float = GPS_NEAR_H,
                     far_h: float = GPS_FAR_H) -> tuple:
    """给没有 GPS 的视频按时间就近借用位置（视频或照片），就地修改 meta。

    分两档，避免"拿几天前的位置硬猜"：
      ≤ near_h(12h)          → gps_level='near'，地名可标到乡镇
      near_h ~ far_h(72h)    → gps_level='far' ，地名只标到城市
      更远                    → 不借，留空
    返回 (near 条数, far 条数)。
    """
    pool = [(v['dt'], v['gps'], T('视频')) for v in meta.values()
            if v.get('gps') and v.get('dt')]
    for dt, g, name in (refs or []):
        pool.append((dt, g, T('照片')))
    if not pool:
        return (0, 0)
    for fn, v in meta.items():
        if v.get('gps') or not v.get('dt'):
            continue
        best = min(pool, key=lambda k: abs((k[0] - v['dt']).total_seconds()))
        gap_h = abs((best[0] - v['dt']).total_seconds()) / 3600.0
        if gap_h <= near_h:
            v['gps'], v['gps_level'] = best[1], 'near'
            v['gps_src'] = T('相邻%.1fh·%s') % (gap_h, best[2])
        elif gap_h <= far_h:
            v['gps'], v['gps_level'] = best[1], 'far'
            v['gps_src'] = T('邻近%.0fh·%s') % (gap_h, best[2])
    near = sum(1 for v in meta.values() if v.get('gps_level') == 'near')
    far = sum(1 for v in meta.values() if v.get('gps_level') == 'far')
    return (near, far)
