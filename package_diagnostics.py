"""Frozen-package acceptance checks shared by local builds and release CI.

Invoked explicitly via --package-check. Results and temporary fixtures are
written beside the requested result JSON, never into the application directory.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import traceback


def _native_modules() -> list[str]:
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    handle = kernel.GetCurrentProcess()
    modules = (wintypes.HMODULE * 2048)()
    needed = wintypes.DWORD()
    psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD)]
    psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE,
                                         wintypes.LPWSTR, wintypes.DWORD]
    if not psapi.EnumProcessModules(handle, modules, ctypes.sizeof(modules), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    if needed.value > ctypes.sizeof(modules):
        raise RuntimeError('Native module inventory exceeded capacity')
    paths = []
    for module in modules[:needed.value // ctypes.sizeof(wintypes.HMODULE)]:
        buffer = ctypes.create_unicode_buffer(32768)
        if not psapi.GetModuleFileNameExW(handle, module, buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        paths.append(buffer.value)
    return sorted(set(paths))


def run_package_check(result_path: str, model_directory: str = '', vulkan_directory: str = '',
                      cuda_directory: str = '') -> int:
    result = Path(result_path).resolve()
    result.parent.mkdir(parents=True, exist_ok=True)
    report = dict(success=False, stage='starting', frozen=bool(getattr(sys, 'frozen', False)),
                  version=getattr(sys, '_seename_version', ''),
                  metadata_writing_enabled=None)

    def checkpoint(stage):
        report['stage'] = stage
        result.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    checkpoint('importing application modules')
    import engine
    from core import settings
    from core.features import metadata_writing_enabled

    report['metadata_writing_enabled'] = metadata_writing_enabled()
    checkpoint('checking build capabilities')
    old_config = settings._CFG
    old_vulkan_root = engine.vulkan_component.root
    old_cuda_root = engine.cuda_component.root
    old_save, old_settings_save = engine.save_config, settings.save_config
    root = None
    try:
        if not report['frozen'] or metadata_writing_enabled():
            raise RuntimeError('Expected a frozen build with metadata writing disabled')
        # Enforce the no-helper-process property during application checks too.
        import subprocess
        import socket

        def forbidden(*args, **kwargs):
            raise AssertionError('Package checks must not launch processes or connect to a network')

        old_popen, old_connect = subprocess.Popen, socket.socket.connect
        subprocess.Popen = socket.socket.connect = forbidden
        try:
            with tempfile.TemporaryDirectory(prefix='package-check-', dir=result.parent) as temporary:
                work = Path(temporary)
                models = str(Path(model_directory).resolve()) if model_directory else str(work/'models')
                cfg = dict(write_meta=True, write_txt=False, write_md=False, help_autoshow=False,
                           model=engine.MODEL, gguf_models_dir=models,
                           vulkan_enabled=bool(vulkan_directory), cuda_enabled=bool(cuda_directory))
                if vulkan_directory:
                    engine.vulkan_component.root = Path(vulkan_directory).resolve()
                if cuda_directory:
                    engine.cuda_component.root = Path(cuda_directory).resolve()
                settings._CFG = cfg
                engine.save_config = settings.save_config = lambda **kw: cfg.update(kw) or cfg
                report['config_override_blocked'] = not engine.Config(str(work), write_meta=True).write_meta
                if not report['config_override_blocked']:
                    raise AssertionError('A saved setting re-enabled metadata writes')

                import av
                from PIL import Image, ImageDraw
                import meta
                from ui.app import App
                from ui.common import asset_path
                import tkinter as tk

                checkpoint('checking bundled resources and video fixture')
                if not engine.load_districts() or not engine.load_world():
                    raise AssertionError('Bundled place databases are missing')
                for name in ('logo.png', 'logo_head.png', 'logo_round.png'):
                    if not Path(asset_path(name)).is_file():
                        raise AssertionError('Bundled image missing: ' + name)
                for name in ('help_zh.md', 'help_en.md', 'NOTICE.md'):
                    if not any((Path(base)/name).is_file() for base in engine._bundle_roots()):
                        raise AssertionError('Bundled document missing: ' + name)
                report['resources'] = True

                source = work/'fixture.mp4'
                image = Image.new('RGB', (160, 96), 'white')
                ImageDraw.Draw(image).rectangle((30, 15, 130, 85), fill='red')
                with av.open(str(source), 'w', options={'movflags': 'use_metadata_tags'}) as container:
                    container.metadata.update(creation_time='2024-12-20T12:30:00Z',
                                              location='+30.65+104.06/')
                    video = container.add_stream('mpeg4', rate=4)
                    video.width, video.height, video.pix_fmt = 160, 96, 'yuv420p'
                    for _ in range(8):
                        for packet in video.encode(av.VideoFrame.from_image(image)):
                            container.mux(packet)
                    for packet in video.encode():
                        container.mux(packet)
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                info = engine.probe(str(source))
                frames = engine.extract_seek(str(source), [0, .5, 1.5], 160)
                if len(frames) != 3 or info['gps'] != (30.65, 104.06):
                    raise AssertionError('Frozen PyAV probing/extraction failed')
                checkpoint('checking local inference library and Vulkan discovery')
                ok, message = meta.write_meta(str(source), 'title', 'description', 'keywords')
                if ok or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                    raise AssertionError('The build allowed a metadata write')
                report['media'] = dict(version=av.__version__, duration=info['dur'],
                                       frames=len(frames), metadata_write_blocked=True)

                if not engine.start_server():
                    raise RuntimeError(engine.runtime_status()['error'])
                import llama_cpp
                if not vulkan_directory and not cuda_directory and llama_cpp.llama_supports_gpu_offload():
                    raise RuntimeError('The CPU build must use a CPU-only inference binding')
                if vulkan_directory:
                    if not engine.vulkan_component.devices or engine.vulkan_component.error:
                        raise RuntimeError('Vulkan package check failed: ' + engine.vulkan_component.error)
                    report['vulkan_devices'] = engine.vulkan_component.devices
                if cuda_directory:
                    if not engine.cuda_component.devices or engine.cuda_component.error:
                        raise RuntimeError('CUDA package check failed: ' + engine.cuda_component.error)
                    report['cuda_devices'] = engine.cuda_component.devices
                llama_cpp.llama_model_default_params()
                llama_cpp.llama_context_default_params()
                report['native_inference_library'] = True

                checkpoint('creating hidden Tk interface')
                root = tk.Tk()
                root.withdraw()
                callback_errors = []
                root.report_callback_exception = lambda kind, error, tb: callback_errors.append(str(error))
                app = App(root)
                root.update_idletasks()
                if app.var_meta.get() or 'disabled' not in app.cb_meta.state() or callback_errors:
                    raise AssertionError('Frozen GUI did not enforce the build capability')
                report['gui'] = True
                root.destroy()
                root = None
                checkpoint('checking native module inventory')

                if model_directory:
                    available = engine.installed_models()
                    if not available:
                        raise AssertionError('No complete model pair in the verification directory')
                    check_model = next(iter(available))
                    buffer = io.BytesIO()
                    image.save(buffer, 'JPEG')
                    text, seconds, _, _ = engine.ask_model(
                        buffer.getvalue(), model=check_model,
                        prompt='请说出图片中央方形的颜色，只输出颜色。')
                    if '红' not in text and 'red' not in text.lower():
                        raise AssertionError('Frozen vision result did not match the red-square fixture: ' + text)
                    report['model_inference'] = dict(text=text, seconds=seconds, device=engine.runtime_status()['device'])
                report['native_modules'] = _native_modules()
                report['success'] = True
                report['stage'] = 'complete'
        finally:
            subprocess.Popen, socket.socket.connect = old_popen, old_connect
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        engine.shutdown_server()
        if root is not None:
            root.destroy()
        settings._CFG = old_config
        engine.vulkan_component.root = old_vulkan_root
        engine.cuda_component.root = old_cuda_root
        engine.save_config, settings.save_config = old_save, old_settings_save
        result.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['success'] else 1
