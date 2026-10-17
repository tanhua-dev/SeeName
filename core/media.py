"""Media discovery, metadata reading and image preparation using FFmpeg/Pillow.

Keep the existing Ollama tuning until measurements justify changing it:
the previous qwen3-vl setup used a roughly 1M-pixel grid and num_ctx=2048
(about 1180 visual tokens). Raising context to 8192 caused GPU spillover
in those benchmarks. Extract keyframes first, falling back to seek extraction.
These are measurements of the previous setup, not assumptions for every backend.
"""
from __future__ import annotations

import io
import math
import os
import re
import shutil
import subprocess
import time
from datetime import datetime

from PIL import Image, ImageFilter, ImageOps, ImageStat

from strings import T

from .defaults import (
    ALREADY_NAMED,
    CAND_WIDTH,
    GUTTER,
    PHOTO_EXT,
    PIX_BUDGET,
    VID_EXT,
)
from .paths import (
    APP_DIR,
    IS_WIN,
    RUNTIME_DIR,
)
from .timestamps import (
    dt_from_name,
    parse_dt,
)


_DUR = re.compile(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)')
_CT = re.compile(r'creation_time\s*:\s*([0-9T:\-\.Z\+ ]+)', re.I)
_LOC = re.compile(r'^\s*location(?:-eng)?\s*:\s*([+\-][0-9.]+[+\-][0-9.]+)', re.M)
JPG_SOI, JPG_EOI = b'\xff\xd8\xff', b'\xff\xd9'
CRE = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def find_ffmpeg() -> str:
    """按优先级定位 ffmpeg：软件目录（含自带的）→ 系统 PATH。"""
    exe = 'ffmpeg.exe' if IS_WIN else 'ffmpeg'
    for c in (os.path.join(APP_DIR, exe),
              os.path.join(APP_DIR, 'bin', exe),
              os.path.join(RUNTIME_DIR, exe),
              os.path.join(APP_DIR, 'runtime', 'ffmpeg', exe)):
        if os.path.isfile(c):
            return c
    w = shutil.which(exe) or shutil.which('ffmpeg')
    if w:
        return w
    raise RuntimeError(T('找不到 ffmpeg：软件目录里没有，系统 PATH 里也没有。'
                         'Mac 版可执行 brew install ffmpeg。'))
_FF_CACHE: str | None = None


def FF() -> str:
    global _FF_CACHE
    if _FF_CACHE is None:
        _FF_CACHE = find_ffmpeg()
    return _FF_CACHE
_FF_NEW_SYNC: bool | None = None


def ffmpeg_sync_args() -> list:
    """关键帧抽取时的「按原时间戳输出」参数。

    ffmpeg 7.1 起 `-vsync` 被标记为废弃，8.x master 构建已彻底移除（写了就
    "Unrecognized option"、整条命令直接失败、image2pipe 输出 0 字节）。
    新版用 `-fps_mode passthrough`，语义与旧 `-vsync 0` 一致。
    这里做一次能力探测：新 ffmpeg 用 -fps_mode，老 ffmpeg 回退 -vsync 0。
    """
    global _FF_NEW_SYNC
    if _FF_NEW_SYNC is None:
        _FF_NEW_SYNC = False
        try:
            r = subprocess.run(
                [FF(), '-hide_banner', '-loglevel', 'quiet',
                 '-f', 'lavfi', '-i', 'nullsrc=s=16x16:d=0.1',
                 '-fps_mode', 'passthrough', '-frames:v', '1',
                 '-f', 'null', '-'],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=CRE, timeout=30)
            _FF_NEW_SYNC = (r.returncode == 0)
        except Exception:
            _FF_NEW_SYNC = False
    return ['-fps_mode', 'passthrough'] if _FF_NEW_SYNC else ['-vsync', '0']


def run_ff(args, want_stdout=False):
    return subprocess.run(
        args,
        stdout=subprocess.PIPE if want_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE, creationflags=CRE)


def split_jpegs(buf: bytes) -> list[bytes]:
    out, i = [], 0
    while True:
        a = buf.find(JPG_SOI, i)
        if a < 0:
            break
        b = buf.find(JPG_EOI, a)
        if b < 0:
            break
        out.append(buf[a:b + 2])
        i = b + 2
    return out


def probe(path: str) -> dict:
    """一次 ffmpeg -i 同时拿到创建时间、时长、GPS。"""
    s = run_ff([FF(), '-hide_banner', '-i', path]).stderr.decode('utf-8', 'ignore')
    dur = 0.0
    m = _DUR.search(s)
    if m:
        h, mi, sec = m.groups()
        dur = int(h) * 3600 + int(mi) * 60 + float(sec)
    ct = None
    m = _CT.search(s)
    if m:
        ct = m.group(1).strip()
    gps = None
    m = _LOC.search(s)
    if m:
        parts = re.findall(r'[+\-][0-9.]+', m.group(1))
        if len(parts) == 2:
            lat, lon = float(parts[0]), float(parts[1])
            if abs(lat) > 0.01 or abs(lon) > 0.01:
                gps = (lat, lon)
    return {'dur': dur, 'creation': ct, 'gps': gps}


def extract_keyframes(path, count, width, dur):
    """只解关键帧（快），按时间均匀取 count 张；不足时 fps 会补重复帧，由调用方去重。"""
    vf = ("select='eq(pict_type,I)',scale='min(%d,iw)':-2,fps=%d/%.3f"
          % (width, count, max(0.1, dur)))
    r = subprocess.run(
        [FF(), '-hide_banner', '-loglevel', 'error', '-skip_frame', 'nokey',
         '-i', path] + ffmpeg_sync_args() +
        ['-vf', vf, '-frames:v', str(count),
         '-q:v', '3', '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=CRE)
    return split_jpegs(r.stdout)


def extract_seek(path, positions, width):
    """逐点定位抽帧（时长无关，约 0.6s/帧）。"""
    out = []
    for sec in positions:
        r = subprocess.run(
            [FF(), '-hide_banner', '-loglevel', 'error', '-ss', '%.3f' % max(0.0, sec),
             '-i', path, '-frames:v', '1', '-vf', "scale='min(%d,iw)':-2" % width,
             '-q:v', '3', '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=CRE)
        j = split_jpegs(r.stdout)
        if j:
            out.append(j[0])
    return out


def score_frame(data: bytes) -> float:
    """清晰度（边缘强度）× 亮度合理度，越高越好。"""
    img = Image.open(io.BytesIO(data)).convert('L')
    brightness = ImageStat.Stat(img).mean[0]
    sharpness = ImageStat.Stat(img.filter(ImageFilter.FIND_EDGES)).stddev[0]
    if brightness < 18 or brightness > 242:
        factor = 0.05
    elif brightness < 35 or brightness > 225:
        factor = 0.5
    else:
        factor = 1.0
    return sharpness * factor


def pick_frames(path, dur, need, cand_width=CAND_WIDTH):
    """返回 (按时间排列的 need 帧, 抽帧方式, 耗时)。先关键帧，不足回退定位抽帧。"""
    n_cand = max(need + 2, need * 2)
    t0 = time.time()
    raw = extract_keyframes(path, n_cand, cand_width, dur)
    seen, uniq = set(), []
    for f in raw:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    how = T('关键帧')
    if len(uniq) < need:
        pos = [dur * (0.12 + 0.76 * i / max(1, n_cand - 1)) for i in range(n_cand)]
        uniq = extract_seek(path, pos, cand_width)
        how = T('定位抽帧')
    t_ex = time.time() - t0
    if not uniq:
        return [], how, t_ex
    if len(uniq) <= need:
        return uniq, how, t_ex
    try:
        sc = [(score_frame(f), i) for i, f in enumerate(uniq)]
        picked = []
        for k in range(need):                    # 时间分段，每段取最清晰的一张
            grp = [x for x in sc if k * len(sc) // need <= x[1] < (k + 1) * len(sc) // need]
            if grp:
                picked.append(max(grp)[1])
        return [uniq[i] for i in sorted(set(picked))], how, t_ex
    except Exception:
        step = len(uniq) / need
        return [uniq[int(i * step)] for i in range(need)], how, t_ex


def grid_layout(n: int):
    """给 n 帧选格子布局，并算出满足像素预算的最大 16:9 单格尺寸。"""
    if n <= 1:
        rows, cols = 1, 1
    elif n == 2:
        rows, cols = 1, 2
    elif n <= 4:
        rows, cols = 2, 2
    elif n <= 6:
        rows, cols = 2, 3
    else:
        cols = 4
        rows = (n + cols - 1) // cols
    c = math.sqrt(PIX_BUDGET / (rows * cols * (9 / 16)))
    cw = max(128, int(c // 8) * 8)
    ch = max(72, int(round(cw * 9 / 16 / 8)) * 8)
    return rows, cols, cw, ch


def compose(frames, cols=None, rows=None):
    """拼成一张宫格 JPEG，返回 (jpeg bytes, (W, H))。"""
    n = len(frames)
    r_auto, c_auto, cw, ch = grid_layout(n)
    rows = rows or r_auto
    cols = cols or c_auto
    W = cols * cw + (cols + 1) * GUTTER
    H = rows * ch + (rows + 1) * GUTTER
    canvas = Image.new('RGB', (W, H), (255, 255, 255))
    for i, b in enumerate(frames):
        try:
            im = Image.open(io.BytesIO(b)).convert('RGB')
        except Exception:
            continue
        r = min(cw / im.width, ch / im.height)
        im = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))))
        r0, c0 = divmod(i, cols)
        x = GUTTER + c0 * (cw + GUTTER) + (cw - im.width) // 2
        y = GUTTER + r0 * (ch + GUTTER) + (ch - im.height) // 2
        canvas.paste(im, (x, y))
    buf = io.BytesIO()
    canvas.save(buf, 'JPEG', quality=88)
    return buf.getvalue(), (W, H)


def scan_meta(directory: str, files: list, manual: dict | None = None) -> dict:
    """预扫描每个视频的拍摄时间与 GPS，供「无 GPS 按时间就近借用」使用。

    manual: {文件名: datetime} —— 用户手工指定的拍摄时间，优先级最高。
    """
    manual = manual or {}
    meta = {}
    for fn in files:
        mdt = manual.get(fn)
        if mdt:
            info = {'creation': '', 'gps': None}
            try:
                info = probe(os.path.join(directory, fn))
            except Exception:
                pass
            meta[fn] = {'dt': mdt, 'gps': info.get('gps'), 'gps_src': T('手工'),
                        'gps_level': 'gps' if info.get('gps') else '',
                        'dur': info.get('dur', 0.0)}
            continue
        try:
            info = probe(os.path.join(directory, fn))
        except Exception:
            info = {'creation': '', 'gps': None}
        meta[fn] = {'dt': parse_dt(info.get('creation')) or dt_from_name(fn),
                    'gps': info.get('gps'),
                    'gps_src': 'GPS' if info.get('gps') else '',
                    'gps_level': 'gps' if info.get('gps') else '',
                    'dur': info.get('dur', 0.0)}
    return meta


def _rat(v):
    try:
        return float(v)
    except Exception:
        pass
    try:
        return float(v[0]) / float(v[1])
    except Exception:
        return 0.0


def _dms_to_deg(v):
    """EXIF 的 (度, 分, 秒) 三元组 → 十进制度。"""
    try:
        return _rat(v[0]) + _rat(v[1]) / 60.0 + _rat(v[2]) / 3600.0
    except Exception:
        return None


def _photo_exif(path: str):
    """读一张照片的拍摄时间与 GPS，返回 (datetime|None, (lat,lon)|None)。"""
    try:
        im = Image.open(path)
        ex = im.getexif()
        im.close()
    except Exception:
        return None, None
    dt = None
    try:
        s = ex.get(36867) or ex.get(36868) or ex.get(306)
        if s:
            dt = datetime.strptime(str(s).strip()[:19], '%Y:%m:%d %H:%M:%S')
    except Exception:
        dt = None
    gps = None
    try:
        ifd = ex.get_ifd(0x8825)
        if ifd:
            la, lo = _dms_to_deg(ifd.get(2)), _dms_to_deg(ifd.get(4))
            if la and lo:
                if str(ifd.get(1, 'N')).upper().startswith('S'):
                    la = -la
                if str(ifd.get(3, 'E')).upper().startswith('W'):
                    lo = -lo
                gps = (round(la, 5), round(lo, 5))
    except Exception:
        gps = None
    return dt, gps


def find_no_time(directory: str, files: list) -> list:
    """找出「读不到拍摄时间」的视频。

    只对文件名里没时间的少数文件做 ffprobe，所以很快。
    """
    out = []
    for fn in files:
        if dt_from_name(fn) is not None:
            continue
        try:
            info = probe(os.path.join(directory, fn))
        except Exception:
            info = {'creation': ''}
        if parse_dt(info.get('creation')) is None:
            out.append(fn)
    return out


def list_videos(directory: str) -> list[str]:
    return [f for f in sorted(os.listdir(directory))
            if os.path.isfile(os.path.join(directory, f)) and f.lower().endswith(VID_EXT)]


def targets_of(directory: str, all_files=False, limit=0) -> list[str]:
    files = list_videos(directory)
    t = files if all_files else [f for f in files if not ALREADY_NAMED.match(f)]
    if limit:
        t = t[:limit]
    return t


def list_photos(directory: str) -> list[str]:
    return [f for f in sorted(os.listdir(directory))
            if os.path.isfile(os.path.join(directory, f))
            and os.path.splitext(f)[1].lower() in PHOTO_EXT]


def photo_meta(path: str) -> dict:
    """照片的拍摄时间与 GPS：EXIF 优先，其次文件名里的日期，都没有就留空。"""
    dt, gps = _photo_exif(path)
    if dt is None:
        dt = dt_from_name(os.path.basename(path))
    return {'dt': dt, 'gps': gps, 'gps_src': 'EXIF' if gps else ''}


def _photo_for_model(path: str, max_side: int = 1024) -> bytes:
    """把照片压成送给模型的 JPEG。视觉端本来就会把图统一重采样，
    这里只为了少传点字节（token 数与耗时不受尺寸影响）。"""
    im = Image.open(path)
    im = ImageOps.exif_transpose(im).convert('RGB')
    if max(im.size) > max_side:
        r = max_side / float(max(im.size))
        im = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))),
                       Image.LANCZOS)
    b = io.BytesIO()
    im.save(b, 'JPEG', quality=88)
    return b.getvalue()
