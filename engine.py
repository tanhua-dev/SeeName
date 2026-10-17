# -*- coding: utf-8 -*-
"""视频 AI 改名 · 引擎层（独立、无 GUI 依赖）。

设计目标：
  * 只依赖 标准库 + Pillow + ffmpeg + Ollama，不依赖 video-ai-renamer-win。
  * 把「抽帧 → 宫格拼图 → 一次送模型 → 解析 → 落名」封装成回调式 API，
    方便 GUI（main.py）或命令行驱动。

关键结论（由此前的四轮基准实测得出，勿随意改动）：
  * Ollama 的 qwen3-vl 对图片有约 1M 像素的下限，小于它会被内部放大到 1M，
    token 数与耗时几乎不变（单次调用地板价约 11 秒）。因此把 K 帧拼成一张
    宫格并压在 1M 像素内，能用「一次调用的成本」塞进多帧。
  * num_ctx 设为 2048 即可（宫格图约 1180 token）；设 8192 会导致约 0.9GB
    显存溢出到 CPU，生成速度从 62 tok/s 掉到 14 tok/s。
  * 抽帧只解关键帧（-skip_frame nokey）平均 0.7s；不足时回退到逐点定位抽帧。
"""
from __future__ import annotations

import sys as _sys
_sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from strings import T  # noqa: E402

import atexit
import glob
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta

from PIL import Image, ImageFilter, ImageOps, ImageStat

from inference import InferenceBackend, ModelStore
from inference.ollama import OllamaBackend, OllamaModelStore, OllamaRuntime

import geo          # 联网精确定位（可选，默认关闭）
import meta as metadb   # 描述落盘：EXIF / XMP / 视频标签 / .ai.txt / 目录说明.md
                        # （叫 metadb 是因为 rename_one() 里有个同名的 meta 形参）

IS_MAC = (sys.platform == 'darwin')
IS_WIN = (sys.platform == 'win32' or os.name == 'nt')


def _app_dir() -> str:
    """软件根目录（兼容 PyInstaller 打包后的单文件模式）。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _user_data_dir() -> str:
    """可写的用户数据目录（配置 / 台账 / 报告 / 缓存 / 日志都写这里）。

    Windows 下沿用便携模式：全部放软件目录旁边，整个文件夹拷走就能用。
    macOS 下 .app  bundle 里写不进去（也不该写——放在 /Applications 里会因
    权限/签名失败，且升级即丢），所以改成系统的
    ~/Library/Application Support/SeeName。
    模型库默认还跟着软件目录走（方便整包拷贝），该目录不可写时才退到这里。
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

# ------------------------------------------------------------------ 默认参数
MODEL = 'qwen3-vl:4b-instruct'
DEFAULT_PORT = 11500        # 自带运行时的私有端口（不抢占系统 Ollama 的 11434）
CTX = 2048
KEEP_ALIVE = '15m'
PIX_BUDGET = 950_000        # 整张宫格的目标像素预算
GUTTER = 6                  # 宫格分隔白边
CAND_WIDTH = 512            # 抽候选帧的宽度
VID_EXT = ('.mp4', '.mov', '.mkv', '.avi', '.m4v', '.3gp', '.wmv')
# 已被本软件命名过的文件：
#   旧格式 20241220_2041_成都肖家河街道_剥洋葱.mp4
#   新格式 视20241220成都肖家河—剥洋葱.mp4 / 图20241220成都肖家河—剥洋葱.jpg
ALREADY_NAMED = re.compile(r'^(\d{8}_\d{4}(_|\.)|[视图]\d{8})')

PROMPT = (
    T('下面这张图是一段手机视频按时间顺序抽出的若干画面拼成的网格' + '（从左到右、从上到下为时间先后）。\n' + '请用极短的词组说明画面主要内容，主体优先。\n' + '只输出一行，格式严格为：\n' + '描述=<4到8个汉字>\n' + '要求：\n' + '1) 画面里有人时，先写主体是谁（用 婆婆/爷爷/奶奶/宝宝/男孩/女孩/女子/男子/' + '一家人/两人 这类词），再写他在做什么，' + '例如 婆婆剥洋葱 / 宝宝吹蜡烛 / 一家人吃饭 / 男子江边钓鱼；\n' + '2) 画面里没有人物时，直接写核心事物或动作，' + '例如 拆包装 / 江边夜景 / 年夜饭；\n' + '3) 不要地点、不要场所词（室内/室外/厨房/客厅），' + '不要景色、光线、天气、心情、形容词，不要标点符号；\n' + '4) 不要解释，不要输出任何其它文字，也不要描述网格本身。')
)

# 照片单张、动作不明显，主体更容易认错不了；要求「谁+特征/动作」。
PHOTO_PROMPT = (
    T('请看这张照片，给文件起一个内容描述，主体优先。\n' + '只输出一行，格式严格为：\n' + '描述=<4到8个汉字>\n' + '要求：\n' + '1) 画面里有人时，先写主体是谁（用 婆婆/爷爷/奶奶/宝宝/男孩/女孩/女子/男子/' + '一家人/两人 这类词），再写外观特征或正在做的事，' + '例如 婆婆戴帽微笑 / 宝宝吹蜡烛 / 一家人合影 / 爷爷打太极；\n' + '2) 画面里没有人物时，直接写事物本身，例如 年夜饭 / 生日蛋糕 / 江边夜景；\n' + '3) 不要地点、不要场所词，不要景色、光线、心情、形容词，不要标点符号；\n' + '4) 不要解释，不要输出任何其它文字。')
)

# 想写元数据 / .ai.txt / 目录说明.md 时才用这一版提示词：一次调用同时要三样东西——
# 文件名的极短描述（和上面完全同标准）、元数据用的长描述、关键词。
# 已知的时间和地点直接喂给模型，让它照着写，避免它凭画面瞎编路名。
FULL_TAIL = T(
    '请严格按下面三行输出，不要多余空行、不要解释：\n'
    + '描述=<4到8个汉字的极短词组>\n'
    + '长描述=<100到200个汉字>\n'
    + '关键词=<3到5个名词短语，逗号分隔>\n'
    + '各行要求：\n'
    + '1) 描述：画面里有人时先写主体是谁（婆婆/爷爷/奶奶/宝宝/男孩/女孩/女子/男子/'
    '一家人/两人），再写他在做什么；没有人就写核心事物或动作。'
    '不要地点、不要场所词、不要景色光线心情形容词、不要标点符号；\n'
    + '2) 长描述：写成 2 到 4 句连贯的话，必须同时包含——'
    '时间（用上面给的拍摄时间）、地点（用上面给的地点，能细到街道或具体场所更好）、'
    '画面主体是谁、正在做什么、场景氛围；不要分点、不要小标题、'
    '不要再写「描述=」「长描述=」这类标记；没把握的具体路名门牌不要编；\n'
    + '3) 关键词：3 到 5 个名词短语，逗号分隔，例如 蓬安中学,校门,人物留影。')

PROMPT_FULL = (
    T('下面这张图是一段手机视频按时间顺序抽出的若干画面拼成的网格'
      '（从左到右、从上到下为时间先后）。\n'
      '已知：拍摄时间 {t}；地点 {p}。\n') + FULL_TAIL)

PHOTO_PROMPT_FULL = (
    T('请看这张照片。\n'
      '已知：拍摄时间 {t}；地点 {p}。\n') + FULL_TAIL)

LONG_MAX = 300      # 长描述硬上限（提示词要 100~200 字，留一点余量）
LONG_MIN = 40       # 短于这个数就当模型敷衍了，由引擎用已知信息补齐
KW_MAX = 5


def time_cn(dt) -> str:
    """2026-10-12 → 2026年10月12日（喂给模型/写进长描述用）。"""
    try:
        return T('%d年%d月%d日') % (dt.year, dt.month, dt.day)
    except Exception:
        return ''


def parse_full(text: str) -> tuple[str, str, str]:
    """解析三行输出 → (短描述, 长描述, 关键词)。拿不到就交给调用方兜底。"""
    short = parse_out(text)
    long, kw = '', ''
    m = re.search(r'长描述\s*[=:：]\s*(.+)', text)
    if m:
        long = m.group(1).strip()
    m = re.search(r'关键词\s*[=:：]\s*(.+)', text)
    if m:
        kw = m.group(1).strip()
    long = re.sub(r'\s+', ' ', long).strip().strip('"“”\'')
    kw = re.sub(r'[\[\]（）()「」【】]', '', kw).strip()
    kw = re.sub(r'\s*[,，]\s*', ',', kw)
    if len(kw) > 80:
        kw = kw[:80]
    return short, long[:LONG_MAX], kw


def fill_long(long: str, desc: str, when: str, where: str) -> str:
    """模型敷衍（太短或没给）时用已知信息拼一段能用的长描述。"""
    if len(long or '') >= LONG_MIN:
        return long
    bits = []
    if when:
        bits.append(when)
    if where:
        bits.append(where)
    bits.append(T('画面为%s') % (desc or T('内容不明')))
    s = '，'.join([b for b in bits if b]) + '。'
    if long:
        s = long + '　' + s
    return s[:LONG_MAX]


# ---------------------------------------------------------------- 离线地名库
# 两级地名库，全部随软件内置、全程离线（没有任何在线反查）：
#   data/districts.csv     中国：县级点位 → code,县名,地级市名,lat,lng,src
#                          名录依据国家统计局行政区划代码，坐标取自 OSM 县级行政边界中心点
#   data/cities_world.csv  全球：GeoNames cities1000 精简 → name,lat,lng,cc
#                          用于中国范围之外的坐标，精度到城市级
_DISTRICTS = None
_WORLD = None
_WINDEX = None
_CN_MASK = None
_DIST_CACHE = {}

# 判定「是否在中国」的经纬度窗口：在窗口内走县级精确库，窗口外走全球城市库
CN_LAT = (18.0, 54.0)
CN_LNG = (73.0, 135.0)

DIST_NEAR_KM = 40.0     # 县级点位的平均间距比乡镇大得多，所以阈值放宽：
                        # 40km 内命中县城 → 标「市 + 县」；40~200km → 只标市；更远不标
DIST_FAR_KM = 200.0
WORLD_TIE_KM = 15.0     # 最近点周围这么宽的范围里，一律改用人口更多的那个城市名
WORLD_MAX_KM = 120.0    # 国外：超出最近城市这么远就不标（多为人迹罕至处）
WORLD_WITH_COUNTRY = True   # 国外地名是否带中文国名前缀，如「日本 Tokyo」
DESC_MAX = 9            # 描述字数「安全上限」。提示词要求模型给 3-6 字，但它常常给到
                        # 7-9 字；上限设 6 会把「手持麦克风演唱」砍成「手持麦克风演」这种
                        # 断词。设 9 既不伤简洁（模型本来就短），也不会砍出半截词。

# 「无 GPS 时按时间就近借用位置」的两档窗口（小时）
GPS_NEAR_H = 12.0       # ≤12h：同一天/跨夜半天内，借到「市+镇」
GPS_FAR_H = 72.0        # 12~72h：只借「市」，精确度降级换正确率；更远不借

_DUR = re.compile(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)')
_CT = re.compile(r'creation_time\s*:\s*([0-9T:\-\.Z\+ ]+)', re.I)
_LOC = re.compile(r'^\s*location(?:-eng)?\s*:\s*([+\-][0-9.]+[+\-][0-9.]+)', re.M)

JPG_SOI, JPG_EOI = b'\xff\xd8\xff', b'\xff\xd9'
CRE = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# 目录预设（主界面「预设」下拉）。开源版刻意留空：不带任何开发者的私人目录。
# 需要的话自行添加，键是下拉里显示的名字，值是目录路径，例如：
#     PRESETS = {T('我的照片'): 'D:/Photos'}
# 留空时主界面不显示该下拉，目录栏默认为空，由用户点「浏览…」自行选择。
PRESETS = {}


# ------------------------------------------------------------------ 配置
_CFG: dict | None = None


def config() -> dict:
    """读取软件目录下的 config.json，缺项用默认值补。"""
    global _CFG
    if _CFG is None:
        d = {'port': DEFAULT_PORT, 'models_dir': '', 'model': MODEL}
        try:
            with io.open(CONFIG_PATH, encoding='utf-8') as f:
                d.update(json.load(f) or {})
        except Exception:
            pass
        _CFG = d
    return _CFG


def save_config(**kw) -> dict:
    c = config()
    c.update(kw)
    try:
        with io.open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return c


# Shared instances are assembled here; adapters never import the application engine.
# Existing functions below remain compatibility entry points for the current UI.
_model_runtime = OllamaRuntime(
    app_dir=APP_DIR, user_data=USER_DATA, config=lambda: config(),
    save_config=lambda **kw: save_config(**kw), default_port=DEFAULT_PORT,
    keep_alive=KEEP_ALIVE)
model_backend: InferenceBackend = OllamaBackend(
    _model_runtime, warmup_prompt=PROMPT, context_size=CTX)
_ollama_model_store = OllamaModelStore(_model_runtime)
model_store: ModelStore = _ollama_model_store


# ------------------------------------------------------------------ 外部依赖
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


def find_runtime():
    """定位推理运行时，返回 (exe, source)，source = 'bundled' | 'system'。"""
    return _model_runtime.find_runtime()


def find_ollama() -> str:
    """兼容旧调用：只返回路径。"""
    return find_runtime()[0]


def models_dir(create=True) -> str:
    """模型库目录。默认就是软件自己的 models 文件夹（模型包下载到这里）。"""
    return model_store.directory(create=create)


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


# ------------------------------------------------------------------ 服务


def server_port() -> int:
    return _model_runtime.server_port()


def server_url() -> str:
    return _model_runtime.server_url()


def port_open(t=1.2, port=None) -> bool:
    return _model_runtime.port_open(t=t, port=port)


def is_ollama(port, t=1.5) -> bool:
    """该端口上跑的确实是 Ollama 服务吗？"""
    return _model_runtime.is_ollama(port, t=t)


def server_log_path() -> str:
    return _model_runtime.server_log_path()


def runtime_status() -> dict:
    """运行时信息，供界面展示。"""
    return _model_runtime.runtime_status()


def start_server(on_log=None, timeout=120) -> bool:
    """确保有可用的推理服务；没有就用「自带运行时」拉起来。"""
    return model_backend.start(on_log=on_log, timeout=timeout)


def shutdown_server():
    """退出/切换模型库时收掉自己拉起来的服务，释放显存。"""
    model_backend.close()


atexit.register(shutdown_server)


def set_models_dir(path, on_log=None) -> bool:
    """切换模型库目录（重启自带服务使其生效）。"""
    return model_store.set_directory(path, on_log=on_log)


def system_ollama_models() -> str:
    """探测本机已安装的 Ollama 模型库，用于一键复用（免去重新下载模型包）。"""
    return _ollama_model_store.system_ollama_models()


def local_models_in(path: str) -> int:
    """粗略统计某个模型库里有多少个模型（按 manifests 下的文件计）。"""
    return _ollama_model_store.local_models_in(path)


def registry() -> str:
    """当前模型下载源；空字符串表示 Ollama 官方源。"""
    return model_store.source()


def set_registry(url: str) -> str:
    """设置模型下载源（传空字符串即恢复 Ollama 官方源）。"""
    return model_store.set_source(url)


# ------------------------------------------------------------------ 基础工具
def server_info() -> dict:
    """查询服务与显存占用（/api/ps）。"""
    status = model_backend.status()
    return {'up': status.available, 'models': [
        {'name': model.name, 'size': model.size_bytes, 'vram': model.device_bytes}
        for model in status.models]}


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


# ------------------------------------------------------------------ 抽帧
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


# ------------------------------------------------------------------ 模型管理
# 模型文件不随软件分发，但软件内置「下载 / 检测」能力：模型包按需单独下载，
# 落在软件自己的 models 目录里（便携：换台机器把整个文件夹拷走即可继续用）。
MODEL_CATALOG = model_store.catalog


def installed_models(retry: int = 2) -> dict:
    """返回 {模型名: 字节数}；服务不可用或查询失败时返回空字典。"""
    return model_store.installed(retry=retry)


def list_models() -> list:
    """本机已下载的模型名（升序）。"""
    return sorted(model_store.installed())


def model_exists(name: str, installed: dict | None = None) -> bool:
    """判断模型是否已下载（忽略 registry 前缀，兼容省略 :latest 的写法）。"""
    return model_store.exists(name, installed=installed)


def pull_model(name: str, on_progress=None, should_stop=None) -> str:
    """流式下载模型。"""
    return model_store.download(name, on_progress=on_progress, should_stop=should_stop)


# ------------------------------------------------------------------ 模型
def ask_model(img: bytes, model=MODEL, timeout=300, prompt=None,
              max_tokens: int = 64):
    """送一张图给模型，返回 (文本, 耗时, prompt token 数, 服务端统计)。"""
    result = model_backend.describe(img, model=model, prompt=prompt or PROMPT,
                                    timeout=timeout, max_tokens=max_tokens)
    return result.text, result.elapsed_seconds, result.prompt_tokens, result.details


DESC_BAD = {T('无动作'), T('无活动'), T('无明显动作'), T('无明显活动'), T('未知'), '无', T('无法判断'),
            T('静止'), T('无内容'), T('看不到'), T('不清晰'), T('无变化')}


def parse_out(text: str) -> str:
    """从模型输出里取出描述，并硬截断到 DESC_MAX 个汉字。"""
    s = ''
    m = re.search(r'描述\s*[=:：]\s*(.+)', text)
    if m:
        s = m.group(1)
    else:
        for line in text.splitlines():
            line = re.sub(r'^\s*[-*\d.、)\s]+', '', line).strip()
            if line and '=' not in line and '：' not in line:
                s = line
                break
    s = s.strip().splitlines()[0] if s.strip() else ''
    s = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]+', '', s)
    s = s[:DESC_MAX]
    return '' if s in DESC_BAD else s


# ------------------------------------------------------------------ 地名
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
    here = os.path.dirname(os.path.abspath(__file__))
    roots.append(here)
    if getattr(sys, 'frozen', False):
        exe = os.path.dirname(os.path.abspath(sys.executable))
        roots.append(exe)
        roots.append(os.path.normpath(os.path.join(exe, '..', 'Resources')))
    return roots


def _data_paths(name: str) -> list:
    """内置数据的候选位置：打包进 exe 的临时目录 → 软件 data 目录。"""
    ps = [os.path.join(r, 'data', name) for r in _bundle_roots()]
    ps.append(os.path.join(DATA_DIR, name))
    return ps


def _load_csv(name: str) -> list:
    """读 data 目录下的 csv，返回切分好的行列表（跳过表头）。"""
    for p in _data_paths(name):
        try:
            with io.open(p, encoding='utf-8') as f:
                head = f.readline()
                if ',' not in head:
                    continue
                return [ln.rstrip('\n').split(',')
                        for ln in f if ln.count(',') >= 3]
        except Exception:
            continue
    return []


def load_districts() -> list:
    """懒加载中国县级点位库 → [[县名, 经度, 纬度, 地级市名], ...]"""
    global _DISTRICTS
    if _DISTRICTS is None:
        _DISTRICTS = []
        for r in _load_csv('districts.csv'):
            try:
                _DISTRICTS.append([r[1], float(r[4]), float(r[3]), r[2]])
            except Exception:
                continue
    return _DISTRICTS


def load_world() -> list:
    """懒加载全球城市兜底库 → [[城市名, 经度, 纬度, 国家码, 人口, 中文名], ...]

    中文名可能为空（GeoNames 的别名里没收录时），此时回退到拉丁名。
    """
    global _WORLD
    if _WORLD is None:
        _WORLD = []
        for r in _load_csv('cities_world.csv'):
            try:
                pop = int(r[4]) if len(r) > 4 else 0
            except Exception:
                pop = 0
            try:
                zh = (r[5] or '').strip() if len(r) > 5 else ''
                _WORLD.append([r[0], float(r[2]), float(r[1]), r[3], pop, zh])
            except Exception:
                continue
    return _WORLD


_WCELL = 2.0            # 全球库的空间网格边长（度），用来把"逐条扫 10 万行"缩到几百行


def _world_index() -> dict:
    """按 2° 网格给全球城市建索引 {(igrid, jgrid): [行...]}"""
    global _WINDEX
    if _WINDEX is None:
        _WINDEX = {}
        for r in load_world():
            _WINDEX.setdefault((int(r[2] // _WCELL), int(r[1] // _WCELL)),
                               []).append(r)
    return _WINDEX


CN_CORE_KM = 60.0       # 离最近县城这么近，毫无疑问在中国，直接走县级库


def _cn_mask() -> set:
    """用县级点位生成一张 0.25° 的"中国陆域"遮罩，用来把国境线附近判准。

    只把行政中心周围 ~0.6° 的格子算进来：常住人口都在这些格子附近，
    而海参崴（距最近县城 105km）这类境外点就不会被误判成中国。
    """
    global _CN_MASK
    if _CN_MASK is None:
        step, rad = 0.25, 0.6
        cells = set()
        for _nm, lng, lat, _city in load_districts():
            dlo = rad / max(0.25, math.cos(math.radians(lat)))
            la = lat - rad
            while la <= lat + rad:
                lo = lng - dlo
                while lo <= lng + dlo:
                    cells.add((int(la / step), int(lo / step)))
                    lo += step
                la += step
        _CN_MASK = cells
    return _CN_MASK


def in_china(lat: float, lon: float) -> bool:
    """两步判定是否在"中国实际范围内"（决定走县级精确库还是全球城市库）。"""
    if not (CN_LAT[0] <= lat <= CN_LAT[1] and CN_LNG[0] <= lon <= CN_LNG[1]):
        return False
    _c, _t, km = _nearest_county(lat, lon)
    if km <= CN_CORE_KM:
        return True
    return (int(lat / 0.25), int(lon / 0.25)) in _cn_mask()


def _nearest(rows: list, lat: float, lon: float) -> tuple:
    """通用最近邻，返回 (命中行, 距离km)；rows 元素形如 [名称, 经度, 纬度, 附加]"""
    if not rows:
        return None, 1e18
    coslat = math.cos(math.radians(lat))
    best, bd = None, 1e18
    for r in rows:
        dx = (lon - r[1]) * coslat
        dy = lat - r[2]
        d = dx * dx + dy * dy
        if d < bd:
            bd, best = d, r
    return best, math.sqrt(bd) * 111.0


def _nearest_county(lat: float, lon: float) -> tuple:
    """中国境内最近邻县级点位，返回 (地级市名, 县名, 距离km)。"""
    key = '%.3f,%.3f' % (round(lat, 3), round(lon, 3))
    hit = _DIST_CACHE.get(key)
    if hit is not None:
        return hit
    row, km = _nearest(load_districts(), lat, lon)
    if row is None:
        return ('', '', 0.0)
    out = (row[3], row[0], km)
    _DIST_CACHE[key] = out
    return out


def _world_candidates(lat: float, lon: float, ring: int = 1) -> list:
    """取该坐标所在 2° 网格及其相邻一圈里的城市（够近也够快）。"""
    idx = _world_index()
    gi, gj = int(lat // _WCELL), int(lon // _WCELL)
    out = []
    for i in range(gi - ring, gi + ring + 1):
        for j in range(gj - ring, gj + ring + 1):
            out.extend(idx.get((i, j), ()))
    return out


def _nearest_world(lat: float, lon: float) -> tuple:
    """中国范围外最近城市，返回 (城市名, 国家码, 距离km)。

    先在 2° 网格里找最近点，再在这个点周围放宽一点点、挑人口更多的那个：
    直接取最近的话，巴黎容易命中「Paris 04 Hotel-de-Ville」这种区级小点。
    """
    cand = _world_candidates(lat, lon)
    if not cand:                       # 海里或极地等空白网格，退全量扫描
        cand = load_world()
    row, km = _nearest(cand, lat, lon)
    if row is None:
        return ('', '', km)
    # 窗口 = 固定的 15km 或按距离放宽（底线是"城市级"精度，宁可写大城市名）
    win = max(WORLD_TIE_KM, km * 1.5 + 5.0)
    best = row
    coslat = math.cos(math.radians(lat))
    for r in cand:
        if len(r) > 4 and r[4] <= best[4]:
            continue
        d = math.hypot((lon - r[1]) * coslat, lat - r[2]) * 111.0
        if d <= win:
            best, km = r, d
    # 优先用中文名；GeoNames 没收录中文别名的城市仍显示拉丁原名
    return (best[5] or best[0], best[3], km)


# 国家码 → 中文国名（只列出常见目的地；查不到就不加前缀，仍能正常显示城市名）
_CC_ZH = {
    'AD': '安道尔', 'AE': '阿联酋', 'AF': '阿富汗', 'AL': '阿尔巴尼亚',
    'AM': '亚美尼亚', 'AR': '阿根廷', 'AT': '奥地利', 'AU': '澳大利亚',
    'AZ': '阿塞拜疆', 'BA': '波黑', 'BD': '孟加拉国', 'BE': '比利时',
    'BG': '保加利亚', 'BH': '巴林', 'BO': '玻利维亚', 'BR': '巴西',
    'BT': '不丹', 'BY': '白俄罗斯', 'CA': '加拿大', 'CH': '瑞士',
    'CL': '智利', 'CO': '哥伦比亚', 'CR': '哥斯达黎加', 'CU': '古巴',
    'CY': '塞浦路斯', 'CZ': '捷克', 'DE': '德国', 'DK': '丹麦',
    'DO': '多米尼加', 'DZ': '阿尔及利亚', 'EC': '厄瓜多尔', 'EE': '爱沙尼亚',
    'EG': '埃及', 'ES': '西班牙', 'ET': '埃塞俄比亚', 'FI': '芬兰',
    'FR': '法国', 'GB': '英国', 'GE': '格鲁吉亚', 'GR': '希腊',
    'HR': '克罗地亚', 'HU': '匈牙利', 'ID': '印度尼西亚', 'IE': '爱尔兰',
    'IL': '以色列', 'IN': '印度', 'IQ': '伊拉克', 'IR': '伊朗',
    'IS': '冰岛', 'IT': '意大利', 'JM': '牙买加', 'JO': '约旦',
    'JP': '日本', 'KE': '肯尼亚', 'KG': '吉尔吉斯斯坦', 'KH': '柬埔寨',
    'KR': '韩国', 'KW': '科威特', 'KZ': '哈萨克斯坦', 'LA': '老挝',
    'LB': '黎巴嫩', 'LK': '斯里兰卡', 'LT': '立陶宛', 'LU': '卢森堡',
    'LV': '拉脱维亚', 'MA': '摩洛哥', 'MD': '摩尔多瓦', 'ME': '黑山',
    'MK': '北马其顿', 'MM': '缅甸', 'MN': '蒙古', 'MT': '马耳他',
    'MV': '马尔代夫', 'MX': '墨西哥', 'MY': '马来西亚', 'MZ': '莫桑比克',
    'NA': '纳米比亚', 'NG': '尼日利亚', 'NL': '荷兰', 'NO': '挪威',
    'NP': '尼泊尔', 'NZ': '新西兰', 'PA': '巴拿马', 'PE': '秘鲁',
    'PH': '菲律宾', 'PK': '巴基斯坦', 'PL': '波兰', 'PT': '葡萄牙',
    'PY': '巴拉圭', 'QA': '卡塔尔', 'RO': '罗马尼亚', 'RS': '塞尔维亚',
    'RU': '俄罗斯', 'SA': '沙特', 'SE': '瑞典', 'SG': '新加坡',
    'SI': '斯洛文尼亚', 'SK': '斯洛伐克', 'SN': '塞内加尔', 'TH': '泰国',
    'TJ': '塔吉克斯坦', 'TM': '土库曼斯坦', 'TN': '突尼斯', 'TR': '土耳其',
    'TT': '特立尼达', 'TW': '', 'TZ': '坦桑尼亚', 'UA': '乌克兰',
    'US': '美国', 'UY': '乌拉圭', 'UZ': '乌兹别克斯坦', 'VE': '委内瑞拉',
    'VN': '越南', 'ZA': '南非', 'ZM': '赞比亚', 'ZW': '津巴布韦',
    'CI': '科特迪瓦', 'AO': '安哥拉', 'HN': '洪都拉斯', 'PS': '巴勒斯坦',
    # 以下为中国领土，按惯例不加国名前缀
    'CN': '', 'HK': '', 'MO': '',
}


def _world_place(nm: str, cc: str) -> str:
    """国外地名：国名 + 城市名。港澳台与大陆一样不加前缀。"""
    cc = (cc or '').upper()
    pre = _CC_ZH.get(cc, '') if WORLD_WITH_COUNTRY else ''
    # 新加坡、摩纳哥这类城市国家：国名已等于（或包含于）城市名，不再叠加前缀
    if pre and (pre == nm or nm.startswith(pre)):
        return nm
    return pre + nm


_ADMIN_SUF = re.compile(T('(街道|镇|乡|办事处)$'))


def _short_place(name: str) -> str:
    """去掉地名末尾的行政级别词（街道/镇/乡/办事处）。"""
    s = _ADMIN_SUF.sub('', name or '')
    return s or (name or '')


def district_of(lat: float, lon: float) -> tuple:
    """离线最近邻取地名，返回 (地名, 距离km)。

    两步路由，全程离线、没有任何在线反查：
      * 在中国经纬度窗口内 → 县级点位库：40km 内命中县城标「市 + 县」
        （如「成都郫都区」「成都彭州」），40~200km 退化为「成都」，更远返回空串；
      * 窗口外 → 全球城市库（GeoNames cities1000）：输出「中文国名 + 城市名」
        （如「日本 Tokyo」），超过 WORLD_MAX_KM 返回空串。
    """
    if in_china(lat, lon):
        city, county, km = _nearest_county(lat, lon)
        if not city:
            # 窗口内但县级库没覆盖到（港澳台等）→ 退回全球城市库
            nm, cc, kmw = _nearest_world(lat, lon)
            return (_world_place(nm, cc), kmw) \
                if nm and kmw <= WORLD_MAX_KM else ('', kmw)
        if km <= DIST_NEAR_KM:
            return _short_place(city + county), km
        if km <= DIST_FAR_KM:
            return city, km
        return '', km
    nm, cc, km = _nearest_world(lat, lon)
    if not nm or km > WORLD_MAX_KM:
        return '', km
    return _world_place(nm, cc), km


def city_of(lat: float, lon: float) -> tuple:
    """只要城市级地名，返回 (地名, 距离km)；超出阈值返回空串。"""
    if in_china(lat, lon):
        city, _cnty, km = _nearest_county(lat, lon)
        if not city or km > DIST_FAR_KM:
            return '', km
        return city, km
    nm, cc, km = _nearest_world(lat, lon)
    if not nm or km > WORLD_MAX_KM:
        return '', km
    return _world_place(nm, cc), km


def parse_dt(creation: str):
    """解析 ffprobe 的 creation_time，返回本地时间 datetime 或 None。"""
    if not creation:
        return None
    try:
        dt = datetime.fromisoformat(creation.replace('Z', '+00:00'))
        if dt.tzinfo:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        return None


# 手机视频文件名里常见的拍摄时间：VID20250223175532 / TG-2025-07-28-170425759 / 20250126_xxx
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


# ---------------------------------------------------------------- 相邻照片
# 手机照片同样带 EXIF GPS。视频没定位时，用时间上最接近的照片来补。
PHOTO_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.dng', '.tif', '.tiff')
_PHOTO_CACHE = None
_PHOTO_CACHE_FILE = 'photo_gps_cache.json'


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


# ---------------------------------------------------------------- 手工时间
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


# ------------------------------------------------------------------ 批量流程
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


def list_videos(directory: str) -> list[str]:
    return [f for f in sorted(os.listdir(directory))
            if os.path.isfile(os.path.join(directory, f)) and f.lower().endswith(VID_EXT)]


def targets_of(directory: str, all_files=False, limit=0) -> list[str]:
    files = list_videos(directory)
    t = files if all_files else [f for f in files if not ALREADY_NAMED.match(f)]
    if limit:
        t = t[:limit]
    return t


def warmup(model=MODEL) -> tuple[bool, str]:
    """热身：把模型与视觉编码器载进显存，避免第一条承担加载时间。"""
    return model_backend.load(model)


NAME_MAX = 80       # 文件名（不含扩展名）的长度上限


def compose_name(ts: str, place: str, desc: str, old_name: str = '',
                 kind: str = '', place_exact: bool = False) -> str:
    """统一的文件名规则：`视20241220成都肖家河_剥洋葱[_log]`。

    这是全软件唯一的命名规则，界面里改完时间/地点/描述后也走这里预览新名字。
      * 前缀：视频 `视`、照片 `图`（不传 kind 时按扩展名判断）；
      * 时间只写到「天」（ts 里的时:分不写进名字），同一天靠描述区分，
        描述也撞车时由改名环节自动补回时刻 `_时分`（再撞才 `_2`/`_3`）；
      * 地名与描述之间用 `_`；
      * 哪一段没有就省掉哪一段；原文件名里的 `_log` 之类的多余尾巴一律不带进来。
    """
    stem = os.path.splitext(old_name or '')[0]
    date = re.sub(r'\D', '', str(ts or ''))[:8]
    # 在线精确定位给的地名（如「成都郫都区郫筒街道」）已经是最终形态，
    # 不再过 _short_place 削后缀——削掉「街道」反而看不出是哪一级
    place = place if (place and place_exact) else (_short_place(place) if place else '')
    if not kind:
        ext = os.path.splitext(old_name or '')[1].lower()
        kind = 'photo' if ext in PHOTO_EXT else 'video'
    head = date + (place or '')
    pre = '图' if kind == 'photo' else '视'

    def build(d: str) -> str:
        body = '_'.join([p for p in (head, d) if p])
        return re.sub(r'[\\/:*?"<>|]', '', pre + body) if body else ''

    raw = build(desc)
    if len(raw) <= NAME_MAX:
        return raw
    # 超长时先砍描述、地名尽量保住（地址比「几个字的描述」更值钱）：
    # 描述砍到 4 字还剩不下，才允许动地名的尾巴。
    over = len(raw) - NAME_MAX
    if desc:
        keep = len(desc) - over
        if keep >= 4:
            return build(desc[:keep])[:NAME_MAX]
        short = build(desc[:4])
        if len(short) <= NAME_MAX:
            return short
        return short[:NAME_MAX]
    return raw[:NAME_MAX]


def name_from_item(it: dict) -> str:
    """按计划项（含用户改过的时间/地点/描述）算出完整新文件名。"""
    old = it.get('old') or ''
    stem = compose_name(str(it.get('time') or '').strip(),
                        str(it.get('place') or '').strip(),
                        str(it.get('desc') or '').strip(),
                        old, kind=it.get('kind') or '',
                        place_exact=bool(it.get('place_exact')))
    return (stem + os.path.splitext(old)[1]) if stem else ''


def place_of(gps, net_geo: bool = False, key: str = '',
             level: str = 'district') -> tuple:
    """地名：离线库打底，联网精确定位（可选）覆盖成更下一级。

    返回 (place, addr, km, src, exact)：
      place  文件名里那段短地名
      addr   完整地址（写进长描述 / .ai.txt / 目录说明）
      km     离线命中县城的距离（在线不改它）
      src    'offline' / 'amap' / 'nominatim'
      exact  True = 在线地名，别再过 _short_place 削后缀
    在线这条路无论出什么状况（无网、无 Key、报错、空结果）都自动回落到离线结果。
    """
    place, km = ('', 0.0)
    addr, src, exact = '', 'offline', False
    if gps:
        try:
            place, km = district_of(gps[0], gps[1])
        except Exception:
            place, km = ('', 0.0)
        addr = place or ''
    if net_geo and gps:
        try:
            r = geo.online_place(gps[0], gps[1], key, level,
                                 in_china=in_china(gps[0], gps[1]))
        except Exception:
            r = None
        if r and r.get('place'):
            place = r['place']
            addr = r.get('addr') or place
            src = r.get('src') or 'online'
            exact = True
    return place, addr, km, src, exact


def rename_one(path: str, model=MODEL, frames=0, meta=None,
               net_geo: bool = False, amap_key: str = '',
               geo_level: str = 'district', want_long: bool = False,
               ai_desc: bool = True) -> dict:
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
            content, t_m, ptok, _ = ask_model(
                grid, model=model, max_tokens=520,
                prompt=PROMPT_FULL.format(t=when or T('时间不明'),
                                          p=addr or place or T('地点不明')))
            desc, long, kw = parse_full(content)
            long = fill_long(long, desc, when, addr or place)
        else:
            content, t_m, ptok, _ = ask_model(grid, model=model)
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


# ------------------------------------------------------------------ 照片
# 照片走「快路径」：只读 EXIF（时间 + GPS），不调模型。几千张照片也就几秒。
# 需要给照片也写内容描述时，才把单张照片送去模型（每张约 11 秒，很贵）。
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


def rename_one_photo(path: str, model=MODEL, describe=False, meta=None,
                     net_geo: bool = False, amap_key: str = '',
                     geo_level: str = 'district',
                     want_long: bool = False) -> dict:
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
                content, t_m, ptok, _ = ask_model(
                    raw_img, model=model, max_tokens=520,
                    prompt=PHOTO_PROMPT_FULL.format(
                        t=when or T('时间不明'), p=addr or place or T('地点不明')))
                desc, long, kw = parse_full(content)
                long = fill_long(long, desc, when, addr or place)
            else:
                content, t_m, ptok, _ = ask_model(raw_img, model=model,
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


# ------------------------------------------------------------------ 计划表
# 「先跑完、再统一改」：识别阶段只产出计划表（不动文件名），
# 用户在界面里把时间/地点/描述改好，再一次性执行改名。
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
              should_stop=None, on_phase=None, state_dir=None):
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
        if not start_server(on_log=log):
            log(T('!! 推理服务不可用（自带运行时启动失败），任务终止'))
            rep.close(); logf.close()
            return {'total': 0, 'done': 0, 'failed': 0, 'stopped': True, 'elapsed': 0.0,
                    'plan': [], 'plan_path': ''}

        phase(T('模型热身'))
        ok, err = warmup(cfg.model)
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
                                     want_long=cfg.want_long and desc_photos)
            else:
                r = rename_one(p, model=cfg.model, frames=cfg.frames, meta=meta,
                               net_geo=cfg.net_geo, amap_key=cfg.amap_key,
                               geo_level=cfg.geo_level, want_long=cfg.want_long,
                               ai_desc=bool(getattr(cfg, 'ai_desc', True)))
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


# ------------------------------------------------------------------ 命令行入口
if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=T('照片/视频 AI 改名引擎（无界面）'))
    ap.add_argument('--dir', required=True, help=T('要处理的目录'))
    ap.add_argument('--apply', action='store_true', help=T('识别完直接改名（默认只出计划表）'))
    ap.add_argument('--limit', type=int, default=0, help=T('最多处理几个，0=全部'))
    ap.add_argument('--frames', type=int, default=0, help=T('固定抽帧数，0=按时长自适应'))
    ap.add_argument('--all', action='store_true', help=T('连已命名的文件也重做'))
    ap.add_argument('--model', default=MODEL, help=T('使用的模型'))
    ap.add_argument('--undo', action='store_true', help=T('按台账回滚'))
    ap.add_argument('--photos', action='store_true', help=T('连目录里的照片一起处理'))
    ap.add_argument('--no-video', action='store_true', help=T('只处理照片，不动视频'))
    ap.add_argument('--describe-photos', action='store_true',
                    help=T('照片也调模型写描述（每张约 11 秒，很慢）'))
    ap.add_argument('--apply-plan', action='store_true',
                    help=T('按上次识别出的计划表直接改名（表格里改过的结果也走这个）'))
    a = ap.parse_args()
    if a.undo:
        rollback(a.dir, on_log=lambda s: print(s, flush=True))
    elif a.apply_plan:
        items = load_plan(a.dir)
        if not items:
            print(T('没有找到计划表，先跑一次不带 --apply-plan 的识别。'))
        else:
            print(apply_plan(a.dir, items, on_log=lambda s: print(s, flush=True)))
    else:
        c = Config(a.dir, apply=a.apply, frames=a.frames, limit=a.limit,
                   all_files=a.all, model=a.model,
                   videos=not a.no_video,
                   photos=a.photos, describe_photos=a.describe_photos)
        st = run_batch(c, on_log=lambda s: print(s, flush=True))
        print({k: v for k, v in st.items() if k != 'plan'})
        print(T('计划表：%s') % st.get('plan_path', ''))
