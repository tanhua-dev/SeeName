"""Discover supported Qwen3-VL weight/projector pairs without loading native code."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
import threading
from typing import Callable

from strings import T
from .downloads import DOWNLOADS, download_pair


DEFAULT_MODEL = 'Qwen3VL-4B-Instruct-Q4_K_M'
MODEL_NAMES = (DEFAULT_MODEL, 'Qwen3VL-4B-Instruct-Q8_0', 'Qwen3VL-4B-Instruct-F16',
               'Qwen3VL-2B-Instruct-Q4_K_M', 'Qwen3VL-2B-Instruct-Q8_0')
PROJECTORS = ('mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf',
              'mmproj-Qwen3VL-4B-Instruct-F16.gguf')


def _user_base() -> str:
    """系统给本用户的软件数据根目录（模型、组件装不进口录时退回这里）。"""
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~/AppData/Local')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
        if not os.path.isabs(base):
            base = os.path.expanduser('~/.local/share')
    return base


def _writable(path: str) -> bool:
    """目录能不能真的写进去（只读的安装目录要能识别出来）。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, '.write_probe')
        with open(probe, 'wb') as stream:
            stream.write(b'1')
        os.remove(probe)
        return True
    except OSError:
        return False



@dataclass(frozen=True)
class ModelFiles:
    name: str
    model: Path
    projector: Path

    @property
    def size_bytes(self) -> int:
        return self.model.stat().st_size + self.projector.stat().st_size


class LocalModelStore:
    """A local library of known Instruct models; weights stay in their directories.

    Only a main model with a matching projector in the same directory is usable.
    Search the selected directory and its immediate subdirectories. Ambiguous
    duplicate pairs are rejected; selecting their specific directory resolves it.
    The shared lock serializes directory changes with native model operations.
    """

    def __init__(self, *, app_dir: str, user_data: str,
                 config: Callable[[], dict], save_config: Callable[..., dict]):
        self.app_dir = app_dir
        self.user_data = user_data
        self.config = config
        self.save_config = save_config
        self.lock = threading.RLock()
        self._download_lock = threading.Lock()
        self.on_change: Callable[[], None] = lambda: None

    @property
    def catalog(self) -> list[dict[str, str]]:
        return [dict(name=name, size='', tag='Q4 / CPU' if name == DEFAULT_MODEL else '',
                     downloadable=name in DOWNLOADS,
                     note=T('主模型和同目录的 mmproj 视觉投影文件必须配套存在。'))
                for name in MODEL_NAMES]

    def directory(self, create: bool = True) -> str:
        # Keep legacy Ollama models_dir separate: those blob files are not GGUF pairs.
        value = self.config().get('gguf_models_dir')
        if not value:
            # 解压即用：模型默认放在软件目录下的 models/，整个文件夹拷走就能用，
            # 不往 C 盘用户目录里另存一份。软件目录写不进去（装在 Program Files、
            # 或 mac 的 .app 里）才退回系统用户目录。
            value = os.path.join(self.app_dir, 'models')
            if not _writable(value):
                value = os.path.join(_user_base(), 'SeeName', 'models')
        value = os.path.abspath(os.path.expanduser(str(value)))
        if create:
            os.makedirs(value, exist_ok=True)
        return value

    @staticmethod
    def _valid_file(path: Path) -> bool:
        try:
            with path.open('rb') as file:
                return file.read(4) == b'GGUF' and path.stat().st_size > 4
        except OSError:
            return False

    def _find(self, name: str) -> tuple[list[ModelFiles], str]:
        if name not in MODEL_NAMES:
            return [], T('不支持的模型：%s') % name
        roots = [Path(self.directory(create=False))]
        # 便携模式下 directory() 本身就是 <软件目录>/models，这两个可能重合；
        # 只在真的不是同一个目录时才追加第二个搜索根，免得同一个文件被当成两份。
        portable = Path(self.app_dir) / 'models'
        if not self.config().get('gguf_models_dir') and portable.resolve() not in {
                Path(r).resolve() for r in roots}:
            roots.append(portable)
        directories = []
        for root in roots:
            try:
                directories.extend([root] + sorted(p for p in root.iterdir() if p.is_dir()))
            except OSError:
                continue
        pairs = []
        seen = set()
        for directory in directories:
            model = directory / (name + '.gguf')
            if not self._valid_file(model):
                continue
            projectors = tuple(p.replace('4B', '2B') for p in PROJECTORS) if '-2B-' in name else PROJECTORS
            for filename in projectors:
                projector = directory / filename
                if self._valid_file(projector):
                    key = (model.resolve(), projector.resolve())
                    if key not in seen:
                        seen.add(key)
                        pairs.append(ModelFiles(name, model, projector))
                    break
        if len(pairs) > 1:
            return [], T('存在多个同名模型，请选择具体的模型目录：%s') % name
        return pairs, '' if pairs else T('缺少主模型或配套的 mmproj 文件：%s') % name

    def resolve(self, name: str) -> ModelFiles:
        pairs, error = self._find(name)
        if not pairs:
            raise ValueError(error)
        return pairs[0]

    def problems(self) -> dict[str, str]:
        return {name: error for name in MODEL_NAMES if (error := self._find(name)[1])}

    def installed(self, retry: int = 2) -> dict[str, int]:
        found = {}
        for name in MODEL_NAMES:
            try:
                found[name] = self.resolve(name).size_bytes
            except (OSError, ValueError):
                pass
        return found

    def exists(self, name: str, installed: dict | None = None) -> bool:
        return name in (self.installed() if installed is None else installed)

    def set_directory(self, path: str, on_log=None) -> bool:
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            if on_log:
                on_log(T('模型目录不存在或无法读取：%s') % path)
            return False
        with self.lock:
            self.on_change()
            self.save_config(gguf_models_dir=path)
        if on_log:
            on_log(T('模型库已切换到 %s') % path)
        return True

    def download(self, name, on_progress=None, should_stop=None) -> str:
        if name not in DOWNLOADS:
            raise ValueError(T('该模型仅支持手动导入。'))
        with self._download_lock, self.lock:
            return download_pair(name, self.directory(), self.source(), on_progress, should_stop)

    def source(self) -> str:
        source = self.config().get('gguf_download_source', 'huggingface')
        return source if source in ('huggingface', 'modelscope') else 'huggingface'

    def set_source(self, url: str) -> str:
        if url not in ('huggingface', 'modelscope'):
            raise ValueError('Unknown download source: ' + url)
        self.save_config(gguf_download_source=url)
        return url
