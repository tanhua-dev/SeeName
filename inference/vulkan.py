"""Versioned optional Vulkan plugin: trusted catalog, verified install and loading.

Only ggml-vulkan.dll is added to the existing native runtime. Never replace a
loaded llama/ggml DLL. A catalog is bundled at build time, not trusted from the
download server; its native-runtime fingerprints must match this application.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import threading
from urllib.request import Request, urlopen
import uuid
import zipfile

from .downloads import DownloadCancelled

BINDING_COMMIT = 'ce261d12b5810fb4c6e695efc85d5e8cf23f0630'
LLAMA_COMMIT = '27c7a769da1671c97b2e3f6cb88704a6e1e0d0cd'
NATIVE_FILES = ('ggml-base.dll', 'ggml.dll', 'ggml-cpu.dll', 'llama.dll', 'mtmd.dll')


def digest(path):
    sha = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def runtime_directory():
    spec = importlib.util.find_spec('llama_cpp')
    if not spec or not spec.origin:
        raise RuntimeError('llama-cpp-python is not installed')
    return Path(spec.origin).parent / 'lib'


def default_root():
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~/AppData/Local')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
        if not os.path.isabs(base):
            base = os.path.expanduser('~/.local/share')
    return Path(base) / 'SeeName' / 'backends'


class VulkanComponent:
    def __init__(self, config, save_config, *, catalog=None, root=None):
        self.config, self.save_config = config, save_config
        self.root = Path(root) if root else default_root()
        self.catalog = Path(catalog) if catalog else Path(__file__).with_name('vulkan-catalog.json')
        self._lock = threading.RLock()
        self._reg = None
        self._loaded_id = ''
        self._dll_directory = None
        self.devices = []
        self.error = ''

    def _entry(self):
        if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
            raise RuntimeError('This Vulkan component currently supports Windows x64 only.')
        data = json.loads(self.catalog.read_text(encoding='utf-8'))
        if data.get('schema') != 1:
            raise ValueError('Unsupported Vulkan catalog schema')
        entry = data.get('windows-x64')
        if not entry:
            raise RuntimeError('No Vulkan component has been published for this application build.')
        if entry.get('binding_commit') != BINDING_COMMIT or entry.get('llama_commit') != LLAMA_COMMIT:
            raise ValueError('Vulkan component source version does not match')
        identifier = entry['id']
        if not identifier or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in identifier):
            raise ValueError('Invalid component identifier')
        if set(entry['runtime_sha256']) != set(NATIVE_FILES):
            raise ValueError('Incomplete native runtime fingerprints')
        for name, expected in entry['runtime_sha256'].items():
            if digest(runtime_directory() / name) != expected:
                raise ValueError('Vulkan component is incompatible with the CPU runtime: ' + name)
        files = entry['files']
        if set(files) != {'ggml-vulkan.dll', 'LICENSE-llama.cpp.txt'}:
            raise ValueError('Unexpected Vulkan component inventory')
        if not 0 < entry['size'] < 128 * 1024 * 1024:
            raise ValueError('Invalid Vulkan archive size')
        return entry

    def status(self):
        """No native imports, network access or directory creation."""
        try:
            entry = self._entry()
            installed = (self.root / entry['id'] / 'ggml-vulkan.dll').is_file()
            return dict(available=bool(entry.get('url')), installed=installed,
                        enabled=bool(self.config().get('vulkan_enabled', False)),
                        size=entry['size'], error=self.error, devices=list(self.devices),
                        directory=str(self.root / entry['id']))
        except Exception as error:
            return dict(available=False, installed=False, enabled=False, size=0,
                        error=str(error), devices=[], directory=str(self.root))

    def _verify(self, folder, entry):
        for name, info in entry['files'].items():
            path = folder / name
            if not path.is_file() or path.is_symlink() or path.stat().st_size != info['size'] or digest(path) != info['sha256']:
                raise ValueError('Vulkan component verification failed: ' + name)

    def install(self, progress=None, should_stop=None):
        """Download from the bundled HTTPS URL; cancelled transfers can resume."""
        with self._lock:
            entry = self._entry()
            final = self.root / entry['id']
            if final.exists():
                self._verify(final, entry)
                return str(final)
            url = entry.get('url', '')
            if not url.startswith('https://'):
                raise ValueError('No published HTTPS component URL is configured')
            self.root.mkdir(parents=True, exist_ok=True)
            part = self.root / (entry['id'] + '.zip.part')
            size = entry['size']

            def stop():
                if should_stop and should_stop():
                    raise DownloadCancelled()

            offset = part.stat().st_size if part.exists() else 0
            if offset > size:
                part.unlink()
                offset = 0
            if shutil.disk_usage(self.root).free < size - offset + sum(f['size'] for f in entry['files'].values()):
                raise OSError('Insufficient disk space for Vulkan component')
            if offset < size:
                headers = {'Accept-Encoding': 'identity', 'User-Agent': 'SeeName'}
                if offset:
                    headers['Range'] = f'bytes={offset}-'
                stop()
                with urlopen(Request(url, headers=headers), timeout=20) as response:
                    if not response.url.startswith('https://'):
                        raise ValueError('Component download redirected away from HTTPS')
                    if response.status == 200:
                        offset = 0
                    elif response.status != 206 or response.headers.get('Content-Range') != f'bytes {offset}-{size-1}/{size}':
                        raise ValueError('Invalid component download range')
                    with part.open('ab' if offset else 'wb') as stream:
                        while True:
                            stop()
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            if offset + len(chunk) > size:
                                raise ValueError('Component download exceeds expected size')
                            stream.write(chunk)
                            offset += len(chunk)
                            if progress:
                                progress(offset, size)
            stop()
            if part.stat().st_size != size:
                raise ValueError('Incomplete Vulkan download; retry to resume')
            if digest(part) != entry['sha256']:
                part.unlink()
                raise ValueError('Vulkan archive SHA-256 mismatch; retry download')
            stage = self.root / (entry['id'] + '-staging-' + uuid.uuid4().hex)
            stage.mkdir()
            try:
                with zipfile.ZipFile(part) as archive:
                    if len(archive.infolist()) != len(entry['files']) or set(archive.namelist()) != set(entry['files']):
                        raise ValueError('Unexpected files in Vulkan archive')
                    for name, info in entry['files'].items():
                        stop()
                        if archive.getinfo(name).file_size != info['size']:
                            raise ValueError('Invalid extracted file size')
                        with archive.open(name) as source, (stage / name).open('wb') as destination:
                            shutil.copyfileobj(source, destination, 1024 * 1024)
                self._verify(stage, entry)
                stop()
                stage.rename(final)
                part.unlink()
            finally:
                if stage.exists():
                    # Only remove the UUID staging directory created above.
                    if stage.resolve().parent != self.root.resolve():
                        raise ValueError('Staging path escaped the component root')
                    shutil.rmtree(stage)
            return str(final)

    def enable(self, enabled):
        self.save_config(vulkan_enabled=bool(enabled))
        self.error = ''

    def load(self):
        """Called under the inference lock, before any model is created."""
        if not self.config().get('vulkan_enabled', False):
            return False
        with self._lock:
            try:
                entry = self._entry()
                folder = self.root / entry['id']
                self._verify(folder, entry)
                from llama_cpp import _ggml as g
                if self._reg is not None and self._loaded_id != entry['id']:
                    raise RuntimeError('Restart SeeName before activating a different Vulkan component version')
                if self._reg is None:
                    self._dll_directory = os.add_dll_directory(str(folder))
                    self._reg = g.ggml_backend_load(os.fsencode(folder / 'ggml-vulkan.dll'))
                    if not self._reg:
                        raise RuntimeError('Vulkan backend initialization failed; using CPU')
                    self._loaded_id = entry['id']
                self.devices = [g.ggml_backend_dev_description(g.ggml_backend_reg_dev_get(self._reg, i)).decode('utf-8', 'replace')
                                for i in range(g.ggml_backend_reg_dev_count(self._reg))]
                if not self.devices:
                    raise RuntimeError('No compatible Vulkan GPU found; using CPU')
                self.error = ''
                return True
            except Exception as error:
                self.error = str(error)
                self.devices = []
                return False
