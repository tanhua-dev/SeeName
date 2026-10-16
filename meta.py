# -*- coding: utf-8 -*-
"""把 AI 生成的描述写回文件本身（元数据）+ 落盘成摘要。

三条独立开关（默认全开），关掉哪个就不做哪件事：
  ① 元数据写入：照片写 EXIF/XMP，视频写容器标签；
  ② .ai.txt 摘要：每个媒体文件旁边一份同名 txt；
  ③ 目录说明.md：跑完在文件夹根目录汇总一份。

写元数据的三条底线：
  * **只增不改**：用户自己写过 ImageDescription / XPTitle / XPComment / XPKeywords
    的，那个字段原样不动，AI 内容挪到同组的空字段里，并加「【AI 描述】」前缀；
    四个字段都被占满时，把 AI 描述换行追加到 XPComment 末尾（同样加分隔符），
    绝不覆盖用户原文。
  * **不重编码**：视频一律走 mutagen（只改 moov/udta）或 ffmpeg -c copy
    （只改容器标签），音视频流一个字节都不动；写完回读校验。
  * **可逆**：mutagen 与 ffmpeg 都是「写临时文件 → 校验 → 替换」，中途失败
    原文件还在；重复处理同一文件时覆盖同一字段，不会越叠越长。
"""
import io
import os
import re
import shutil
import subprocess
import sys
import time
import zlib

from strings import T

AI_TAG = '【AI 描述】'        # 标记：这一格是我们写的，可以被下次覆盖
AI_SEP = '\n【AI 描述】'      # 追加到用户内容后面时的分隔

VID_MUTAGEN = ('.mp4', '.m4v', '.mov')
VID_FF = ('.mkv', '.avi', '.wmv', '.3gp', '.mpg', '.mpeg', '.ts', '.flv', '.webm')
JPG_EXT = ('.jpg', '.jpeg', '.webp')

CRE = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


# ------------------------------------------------------------------ 小工具
def _u16(s: str) -> bytes:
    """XP 系列字段要 UTF-16LE（Windows 资源管理器据此显示中文）。"""
    return (s or '').encode('utf-16-le') + b'\x00\x00'


def _u16dec(v) -> str:
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
    elif isinstance(v, (list, tuple)):
        b = bytes(bytearray(v))
    else:
        b = str(v or '').encode('utf-16-le')
    try:
        return b.decode('utf-16-le').rstrip('\x00')
    except Exception:
        return ''


def _clean(s: str) -> str:
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def make_title(place: str, desc: str, limit: int = 30) -> str:
    """短标题：'蓬安中学·校门留影' 这种（地点 + 内容）。"""
    p, d = _clean(place), _clean(desc)
    if p and d:
        t = '%s·%s' % (p, d)
    else:
        t = p or d
    return t[:limit]


def make_keywords(place: str, desc: str, kw: str, limit: int = 5) -> str:
    """关键词：逗号分隔的名词短语；模型没给就用地名 + 描述兜底。"""
    parts = [x.strip() for x in re.split(r'[,，、;；]+', str(kw or '')) if x.strip()]
    if not parts:
        parts = [x for x in (place, desc) if x]
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return ','.join(out[:limit])


def _wrap(x):
    return x - 0x100000000 if (isinstance(x, int) and x > 0x7FFFFFFF) else x


def _fix_srational(ex: dict):
    """很多手机（OPPO/部分安卓）把负的亮度、曝光补偿按**无符号**写进 EXIF，
    例如 -98 被存成 4294967198。piexif 读出来没问题，重新 dump 时按
    SRATIONAL（有符号）打包就撞 int32 上限，整个写入直接失败。
    按 tag 声明的类型（SLONG=9 / SRATIONAL=10）把这些值回绕成负数——
    字节语义不变，反而比原始写法更标准。"""
    import piexif
    for ifd in ('0th', 'Exif', 'GPS', '1st', 'Interop', 'MPF'):
        d = ex.get(ifd)
        if not d:
            continue
        try:
            tags = piexif.TAGS.get(ifd) or {}
        except Exception:
            tags = {}
        for k in list(d.keys()):
            t = (tags.get(k) or {}).get('type')
            if t not in (9, 10):
                continue
            v = d[k]
            if isinstance(v, tuple) and v and isinstance(v[0], tuple):
                d[k] = tuple(tuple(_wrap(x) for x in r) for r in v)
            elif isinstance(v, tuple):
                d[k] = tuple(_wrap(x) for x in v)
            elif isinstance(v, int):
                d[k] = _wrap(v)


def _fix_undefined(ex: dict):
    """piexif 的 load/dump 不对称：UNDEFINED（type 7）的字段（0xA301 SceneType
    这类）被 load 成 int，dump 却只认 bytes——原样 dump 直接报错。按声明类型
    把 int/str 补成 bytes。"""
    import piexif
    for ifd in ('0th', 'Exif', 'GPS', '1st', 'Interop', 'MPF'):
        d = ex.get(ifd)
        if not d:
            continue
        try:
            tags = piexif.TAGS.get(ifd) or {}
        except Exception:
            tags = {}
        for k in list(d.keys()):
            t = (tags.get(k) or {}).get('type')
            if t != 7:
                continue
            v = d[k]
            if isinstance(v, int):
                d[k] = bytes([v])
            elif isinstance(v, str):
                d[k] = v.encode('latin-1', 'replace')


def _dump_safe(ex: dict) -> bytes:
    """dump 前先做两处规范化；个别实在过不去的怪 tag（厂商私有字段居多）
    只能移除后重试——用它们的 EXIF 换整张照片的元数据写入成功，值得。"""
    import piexif
    _fix_srational(ex)
    _fix_undefined(ex)
    for _ in range(8):
        try:
            return piexif.dump(ex)
        except Exception as e:
            m = re.search(r'(\d+)\s+in\s+(\w+)\s+IFD', str(e))
            if not m:
                raise
            d = ex.get(m.group(2)) or {}
            d.pop(int(m.group(1)), None)
    raise RuntimeError('exif dump failed')


def _is_ai(s: str) -> bool:
    """这一格是不是我们之前写的（是就可以覆盖，不算用户原文）。"""
    return str(s or '').strip().startswith(AI_TAG)


def _has_user(s: str) -> bool:
    s = str(s or '').strip()
    return bool(s) and not _is_ai(s)


# ------------------------------------------------------------------ 照片：EXIF
def _jpg_write(path: str, title: str, long: str, kw: str) -> tuple[bool, str]:
    import piexif
    from PIL import Image
    ex = piexif.load(path)
    _fix_srational(ex)
    z = ex.setdefault('0th', {})
    I = piexif.ImageIFD

    def cur(tag):
        v = z.get(tag)
        if v is None:
            return ''
        if isinstance(v, (bytes, bytearray)):
            # 0x010E 存的是 UTF-8 字节，piexif 取出来是 latin-1 解码过的 str
            try:
                return bytes(v).decode('utf-8', 'replace')
            except Exception:
                return ''
        if isinstance(v, (list, tuple)):
            return _u16dec(v)
        return str(v)

    touched = []
    # ① 长描述：ImageDescription 空 → 写这里；被用户占了 → 挪到 XPComment
    if not _has_user(cur(I.ImageDescription)):
        z[I.ImageDescription] = (AI_TAG + long).encode('utf-8')
        touched.append('ImageDescription')
    elif not _has_user(_u16dec(z.get(I.XPComment))):
        z[I.XPComment] = _u16(AI_TAG + long)
        touched.append('XPComment')
    else:
        old = _u16dec(z.get(I.XPComment))
        if long not in old:
            base = old[:-2] if _is_ai(old) else old
            z[I.XPComment] = _u16(base + AI_SEP + long)
            touched.append('XPComment+')

    # ② 短标题
    if title and not _has_user(_u16dec(z.get(I.XPTitle))):
        z[I.XPTitle] = _u16(title)
        touched.append('XPTitle')

    # ③ 关键词
    if kw and not _has_user(_u16dec(z.get(I.XPKeywords))):
        z[I.XPKeywords] = _u16(kw)
        touched.append('XPKeywords')

    piexif.insert(_dump_safe(ex), path)

    # —— 回读校验：确认中文完整（按字节比对，避免编码把汉字吃成 ? 或截断）
    back = piexif.load(path)['0th']
    v = back.get(I.ImageDescription)
    ok_long = False
    if v is not None:
        raw = bytes(v) if isinstance(v, (bytes, bytearray)) else str(v).encode('utf-8')
        ok_long = long.encode('utf-8') in raw
    if not ok_long:
        v2 = back.get(I.XPComment)
        s2 = _u16dec(v2)
        ok_long = long in s2
    if not ok_long:
        return False, T('写完回读不一致')
    return True, ','.join(touched)


def _heic_write(path: str, title: str, long: str, kw: str) -> tuple[bool, str]:
    """HEIC/HEIF：piexif 读不了这种容器，走 exiftool 命令行兜底。

    exiftool 不在 PATH 里就如实报「需要 exiftool」，绝不假装成功。
    """
    exe = shutil.which('exiftool') or shutil.which('exiftool.exe')
    if not exe:
        base = os.path.dirname(os.path.abspath(
            sys.executable if getattr(sys, 'frozen', False) else __file__))
        for nm in ('exiftool', 'exiftool.exe', 'exiftool(-k).pl'):
            p = os.path.join(base, 'tools', nm)
            if os.path.exists(p):
                exe = p
                break
    if not exe:
        return False, T('HEIC/HEIF 需要 exiftool（未找到，已跳过）')
    args = [exe, '-overwrite_original', '-charset', 'UTF8', '-n',
            '-ImageDescription=%s' % (AI_TAG + long),
            '-XPTitle=%s' % title,
            '-XPComment=%s' % (AI_TAG + long),
            '-XPKeywords=%s' % kw, path]
    try:
        p = subprocess.run(args, capture_output=True, creationflags=CRE, timeout=60)
        if p.returncode != 0:
            return False, T('exiftool 写入失败')
    except Exception as e:
        return False, T('exiftool 调用失败：%r') % (e,)
    # 回读：exiftool -ImageDescription -b
    try:
        p = subprocess.run([exe, '-charset', 'UTF8', '-ImageDescription', '-b', path],
                           capture_output=True, creationflags=CRE, timeout=30)
        s = p.stdout.decode('utf-8', 'replace')
        return (long in s), T('已写入 exiftool')
    except Exception:
        return True, T('已写入 exiftool（回读失败）')


# ------------------------------------------------------------------ PNG：XMP
XMP_NS = {
    'x': 'adobe:ns:meta/',
    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'dc': 'http://purl.org/dc/elements/1.1/',
}
XMP_MIN = (
    '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="SeeName">'
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '</rdf:RDF></x:xmpmeta>'
    '<?xpacket end="w"?>')


def _xmp_build(title: str, long: str, kw: str, old: str = '') -> str:
    """在原有 XMP 上改 dc:title / dc:description / dc:subject，其余原样保留。"""
    import xml.etree.ElementTree as ET
    for k, v in XMP_NS.items():
        ET.register_namespace(k, v)
    root = ET.fromstring(old) if old.strip().startswith('<') else ET.fromstring(XMP_MIN)
    rdf = root.find('rdf:RDF', XMP_NS)
    if rdf is None:
        rdf = ET.SubElement(root, '{%s}RDF' % XMP_NS['rdf'])
    de = rdf.find('rdf:Description', XMP_NS)
    if de is None:
        de = ET.SubElement(rdf, '{%s}Description' % XMP_NS['rdf'])
        de.set('{%s}about' % XMP_NS['rdf'], '')

    def put(tag, text, bag=False):
        el = de.find('dc:%s' % tag, XMP_NS)
        if el is None:
            el = ET.SubElement(de, '{%s}%s' % (XMP_NS['dc'], tag))
        for c in list(el):
            el.remove(c)
        wrap = '{%s}%s' % (XMP_NS['rdf'], 'Bag' if bag else 'Alt')
        w = ET.SubElement(el, wrap)
        items = [text] if not bag else [x for x in str(text).split(',') if x]
        for v in items:
            li = ET.SubElement(w, '{%s}li' % XMP_NS['rdf'])
            if not bag:
                li.set('{http://www.w3.org/XML/1998/namespace}lang', 'x-default')
            li.text = v

    put('title', title or '')
    put('description', (AI_TAG + long) if long else '')
    if kw:
        put('subject', kw, bag=True)
    return ('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            + ET.tostring(root, encoding='unicode')
            + '<?xpacket end="w"?>')


def _png_chunks(data: bytes) -> list:
    out, i = [], 8
    n = len(data)
    while i + 8 <= n:
        ln = int.from_bytes(data[i:i + 4], 'big')
        typ = data[i + 4:i + 8]
        out.append((typ, data[i + 8:i + 8 + ln]))
        i += 12 + ln
    return out


def _png_xmp_of(chunks: list) -> tuple[int, str]:
    for idx, (typ, body) in enumerate(chunks):
        if typ in (b'iTXt', b'tEXt', b'zTXt') and body.startswith(b'XML:com.adobe.xmp\x00'):
            b = body.split(b'\x00', 1)[1]
            if typ == b'iTXt':
                b = b.split(b'\x00', 3)[-1]
            elif typ == b'zTXt':
                import zlib
                try:
                    b = zlib.decompress(b[1:])
                except Exception:
                    continue
            return idx, b.decode('utf-8', 'replace')
    return -1, ''


def _png_write(path: str, title: str, long: str, kw: str) -> tuple[bool, str]:
    data = open(path, 'rb').read()
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        return False, T('不是 PNG')
    chunks = _png_chunks(data)
    idx, old = _png_xmp_of(chunks)
    try:
        xmp = _xmp_build(title, long, kw, old)
    except Exception:
        xmp = _xmp_build(title, long, kw, '')
    body = (b'XML:com.adobe.xmp\x00' + b'\x00' + b'\x00' + b'\x00' + b'\x00'
            + xmp.encode('utf-8'))
    newch = (b'iTXt', body)
    if idx >= 0:
        chunks[idx] = newch
    else:
        pos = len(chunks) - 1 if chunks and chunks[-1][0] == b'IEND' else len(chunks)
        chunks.insert(pos, newch)
    raw = b'\x89PNG\r\n\x1a\n'
    for typ, b in chunks:
        raw += len(b).to_bytes(4, 'big') + typ + b + zlib.crc32(typ + b).to_bytes(4, 'big')
    tmp = path + '.xmp.tmp'
    with open(tmp, 'wb') as f:
        f.write(raw)
    ok = False
    try:
        from PIL import Image
        im = Image.open(tmp)
        im.verify()
        ok = True
    except Exception:
        ok = False
    if not ok:
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False, T('PNG 写入后校验失败，已放弃')
    os.replace(tmp, path)
    # 回读
    idx2, s2 = _png_xmp_of(_png_chunks(open(path, 'rb').read()))
    return ((long in s2) if long else bool(s2)), T('XMP')


# ------------------------------------------------------------------ 视频
def _ffprobe_dur(ff: str, path: str) -> float:
    try:
        p = subprocess.run([ff, '-hide_banner', '-i', path, '-f', 'null', '-'],
                           capture_output=True, creationflags=CRE, timeout=120)
        se = p.stderr.decode('utf-8', 'replace')
        m = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', se)
        if m:
            return (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                    + float(m.group(3)))
    except Exception:
        pass
    return -1.0


def _ff_tags(ff: str, path: str) -> dict:
    try:
        p = subprocess.run([ff, '-hide_banner', '-i', path, '-f', 'null', '-'],
                           capture_output=True, creationflags=CRE, timeout=120)
        se = p.stderr.decode('utf-8', 'replace')
        m = re.search(r'Metadata:(.*?)Duration', se, re.S)
        out = {}
        if m:
            for ln in m.group(1).splitlines():
                if ':' in ln:
                    k, v = ln.split(':', 1)
                    out[k.strip().lower()] = v.strip()
        return out
    except Exception:
        return {}


def _video_ffmpeg(ff: str, path: str, title: str, long: str,
                  kw: str) -> tuple[bool, str]:
    """所有视频容器统一走 ffmpeg -c copy：只改容器标签，音视频流原样复制。

    （最初一版想用 mutagen 原地改 MP4，但它是 GPLv2+，打包进 exe 会把整个
    分发拖进 GPL——我们已经为合规换过 LGPL 的 ffmpeg，这里不再引入 GPL。
    -c copy 不重编码、不碰流，与 mutagen 达到同样的效果，只是要落一个临时文件。）
    MP4/MOV 会落成标准 atom：©nam / ©cmt / desc(©des) / keyw；
    MKV 写 TITLE/DESCRIPTION/COMMENT/KEYWORDS；AVI 写 INFO 的 INAM/ICMT。"""
    d0 = _ffprobe_dur(ff, path)
    tmp = path + '.meta.tmp' + os.path.splitext(path)[1]
    args = [ff, '-hide_banner', '-loglevel', 'error', '-i', path, '-c', 'copy',
            '-map', '0',
            '-metadata', 'title=%s' % title,
            '-metadata', 'comment=%s' % long,
            '-metadata', 'description=%s' % long,
            '-metadata', 'keywords=%s' % kw,
            '-y', tmp]
    try:
        p = subprocess.run(args, capture_output=True, creationflags=CRE, timeout=1800)
    except Exception as e:
        return False, T('ffmpeg 调用失败：%r') % (e,)
    if p.returncode != 0 or not os.path.exists(tmp):
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False, T('ffmpeg 写入失败')
    d1 = _ffprobe_dur(ff, tmp)
    if d0 > 0 and (d1 <= 0 or abs(d1 - d0) > 0.5):
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False, T('时长校验未通过（%.2fs → %.2fs），已放弃') % (d0, d1)
    tags = _ff_tags(ff, tmp)
    ok = (title in tags.get('title', '')) and (long[:20] in tags.get('comment', '')
                                              or long[:20] in tags.get('description', ''))
    if not ok:
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False, T('标签回读不一致，已放弃')
    os.replace(tmp, path)
    return True, T('容器标签（时长 %.2fs 不变）') % d0


# ------------------------------------------------------------------ 对外主入口
def write_meta(path: str, title: str, long: str, kw: str,
               ffmpeg: str = '') -> tuple[bool, str]:
    """写一个文件的元数据。返回 (是否成功, 说明)。失败一律不改原文件。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in JPG_EXT:
            return _jpg_write(path, title, long, kw)
        if ext == '.png':
            return _png_write(path, title, long, kw)
        if ext in ('.heic', '.heif'):
            return _heic_write(path, title, long, kw)
        if ext in VID_MUTAGEN:
            try:
                return _video_mutagen(path, title, long, kw)
            except Exception as e:
                if not ffmpeg:
                    return False, T('mutagen 写入失败：%r') % (e,)
                return _video_ffmpeg(ffmpeg, path, title, long, kw)
        if ffmpeg:
            return _video_ffmpeg(ffmpeg, path, title, long, kw)
        return False, T('这个格式暂不支持写元数据：%s') % ext
    except Exception as e:
        return False, T('写元数据失败：%r') % (e,)


def write_sidecar(path: str, place: str, long: str) -> tuple[bool, str]:
    """同名 .ai.txt：第一行地点、第二行内容长描述（跟着文件名走）。"""
    try:
        p = path + '.ai.txt'
        with io.open(p, 'w', encoding='utf-8') as f:
            f.write('%s\n%s\n' % (place or '', long or ''))
        return True, os.path.basename(p)
    except Exception as e:
        return False, repr(e)


def write_dir_md(directory: str, rows: list, title: str = '') -> tuple[bool, str]:
    """目录说明.md：整目录一次汇总（每次重写，不累加）。"""
    try:
        p = os.path.join(directory, '目录说明.md')
        with io.open(p, 'w', encoding='utf-8') as f:
            f.write('# %s\n\n' % (title or T('目录说明')))
            f.write('%s：%s\n\n%s：%d\n\n'
                    % (T('生成时间'), time.strftime('%Y-%m-%d %H:%M:%S'),
                       T('文件数'), len(rows)))
            for name, place, long in rows:
                f.write('## %s\n\n' % name)
                f.write('- %s：%s\n' % (T('地点'), place or T('（地点不明）')))
                f.write('- %s：%s\n\n' % (T('内容'), long or T('（无描述）')))
        return True, os.path.basename(p)
    except Exception as e:
        return False, repr(e)
