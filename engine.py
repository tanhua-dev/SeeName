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
from inference.llama_backend import LlamaBackend
from inference.local_models import LocalModelStore
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
    CRE,
    find_ffmpeg,
    FF,
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


# Importing the engine does not import the native binding or load model weights.
model_store: ModelStore = LocalModelStore(
    app_dir=APP_DIR, user_data=USER_DATA, config=lambda: config(),
    save_config=lambda **kw: save_config(**kw))
model_backend: InferenceBackend = LlamaBackend(model_store, context_size=CTX)


def models_dir(create=True) -> str:
    """本地 GGUF 模型库目录，不复用旧 Ollama blob 目录配置。"""
    return model_store.directory(create=create)


def runtime_status() -> dict:
    """本地推理状态；轮询不会等待模型操作。"""
    return model_backend.diagnostics()


def start_server(on_log=None, timeout=120) -> bool:
    """兼容入口：初始化进程内推理库，不启动服务或子进程。"""
    return model_backend.start(on_log=on_log, timeout=timeout)


def shutdown_server():
    """兼容入口：中止正在进行的推理并释放本进程拥有的模型。"""
    model_backend.close()

atexit.register(shutdown_server)


def set_models_dir(path, on_log=None) -> bool:
    """切换 GGUF 模型库目录并释放旧模型，不移动权重文件。"""
    return model_store.set_directory(path, on_log=on_log)


def server_info() -> dict:
    """兼容入口：已初始化状态及加载模型的文件大小。"""
    status = model_backend.status()
    return {'up': status.available, 'models': [
        {'name': model.name, 'size': model.size_bytes, 'vram': model.device_bytes}
        for model in status.models]}


MODEL_CATALOG = model_store.catalog


def installed_models(retry: int = 2) -> dict:
    """返回配套模型 {标识: 主模型与视觉投影文件总字节数}。"""
    return model_store.installed(retry=retry)


def list_models() -> list:
    """本机已下载的模型名（升序）。"""
    return sorted(model_store.installed())


def model_exists(name: str, installed: dict | None = None) -> bool:
    """判断指定本地模型是否具备配套权重。"""
    return model_store.exists(name, installed=installed)


def model_problems() -> dict:
    return model_store.problems()


def selected_model() -> str:
    """Old Ollama identifiers fall back to the native default, without rewriting config."""
    name = str(config().get('model') or '')
    return name if any(item['name'] == name for item in MODEL_CATALOG) else MODEL


def select_model(name: str):
    if not any(item['name'] == name for item in MODEL_CATALOG):
        raise ValueError(T('不支持的模型：%s') % name)
    if name != selected_model() or any(m.name != name for m in model_backend.status().models):
        model_backend.close()
    save_config(model=name)


def ask_model(img: bytes, model=MODEL, timeout=300, prompt=None,
              max_tokens: int = 64):
    """送一张图给模型，返回 (文本, 耗时, prompt token 数, 服务端统计)。"""
    result = model_backend.describe(img, model=model, prompt=prompt or PROMPT,
                                    timeout=timeout, max_tokens=max_tokens)
    return result.text, result.elapsed_seconds, result.prompt_tokens, result.details


def warmup(model=MODEL) -> tuple[bool, str]:
    """加载主模型；视觉编码器由绑定在首次识图时初始化。"""
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
                    help=T('照片也调用模型生成描述（CPU 耗时较长）'))
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
