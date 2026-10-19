"""Single-file recognition and batch coordination with an explicitly supplied model backend."""
from __future__ import annotations

import geo
import io
import meta as metadb
import os
import time
import traceback
from collections.abc import Callable
from inference import InferenceBackend
from strings import T
from .defaults import (
    ALREADY_NAMED,
    CTX,
    MODEL,
    PIX_BUDGET,
)
from .media import (
    FF,
    _photo_exif,
    _photo_for_model,
    compose,
    list_photos,
    list_videos,
    photo_meta,
    pick_frames,
    probe,
    scan_meta,
)
from .naming import (
    PHOTO_PROMPT,
    PHOTO_PROMPT_FULL,
    PROMPT,
    PROMPT_FULL,
    compose_name,
    fill_long,
    parse_full,
    parse_out,
    time_cn,
)
from .places import (
    place_of,
)
from .plans import (
    apply_plan,
    item_of,
    load_manual_times,
    save_plan,
    state_paths,
)
from .timestamps import (
    dt_from_name,
    parse_dt,
)


def _ask_model(backend: InferenceBackend, img: bytes, model=MODEL, timeout=300, prompt=None,
               max_tokens: int = 64):
    """送一张图给模型，返回 (文本, 耗时, prompt token 数, 服务端统计)。"""
    result = backend.describe(img, model=model, prompt=prompt or PROMPT,
                              timeout=timeout, max_tokens=max_tokens)
    return result.text, result.elapsed_seconds, result.prompt_tokens, result.details


class Config:
    def __init__(self, directory, apply=False, frames=0, limit=0, all_files=False,
                 model=MODEL, manual_times=None, videos=True, photos=False,
                 describe_photos=False, photo_dirs=None, use_photos=False,
                 net_geo=False, amap_key='', geo_level='district',
                 write_meta=True, write_txt=True, write_md=True,
                 ai_desc=True):
        self.directory = directory
        self.apply = apply              # False = 只出建议（不动文件名）
        self.frames = frames            # 0 = 按视频时长自适应
        self.limit = limit              # 0 = 不限制
        self.all_files = all_files      # 是否连已命名的文件也重做
        self.model = model
        self.manual_times = manual_times or {}   # {文件名: datetime} 手工指定时间
        self.videos = videos            # 是否处理视频
        self.photos = photos            # 是否连目录里的照片一起处理
        self.describe_photos = describe_photos   # 照片是否也调模型写描述（慢）
        # —— AI 描述总开关：False 时视频不抽帧、照片不送模型，只按「时间+地名」命名
        #     （慢机器的纯离线快路径：连推理服务都不用起）
        self.ai_desc = bool(ai_desc)
        # —— 联网精确定位（默认关闭；关着就是纯离线，一个网络包都不发）
        self.net_geo = bool(net_geo)
        self.amap_key = amap_key or ''
        self.geo_level = 'street' if geo_level == 'street' else 'district'
        # —— 描述落盘（默认全开：元数据 / .ai.txt / 目录说明.md）
        self.write_meta = bool(write_meta)
        self.write_txt = bool(write_txt)
        self.write_md = bool(write_md)
        # 以下两个参数已停用（相邻借鉴功能已下线），保留仅为兼容旧调用
        self.photo_dirs = photo_dirs
        self.use_photos = use_photos

    @property
    def want_long(self) -> bool:
        """要不要多花几十秒让模型写长描述：三个落盘开关全关就不用。"""
        return bool(self.write_meta or self.write_txt or self.write_md)


def rename_one(path: str, model=MODEL, frames=0, meta=None,
               net_geo: bool = False, amap_key: str = '',
               geo_level: str = 'district', want_long: bool = False,
               ai_desc: bool = True, *, backend: InferenceBackend) -> dict:
    """处理单个视频，返回结果 dict（不落盘改名）。

    meta: scan_meta() 的结果（可选）。有则复用其中的拍摄时间与 GPS
          （含「无 GPS 时按时间就近借用」的结果），避免重复探测。
    net_geo / amap_key / geo_level：联网精确定位（见 place_of）。
    want_long：要不要顺带让模型写 100~200 字的长描述（写元数据/txt 时才需要）。
    ai_desc：AI 描述总开关。False = 不抽帧、不调模型，只按「时间 + 地名」命名
             （纯离线快路径，t_model=0，desc/long/kw 全空）。
    """
    fn = os.path.basename(path)
    m = (meta or {}).get(fn) or {}
    if 'dur' in m:
        # 预扫描已经 probe 过一次，别再来一遍 ffmpeg（每个视频省约 0.2s）
        info = {'dur': m.get('dur', 0.0), 'creation': '', 'gps': m.get('gps')}
    else:
        info = probe(path)
    dt = m.get('dt') or parse_dt(info.get('creation')) or dt_from_name(fn)
    if dt is not None and dt.year <= 1970:
        dt = None
    # 读不到时间的**不再跳过**：照样识别，时间留空，交给用户在处理完后的表格里填
    ts = dt.strftime('%Y%m%d_%H%M') if dt else ''
    no_time = dt is None

    dur = info['dur']
    if frames:
        need = frames
    elif dur < 3:
        need = 2
    elif dur <= 20:
        need = 4
    else:
        need = 6

    gps = m.get('gps') or info.get('gps')
    gsrc = m.get('gps_src') or ('GPS' if info.get('gps') else '')
    lvl = m.get('gps_level') or ('gps' if info.get('gps') else '')
    # 地名先算出来：要长描述时得把地点一起喂给模型（否则它会瞎编）
    place, addr, pkm, psrc, pexact = place_of(gps, net_geo, amap_key, geo_level)
    when = time_cn(dt) if dt else ''

    # —— AI 描述总开关：关掉就完全不碰模型（不抽帧、不拼宫格、不问模型），
    #    只按「时间 + 地名」命名。desc/long/kw 留空，t_model=0。
    desc, long, kw, content = '', '', '', ''
    t_m, ptok = 0.0, 0
    imgs, how, t_ex = [], '', 0.0
    grid, gsize = None, (0, 0)
    if ai_desc:
        imgs, how, t_ex = pick_frames(path, dur, need)
        if not imgs:
            return {'ok': False, 'old': fn, 'err': T('抽帧失败'), 'dur': dur}
        grid, gsize = compose(imgs)
        if want_long:
            content, t_m, ptok, _ = _ask_model(backend,
                grid, model=model, max_tokens=520,
                prompt=PROMPT_FULL.format(t=when or T('时间不明'),
                                          p=addr or place or T('地点不明')))
            desc, long, kw = parse_full(content)
            long = fill_long(long, desc, when, addr or place)
        else:
            content, t_m, ptok, _ = _ask_model(backend, grid, model=model)
            desc = parse_out(content)

    new_stem = compose_name(ts, place, desc, fn, place_exact=pexact)

    return {
        'ok': True, 'old': fn, 'kind': 'video', 'new_stem': new_stem,
        'ext': os.path.splitext(fn)[1],
        'place': place, 'desc': desc or '', 'km': pkm,
        'addr': addr, 'place_src': psrc, 'place_exact': pexact,
        'long': long, 'kw': kw, 'when': when,
        'gps': gps, 'gps_src': gsrc, 'gps_level': lvl, 'no_time': no_time,
        'ts': ts, 'dur': dur, 'frames': len(imgs), 'how': how,
        't_ex': t_ex, 't_model': t_m, 'tokens': ptok, 'grid': gsize,
        'raw': content, 'thumb': grid,
    }


def rename_one_photo(path: str, model=MODEL, describe=False, meta=None,
                     net_geo: bool = False, amap_key: str = '',
                     geo_level: str = 'district',
                     want_long: bool = False, *, backend: InferenceBackend) -> dict:
    """处理单张照片，返回与 rename_one 同构的结果 dict（不落盘改名）。"""
    fn = os.path.basename(path)
    m = meta or photo_meta(path)
    dt = m.get('dt')
    ts = dt.strftime('%Y%m%d_%H%M') if dt else ''
    gps = m.get('gps')
    place, addr, pkm, psrc, pexact = place_of(gps, net_geo, amap_key, geo_level)
    when = time_cn(dt) if dt else ''
    desc, raw, t_m, ptok = '', '', 0.0, 0
    long, kw = '', ''
    if describe:
        try:
            raw_img = _photo_for_model(path)
            if want_long:
                content, t_m, ptok, _ = _ask_model(backend,
                    raw_img, model=model, max_tokens=520,
                    prompt=PHOTO_PROMPT_FULL.format(
                        t=when or T('时间不明'), p=addr or place or T('地点不明')))
                desc, long, kw = parse_full(content)
                long = fill_long(long, desc, when, addr or place)
            else:
                content, t_m, ptok, _ = _ask_model(backend, raw_img, model=model,
                                                  prompt=PHOTO_PROMPT)
                desc = parse_out(content)
            raw = content
        except Exception as e:
            raw = T('（照片识别失败：%r）') % (e,)
    new_stem = compose_name(ts, place, desc, fn, place_exact=pexact)
    return {
        'ok': True, 'old': fn, 'kind': 'photo', 'new_stem': new_stem,
        'ext': os.path.splitext(fn)[1], 'place': place, 'desc': desc,
        'km': pkm, 'gps': gps, 'gps_src': m.get('gps_src', ''),
        'addr': addr, 'place_src': psrc, 'place_exact': pexact,
        'long': long, 'kw': kw, 'when': when,
        'gps_level': 'gps' if gps else '', 'no_time': not dt,
        'ts': ts, 'dur': 0.0, 'frames': 1 if describe else 0,
        'how': T('照片') if not describe else T('照片+模型'),
        't_ex': 0.0, 't_model': t_m, 'tokens': ptok,
        'grid': (0, 0), 'raw': raw, 'thumb': None,
    }


def finalize(directory: str, items: list, on_log=None,
             write_meta=True, write_txt=True, write_md=True) -> dict:
    """识别（以及改名）都结束之后：把描述写回文件本身，并落盘摘要。

    items 里的项已经带上了 long（长描述）/ addr（完整地址）/ kw（关键词），
    这些都是识别阶段一次模型调用顺手拿到的，这里不再调用模型。
    改名过的用新文件名，没改名（时间空白之类）的用原文件名。
    """
    say = on_log or (lambda s: None)
    st = {'meta': 0, 'meta_fail': 0, 'txt': 0, 'md': 0, 'skip': 0}
    if not (write_meta or write_txt or write_md):
        return st
    ff = ''
    if write_meta:
        try:
            ff = FF()
        except Exception:
            ff = ''
    rows = []
    for it in items:
        if not it.get('ok', True):
            st['skip'] += 1
            continue
        name = (it.get('new') if it.get('applied') else '') or it.get('old') or ''
        if not name:
            st['skip'] += 1
            continue
        p = os.path.join(directory, name)
        if not os.path.exists(p):
            st['skip'] += 1
            continue
        place = str(it.get('place') or '').strip()
        addr = str(it.get('addr') or place).strip()
        desc = str(it.get('desc') or '').strip()
        long = str(it.get('long') or '').strip()
        kw = str(it.get('kw') or '').strip()
        if not long:
            long = fill_long('', desc, str(it.get('when') or ''), addr)
        title = metadb.make_title(place or addr, desc)
        kw = metadb.make_keywords(place, desc, kw)
        if write_meta:
            ok, msg = metadb.write_meta(p, title, long, kw, ffmpeg=ff)
            if ok:
                st['meta'] += 1
            else:
                st['meta_fail'] += 1
                say(T('  · 元数据未写入 %s：%s') % (name, msg))
        if write_txt:
            ok, msg = metadb.write_sidecar(p, addr, long)
            if ok:
                st['txt'] += 1
            else:
                say(T('  · 摘要未写入 %s：%s') % (name, msg))
        rows.append((name, addr, long))
    if write_md and rows:
        ok, msg = metadb.write_dir_md(directory, rows)
        st['md'] = len(rows) if ok else 0
        if not ok:
            say(T('  · 目录说明.md 未写入：%s') % msg)
    return st


def run_batch(cfg: Config, on_log=None, on_item=None, on_progress=None,
              should_stop=None, on_phase=None, state_dir=None, *,
              backend: InferenceBackend, runtime_status: Callable[[], dict]):
    """识别一批视频/照片，产出「计划表」。

    cfg.apply=False（默认）只写计划表、**不动文件名**；True 则识别完立即按表改名。
    读不到拍摄时间的文件不再跳过，而是时间留空、照常识别，留给用户之后手填。

    回调：
      on_log(str)                  一行日志
      on_item(dict)                每处理完一个（含 thumb 宫格图）
      on_progress(i, total, stats) 进度
      should_stop() -> bool        返回 True 则尽快停止
      on_phase(str)                阶段提示（准备/热身/处理/收尾）
    """
    say = on_log or (lambda s: None)
    phase = on_phase or (lambda s: None)
    stop = should_stop or (lambda: False)
    d = os.path.abspath(cfg.directory)
    if not os.path.isdir(d):
        raise RuntimeError(T('目录不存在：%s') % d)

    sp = state_paths(d, state_dir)
    os.makedirs(sp['dir'], exist_ok=True)
    logf = io.open(os.path.join(sp['dir'], 'run.log'), 'w', encoding='utf-8', buffering=1)
    rep = io.open(sp['report'], 'w', encoding='utf-8', buffering=1)

    def log(s):
        logf.write(s + '\n')
        say(s)

    phase(T('准备中'))
    log(T('=== 目录: %s') % d)
    log(T('=== 模型: %s   ctx=%d   宫格像素预算 %d') % (cfg.model, CTX, PIX_BUDGET))
    geo.reset()     # 新一轮：清掉在线定位的熔断计数
    if getattr(cfg, 'net_geo', False):
        log(T('=== 联网精确定位：开（%s）｜ 地址粒度：%s')
            % ((T('已填高德 Key') if (cfg.amap_key or '').strip()
                else T('未填高德 Key，国内坐标走离线库')),
               T('到街道') if cfg.geo_level == 'street' else T('到区县')))
        log(T('    只发送经纬度给地图服务商，不发送文件本身；无网或失败自动回落离线库'))
    else:
        log(T('=== 联网精确定位：关（纯离线，全程不发网络请求）'))
    rs = runtime_status()
    log(T('=== 推理运行时：%s（%s）') % (rs['exe'] or T('未找到'), rs['label']))
    log(T('=== 模型库：%s') % rs['models_dir'])

    # AI 描述总开关关着时全程不碰模型：连推理服务和模型热身都跳过（纯离线快路径）
    if getattr(cfg, 'ai_desc', True):
        if not backend.start(on_log=log):
            log(T('!! 推理服务不可用（自带运行时启动失败），任务终止'))
            rep.close(); logf.close()
            return {'total': 0, 'done': 0, 'failed': 0, 'stopped': True, 'elapsed': 0.0,
                    'plan': [], 'plan_path': ''}

        phase(T('模型热身'))
        ok, err = backend.load(cfg.model)
        log(T('=== 模型热身%s') % (T('完成') if ok else T('失败（继续）：%s') % err))
    else:
        log(T('=== AI 描述：关（纯离线，不调用模型），跳过推理服务与模型热身'))

    # ---- 收集目标：先视频，再（可选）照片
    vids = list_videos(d) if cfg.videos else []
    if not cfg.all_files:
        vids = [f for f in vids if not ALREADY_NAMED.match(f)]
    shots = []
    if cfg.photos:
        shots = [f for f in list_photos(d) if cfg.all_files or not ALREADY_NAMED.match(f)]
    targets = [(f, 'video') for f in vids] + [(f, 'photo') for f in shots]
    if cfg.limit:
        targets = targets[:cfg.limit]
    total = len(targets)
    log(T('=== 待处理 %d 个（视频 %d ／ 照片 %d）') % (total, len(vids), len(shots)))
    # 「照片也写内容描述」受总开关节制：AI 描述关着时一律走不调模型的快路径
    desc_photos = bool(getattr(cfg, 'ai_desc', True)) and bool(cfg.describe_photos)
    if cfg.photos and shots:
        log(T('=== 照片只读 EXIF 时间与 GPS，不调模型，秒级完成')
            + (T('；已开启「照片也写描述」，每张约 11 秒') if desc_photos else ''))

    # ---- 预扫描视频的拍摄时间与 GPS（照片不进这里）
    manual = dict(load_manual_times(d, state_dir))
    manual.update(cfg.manual_times or {})
    meta = scan_meta(d, vids, manual) if vids else {}
    no_time = [f for f in vids if not meta[f].get('dt')]
    have = sum(1 for v in meta.values() if v.get('gps'))
    p_gps = 0
    if shots:
        for f in shots:
            try:
                if _photo_exif(os.path.join(d, f))[1]:
                    p_gps += 1
            except Exception:
                pass
    log(T('=== 预扫描：视频自带 GPS %d 个 ｜ 照片带 GPS %d/%d 张')
        % (have, p_gps, len(shots)))
    log(T('=== 没有 GPS 的一律留空（相邻借鉴已下线），读不到时间的也留空，之后在表格里手填'))
    if no_time:
        log(T('    读不到时间：%s') % '、'.join(f[:30] for f in no_time[:6]))
    log('')

    stats = {'total': total, 'done': 0, 'failed': 0, 'stopped': False,
             'elapsed': 0.0, 'avg': 0.0, 'applied': 0, 'skipped': 0}
    items = []
    t_all = time.time()
    stop_flag = False

    for i, (fn, kind) in enumerate(targets, 1):
        if stop():
            stop_flag = True
            log(T('=== 收到停止请求，已在第 %d 个停下') % (i - 1))
            break
        p = os.path.join(d, fn)
        try:
            if kind == 'photo':
                r = rename_one_photo(p, model=cfg.model, describe=desc_photos,
                                     net_geo=cfg.net_geo, amap_key=cfg.amap_key,
                                     geo_level=cfg.geo_level,
                                     want_long=cfg.want_long and desc_photos, backend=backend)
            else:
                r = rename_one(p, model=cfg.model, frames=cfg.frames, meta=meta,
                               net_geo=cfg.net_geo, amap_key=cfg.amap_key,
                               geo_level=cfg.geo_level, want_long=cfg.want_long,
                               ai_desc=bool(getattr(cfg, 'ai_desc', True)), backend=backend)
            r['index'] = i
            r['total'] = total
            it = item_of(r)
            it['index'] = i
            items.append(it)

            if r['ok']:
                stats['done'] += 1
                line = ('[%d/%d] %s ｜ %s ｜ %s ｜ %s'
                        % (i, total, fn[:32], it['time'] or T('时间空白'),
                           it['place'] or T('地名留空'), it['desc'] or T('(无描述)')))
                log(line)
                rep.write(line + '\n')
                rep.write('       -> %s\n' % (it['new'] or T('（时间空白，先不改名）')))
                if r.get('raw'):
                    rep.write(T('       原始输出: %s\n') % r['raw'].replace('\n', ' | ')[:140])
            else:
                stats['failed'] += 1
                log(T('[%d/%d] %s 失败：%s') % (i, total, fn[:32], r.get('err', '')))
                rep.write('[%d/%d] %s !! %s\n' % (i, total, fn, r.get('err', '')))
            if on_item:
                on_item(r)
        except Exception as e:
            err_txt = traceback.format_exc(limit=3)
            log(T('[%d/%d] %s 异常：%r') % (i, total, fn[:32], e))
            rep.write(T('[%d/%d] %s !! 异常 %r\n') % (i, total, fn, e))
            logf.write(err_txt + '\n')
            stats['failed'] += 1
            items.append({'old': fn, 'kind': kind, 'ok': False, 'err': repr(e),
                          'time': '', 'place': '', 'desc': '', 'new': '',
                          'index': i, 'applied': False})
        if on_progress:
            el0 = time.time() - t_all
            on_progress(i, total, {'done': stats['done'], 'failed': stats['failed'],
                                   'elapsed': el0, 'avg': el0 / max(1, i)})

    el = time.time() - t_all
    stats['elapsed'] = el
    stats['avg'] = el / max(1, stats['done'] + stats['failed'])
    stats['stopped'] = stop_flag
    stats['plan'] = items
    stats['plan_path'] = save_plan(d, items, state_dir,
                                   {'model': cfg.model, 'photos': cfg.photos,
                                    'describe_photos': cfg.describe_photos})
    log(T('=== 识别完成：成功 %d ／ 失败 %d ／ 用时 %.1fs')
        % (stats['done'], stats['failed'], el))
    rep.write(T('\n=== 识别完成 %d 个，失败 %d 个，用时 %.1fs\n')
              % (stats['done'], stats['failed'], el))

    if cfg.apply:
        phase(T('改名中'))
        done, failed, skipped = apply_plan(d, items, state_dir, on_log=log)
        stats['applied'], stats['skipped'] = done, skipped
        rep.write(T('\n=== 改名 %d 个，跳过 %d 个（时间空白），失败 %d 个\n')
                  % (done, skipped, failed))
        rep.write(T('\n=== 改名对照（可据此回滚）===\n'))
        for it in items:
            if it.get('applied') and it.get('new') != it.get('old'):
                rep.write('  %s\n    -> %s\n' % (it['old'], it['new']))
    else:
        log(T('=== 文件名尚未改动。可在结果表里核对/修改「时间、地点、描述」，' + '确认后点「应用改名」。'))

    # ---- 描述落盘：元数据（EXIF/视频标签）+ .ai.txt + 目录说明.md
    if cfg.want_long or cfg.write_meta or cfg.write_txt or cfg.write_md:
        phase(T('写入描述'))
        st = finalize(d, items, on_log=log, write_meta=cfg.write_meta,
                      write_txt=cfg.write_txt, write_md=cfg.write_md)
        stats['meta'] = st
        if cfg.write_meta:
            log(T('=== 元数据写入：成功 %d 个，失败 %d 个（原文件不动）')
                % (st['meta'], st['meta_fail']))
        if cfg.write_txt:
            log(T('=== 摘要 .ai.txt：%d 个') % st['txt'])
        if cfg.write_md and st['md']:
            log(T('=== 目录说明.md：汇总 %d 个文件') % st['md'])
    else:
        log(T('=== 三个落盘开关都关着：不写元数据、不写 .ai.txt、不写目录说明.md'))
    rep.close()
    logf.close()
    phase(T('完成'))
    return stats
