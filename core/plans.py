"""Editable plans, rename ledgers, collision handling and rollback."""
from __future__ import annotations

import io
import json
import os
import re
from strings import T
from .paths import (
    DATA_DIR,
)
from .timestamps import (
    parse_manual_time,
)


def manual_times_path(directory: str, state_dir=None) -> str:
    sp = state_paths(directory, state_dir)
    return os.path.join(sp['dir'], 'manual_times_%s.json' % _tag_of(directory))


def load_manual_times(directory: str, state_dir=None) -> dict:
    raw = load_json(manual_times_path(directory, state_dir), {})
    out = {}
    for fn, s in (raw or {}).items():
        d = parse_manual_time(s) if isinstance(s, str) else None
        if d:
            out[fn] = d
    return out


def save_manual_times(directory: str, mapping: dict, state_dir=None):
    raw = {fn: (d.strftime('%Y%m%d_%H%M') if hasattr(d, 'strftime') else str(d))
           for fn, d in mapping.items()}
    save_json(manual_times_path(directory, state_dir), raw)


def _tag_of(d: str) -> str:
    t = re.sub(r'[^\w\u4e00-\u9fff]+', '_', os.path.basename(d.rstrip('\\/')))
    return t or 'root'


def state_paths(directory: str, state_dir: str | None = None):
    base = state_dir or DATA_DIR
    os.makedirs(base, exist_ok=True)
    tag = _tag_of(directory)
    return {
        'dir': base,
        'ledger': os.path.join(base, 'ledger_%s.json' % tag),
        'report': os.path.join(base, 'report_%s.txt' % tag),
        'cache': os.path.join(base, 'gps_city_cache.json'),
    }


def load_json(p, default):
    try:
        with io.open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def plan_path(directory: str, state_dir: str | None = None) -> str:
    sp = state_paths(directory, state_dir)
    return os.path.join(sp['dir'], 'plan_%s.json' % _tag_of(directory))


def save_plan(directory: str, items: list, state_dir: str | None = None,
              extra: dict | None = None) -> str:
    p = plan_path(directory, state_dir)
    obj = {'dir': os.path.abspath(directory), 'items': items}
    if extra:
        obj.update(extra)
    save_json(p, obj)
    return p


def load_plan(directory: str, state_dir: str | None = None) -> list:
    p = plan_path(directory, state_dir)
    obj = load_json(p, None)
    if isinstance(obj, dict):
        return obj.get('items') or []
    return obj or []


def item_of(r: dict) -> dict:
    """把 rename_one / rename_one_photo 的结果转成一个可编辑的计划项。"""
    stem = r.get('new_stem') or ''
    ts = r.get('ts', '')
    return {
        'old': r.get('old', ''), 'kind': r.get('kind', 'video'),
        'time': ts, 'place': r.get('place', ''),
        'desc': r.get('desc', ''),
        # 没有时间的项不参与改名（新名字直接留空），原文件保持不动
        'new': (stem + r.get('ext', '')) if (stem and ts) else '',
        'ok': bool(r.get('ok')), 'err': r.get('err', ''),
        'secs': round(float(r.get('t_model') or 0), 1),
        'gps': list(r['gps']) if r.get('gps') else None,
        'gps_src': r.get('gps_src', ''),
        'no_time': bool(r.get('no_time')),
        'applied': False,
        # —— 描述落盘用的三件套（写元数据 / .ai.txt / 目录说明.md 时要用）
        'addr': r.get('addr', ''), 'long': r.get('long', ''),
        'kw': r.get('kw', ''), 'when': r.get('when', ''),
        'place_src': r.get('place_src', ''),
        'place_exact': bool(r.get('place_exact')),
    }


def apply_plan(directory: str, items: list, state_dir: str | None = None,
               on_log=None) -> tuple:
    """按（可能已被用户改过的）计划表执行改名。返回 (成功, 失败, 跳过)。

    时间空白的项会被跳过（不改名），原文件保持不动。
    """
    say = on_log or (lambda s: None)
    sp = state_paths(directory, state_dir)
    led = load_json(sp['ledger'], {})
    done = failed = skipped = 0
    for it in items:
        if not it.get('ok', True):
            skipped += 1
            continue
        old, new = it.get('old') or '', it.get('new') or ''
        a, b = os.path.join(directory, old), os.path.join(directory, new)
        if not old or not str(it.get('time') or '').strip():
            # 时间留空 = 不改名（用户可以在表格里补上时间再来一次）
            it['new'] = ''
            skipped += 1
            continue
        if not new:
            skipped += 1
            continue
        if not os.path.exists(a):
            skipped += 1
            continue
        if os.path.abspath(a) == os.path.abspath(b):
            it['applied'] = True
            continue
        stem, ext = os.path.splitext(b)
        # 撞名时优先把时刻补回名字（比 _2/_3 有用得多）：
        #   视20250628资阳石羊_静坐观想_1020.mp4 ／ 图20240101成都肖家河_0830.jpg
        # 时刻是 0000 说明时间只剩日期（EXIF 被剥过），补上没意义，直接用序号。
        hhmm = re.sub(r'\D', '', str(it.get('time') or ''))[8:12]
        cands = []
        if hhmm and hhmm != '0000':
            cands.append('%s_%s%s' % (stem, hhmm, ext))
            cands += ['%s_%s_%d%s' % (stem, hhmm, k, ext) for k in range(2, 10)]
        cands += ['%s_%d%s' % (stem, k, ext) for k in range(2, 100)]
        target = b
        for c in cands:
            if not os.path.exists(target):
                break
            target = c
        if os.path.exists(target):
            it['err'] = T('目标文件名已存在')
            failed += 1
            say(T('  !! 改名失败 %s：目标名已存在') % old)
            continue
        try:
            os.rename(a, target)
            led[os.path.basename(target)] = old
            it['new'] = os.path.basename(target)
            it['applied'] = True
            done += 1
            # 摘要 txt 跟着文件名走（有的话一起改名，否则下次会多出一份孤儿）
            try:
                t0 = a + '.ai.txt'
                if os.path.exists(t0):
                    os.replace(t0, target + '.ai.txt')
            except Exception:
                pass
        except Exception as e:
            failed += 1
            it['err'] = T('改名失败：%r') % (e,)
            say(T('  !! 改名失败 %s：%r') % (old, e))
    save_json(sp['ledger'], led)
    # 把执行结果写回计划表，重开界面时状态一致
    save_plan(directory, items, state_dir)
    say(T('改名完成：成功 %d 个，跳过 %d 个，失败 %d 个') % (done, skipped, failed))
    return done, failed, skipped


def rollback(directory: str, on_log=None, state_dir=None) -> int:
    """按台账把名字改回去。"""
    sp = state_paths(directory, state_dir)
    led = load_json(sp['ledger'], {})
    n = 0
    for new, old in led.items():
        a, b = os.path.join(directory, new), os.path.join(directory, old)
        if os.path.exists(a) and not os.path.exists(b):
            try:
                os.rename(a, b)
                n += 1
                # 摘要 txt 跟着文件名走，一并改回去
                try:
                    t0 = a + '.ai.txt'
                    if os.path.exists(t0):
                        os.replace(t0, b + '.ai.txt')
                except Exception:
                    pass
            except Exception as e:
                if on_log:
                    on_log(T('  回滚失败 %s: %r') % (new, e))
    if on_log:
        on_log(T('回滚完成：%d 个文件已还原') % n)
    return n
