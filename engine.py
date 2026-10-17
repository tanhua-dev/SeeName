# -*- coding: utf-8 -*-
"""照片/视频改名引擎的兼容入口及命令行入口，无 GUI 依赖。

业务实现位于 core，推理实现位于 inference。这里组装共享模型实例，
保留界面使用的函数名和参数，并在调用识别流程时传入当前模型实例。
core 和 inference 不反向导入本模块。
"""
from __future__ import annotations

import atexit
from strings import T
from inference import InferenceBackend, ModelStore
from inference.ollama import OllamaBackend, OllamaModelStore, OllamaRuntime
from core import workflow as _workflow
from core.defaults import (
    MODEL,
    DEFAULT_PORT,
    CTX,
    KEEP_ALIVE,
    PIX_BUDGET,
    GUTTER,
    CAND_WIDTH,
    VID_EXT,
    PHOTO_EXT,
    ALREADY_NAMED,
    PRESETS,
)
from core.media import (
    JPG_SOI,
    JPG_EOI,
    CRE,
    find_ffmpeg,
    FF,
    ffmpeg_sync_args,
    run_ff,
    split_jpegs,
    probe,
    extract_keyframes,
    extract_seek,
    score_frame,
    pick_frames,
    grid_layout,
    compose,
    scan_meta,
    find_no_time,
    list_videos,
    targets_of,
    list_photos,
    photo_meta,
    _photo_for_model,
    _photo_exif,
)
from core.naming import (
    PROMPT,
    PHOTO_PROMPT,
    FULL_TAIL,
    PROMPT_FULL,
    PHOTO_PROMPT_FULL,
    LONG_MAX,
    LONG_MIN,
    KW_MAX,
    DESC_MAX,
    time_cn,
    parse_full,
    fill_long,
    DESC_BAD,
    parse_out,
    NAME_MAX,
    compose_name,
    name_from_item,
)
from core.paths import (
    IS_MAC,
    IS_WIN,
    APP_NAME,
    APP_DIR,
    USER_DATA,
    DATA_DIR,
    RUNTIME_DIR,
    CONFIG_PATH,
    _bundle_roots,
    _app_dir,
    _user_data_dir,
)
from core.photo_refs import (
    GPS_NEAR_H,
    GPS_FAR_H,
    default_photo_dirs,
    scan_photo_refs,
    fill_missing_gps,
)
from core.places import (
    CN_LAT,
    CN_LNG,
    DIST_NEAR_KM,
    DIST_FAR_KM,
    WORLD_TIE_KM,
    WORLD_MAX_KM,
    WORLD_WITH_COUNTRY,
    load_districts,
    load_world,
    CN_CORE_KM,
    in_china,
    district_of,
    city_of,
    place_of,
    _short_place,
)
from core.plans import (
    state_paths,
    load_json,
    save_json,
    manual_times_path,
    load_manual_times,
    save_manual_times,
    plan_path,
    save_plan,
    load_plan,
    item_of,
    apply_plan,
    rollback,
)
from core.settings import (
    config,
    save_config,
)
from core.timestamps import (
    parse_dt,
    dt_from_name,
    parse_manual_time,
)
from core.workflow import (
    Config,
    finalize,
)


# Runtime ownership remains here so shutdown and backend replacement use the
# same instances as the GUI and batch entry points.
_model_runtime = OllamaRuntime(
    app_dir=APP_DIR, user_data=USER_DATA, config=lambda: config(),
    save_config=lambda **kw: save_config(**kw), default_port=DEFAULT_PORT,
    keep_alive=KEEP_ALIVE)


model_backend: InferenceBackend = OllamaBackend(
    _model_runtime, warmup_prompt=PROMPT, context_size=CTX)


_ollama_model_store = OllamaModelStore(_model_runtime)


model_store: ModelStore = _ollama_model_store


def find_runtime():
    """定位推理运行时，返回 (exe, source)，source = 'bundled' | 'system'。"""
    return _model_runtime.find_runtime()


def find_ollama() -> str:
    """兼容旧调用：只返回路径。"""
    return find_runtime()[0]


def models_dir(create=True) -> str:
    """模型库目录。默认就是软件自己的 models 文件夹（模型包下载到这里）。"""
    return model_store.directory(create=create)


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


def server_info() -> dict:
    """查询服务与显存占用（/api/ps）。"""
    status = model_backend.status()
    return {'up': status.available, 'models': [
        {'name': model.name, 'size': model.size_bytes, 'vram': model.device_bytes}
        for model in status.models]}


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


def ask_model(img: bytes, model=MODEL, timeout=300, prompt=None,
              max_tokens: int = 64):
    """送一张图给模型，返回 (文本, 耗时, prompt token 数, 服务端统计)。"""
    result = model_backend.describe(img, model=model, prompt=prompt or PROMPT,
                                    timeout=timeout, max_tokens=max_tokens)
    return result.text, result.elapsed_seconds, result.prompt_tokens, result.details


def warmup(model=MODEL) -> tuple[bool, str]:
    """热身：把模型与视觉编码器载进显存，避免第一条承担加载时间。"""
    return model_backend.load(model)


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
    return _workflow.rename_one(
        path=path,
        model=model,
        frames=frames,
        meta=meta,
        net_geo=net_geo,
        amap_key=amap_key,
        geo_level=geo_level,
        want_long=want_long,
        ai_desc=ai_desc,
        backend=model_backend)


def rename_one_photo(path: str, model=MODEL, describe=False, meta=None,
                     net_geo: bool = False, amap_key: str = '',
                     geo_level: str = 'district',
                     want_long: bool = False) -> dict:
    """处理单张照片，返回与 rename_one 同构的结果 dict（不落盘改名）。"""
    return _workflow.rename_one_photo(
        path=path,
        model=model,
        describe=describe,
        meta=meta,
        net_geo=net_geo,
        amap_key=amap_key,
        geo_level=geo_level,
        want_long=want_long,
        backend=model_backend)


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
    return _workflow.run_batch(
        cfg=cfg,
        on_log=on_log,
        on_item=on_item,
        on_progress=on_progress,
        should_stop=should_stop,
        on_phase=on_phase,
        state_dir=state_dir,
        backend=model_backend,
        runtime_status=runtime_status)


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
