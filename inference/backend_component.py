"""Versioned optional native plugin: trusted catalog, verified install/loading.

Only the selected ggml backend and its declared runtime files are added to the
existing native runtime. Never replace a loaded llama/ggml DLL. A catalog is
bundled at build time, not trusted from the download server; compatibility is
gated by the pinned source commits and backend ABI. Runtime DLL hashes remain
diagnostic fingerprints, while component files are checked against their hashes.
"""
from __future__ import annotations

import ctypes
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
BACKEND_ABI = 'ggml-backend-dl-v1'
LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x00000100
LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
LOAD_LIBRARY_SEARCH_DEFAULT_DIRS = 0x00001000
def compatibility_key(binding_commit=BINDING_COMMIT, llama_commit=LLAMA_COMMIT):
    """Identify the source-level runtime ABI shared by app and backend builds.

    Runtime DLL byte hashes are recorded for diagnostics, but are not a useful
    compatibility key: hosted Windows runners can produce different DLL bytes
    for the same pinned sources and ABI.
    """
    return f'windows-x64-{BACKEND_ABI}-binding-{binding_commit}-llama-{llama_commit}'


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


class NativeBackendComponent:
    """Install and load one pinned, separately published llama.cpp backend."""

    def __init__(self, config, save_config, *, backend, plugin, config_key,
                 catalog=None, root=None, extra_files=(), runtime_files=()):
        self.config, self.save_config = config, save_config
        self.backend, self.plugin, self.config_key = backend, plugin, config_key
        self.extra_files = tuple(extra_files)
        self.runtime_files = tuple(runtime_files)
        self.root = Path(root) if root else default_root()
        self.catalog = Path(catalog) if catalog else Path(__file__).with_name(backend.lower()+'-catalog.json')
        self._lock = threading.RLock()
        self._reg = None
        self._loaded_id = ''
        self._dll_directory = None
        self._runtime_handles = []
        self.devices = []
        self.error = ''
        self._version_entries = []

    @property
    def component_config_key(self):
        return self.config_key.removesuffix('_enabled') + '_component_id'

    def _validate_entry(self, entry):
        if entry.get('binding_commit') != BINDING_COMMIT or entry.get('llama_commit') != LLAMA_COMMIT:
            raise ValueError(f'{self.backend} component source version does not match')
        expected_compatibility = compatibility_key(entry['binding_commit'], entry['llama_commit'])
        if entry.get('compatibility_key', expected_compatibility) != expected_compatibility:
            raise ValueError(f'{self.backend} component compatibility profile does not match')
        identifier = entry['id']
        if not identifier or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in identifier):
            raise ValueError('Invalid component identifier')
        if set(entry['runtime_sha256']) != set(NATIVE_FILES):
            raise ValueError('Incomplete native runtime fingerprints')
        files = entry['files']
        if set(files) != {self.plugin, 'LICENSE-llama.cpp.txt', *self.extra_files}:
            raise ValueError(f'Unexpected {self.backend} component inventory')
        max_size = 2 * 1024 * 1024 * 1024 if self.backend == 'CUDA' else 128 * 1024 * 1024
        if not 0 < entry['size'] < max_size:
            raise ValueError(f'Invalid {self.backend} archive size')
        return entry

    def _entry(self):
        if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
            raise RuntimeError(f'This {self.backend} component currently supports Windows x64 only.')
        data = json.loads(self.catalog.read_text(encoding='utf-8'))
        if data.get('schema') != 1:
            raise ValueError(f'Unsupported {self.backend} catalog schema')
        entry = data.get('windows-x64')
        if not entry:
            raise RuntimeError(f'No {self.backend} component has been published for this application build.')
        current = self._validate_entry(entry)
        self._version_entries = [current]
        for candidate in data.get('versions', []):
            candidate = self._validate_entry(candidate)
            if candidate['id'] != current['id'] and all(item['id'] != candidate['id'] for item in self._version_entries):
                self._version_entries.append(candidate)
        return current

    def _installed_entries(self, current):
        # Historical entries are copied into the trusted application catalog by
        # the release resolver. Never trust a writable sidecar as a hash source.
        return {item['id']: item for item in self._version_entries or [current]}

    def _selected_entry(self, entries=None):
        current = self._entry()
        entries = entries or self._installed_entries(current)
        selected_id = self.config().get(self.component_config_key, '')
        if selected_id in entries:
            return entries[selected_id]
        # Preserve a previously installed compatible backend across an app
        # update that pins a newer version. New installs use the current pin.
        for entry in entries.values():
            if (self.root / entry['id'] / self.plugin).is_file():
                return entry
        return current

    def status(self):
        """No native imports, network access or directory creation."""
        try:
            current = self._entry()
            entries = self._installed_entries(current)
            entry = self._selected_entry(entries)
            versions = []
            for item in entries.values():
                path = self.root / item['id']
                installed = (path / self.plugin).is_file()
                versions.append(dict(id=item['id'], label=item['id'], installed=installed,
                                     available=installed or bool(item.get('url')),
                                     current=item['id'] == current['id'], size=item['size']))
            return dict(available=bool(entry.get('url')) or (self.root / entry['id'] / self.plugin).is_file(),
                        installed=(self.root / entry['id'] / self.plugin).is_file(),
                        enabled=bool(self.config().get(self.config_key, False)),
                        size=entry['size'], error=self.error, devices=list(self.devices),
                        id=entry['id'], versions=versions,
                        directory=str(self.root / entry['id']))
        except Exception as error:
            return dict(available=False, installed=False, enabled=False, size=0,
                        error=str(error), devices=[], versions=[], id='', directory=str(self.root))

    def _verify(self, folder, entry):
        for name, info in entry['files'].items():
            path = folder / name
            if not path.is_file() or path.is_symlink() or path.stat().st_size != info['size'] or digest(path) != info['sha256']:
                raise ValueError(f'{self.backend} component verification failed: ' + name)

    def install(self, progress=None, should_stop=None, component_id=None):
        """Download from the bundled HTTPS URL; cancelled transfers can resume."""
        with self._lock:
            entries = self._installed_entries(self._entry())
            selected_id = component_id or self.config().get(self.component_config_key, '')
            entry = entries.get(selected_id) or self._selected_entry(entries)
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
                raise OSError(f'Insufficient disk space for {self.backend} component')
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
                                raise ValueError(f'{self.backend} download exceeds expected size')
                            stream.write(chunk)
                            offset += len(chunk)
                            if progress:
                                progress(offset, size)
            stop()
            if part.stat().st_size != size:
                raise ValueError(f'Incomplete {self.backend} download; retry to resume')
            if digest(part) != entry['sha256']:
                part.unlink()
                raise ValueError(f'{self.backend} archive SHA-256 mismatch; retry download')
            stage = self.root / (entry['id'] + '-staging-' + uuid.uuid4().hex)
            stage.mkdir()
            try:
                with zipfile.ZipFile(part) as archive:
                    if len(archive.infolist()) != len(entry['files']) or set(archive.namelist()) != set(entry['files']):
                        raise ValueError(f'Unexpected files in {self.backend} archive')
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

    def enable(self, enabled, component_id=None):
        values = {self.config_key: bool(enabled)}
        if enabled and component_id:
            values[self.component_config_key] = component_id
        self.save_config(**values)
        self.error = ''

    def load(self):
        """Called under the inference lock, before any model is created."""
        if not self.config().get(self.config_key, False):
            return False
        with self._lock:
            try:
                entry = self._selected_entry()
                folder = self.root / entry['id']
                self._verify(folder, entry)
                from llama_cpp import _ggml as g
                if self._reg is not None and self._loaded_id != entry['id']:
                    raise RuntimeError(f'Restart SeeName before activating a different {self.backend} component version')
                if self._reg is None:
                    self._dll_directory = os.add_dll_directory(str(folder))
                    # llama.cpp currently calls LoadLibraryW for backend DLLs.
                    # That does not search the backend's directory for imported
                    # DLLs, even when it was registered with AddDllDirectory.
                    # Preload verified sidecar runtimes by absolute path so the
                    # subsequent backend load can resolve them by module name.
                    if self.backend == 'CUDA':
                        try:
                            self._runtime_handles.append(ctypes.WinDLL(
                                'nvcuda.dll', winmode=LOAD_LIBRARY_SEARCH_SYSTEM32))
                        except OSError as error:
                            raise RuntimeError(
                                f'NVIDIA driver library nvcuda.dll is unavailable: {error}'
                            ) from error
                    for name in self.runtime_files:
                        try:
                            handle = ctypes.WinDLL(
                                str(folder / name),
                                winmode=(LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR |
                                         LOAD_LIBRARY_SEARCH_DEFAULT_DIRS),
                            )
                        except OSError as error:
                            raise RuntimeError(
                                f'Unable to load {self.backend} runtime dependency '
                                f'{name} from {folder}: {error}'
                            ) from error
                        self._runtime_handles.append(handle)
                    self._reg = g.ggml_backend_load(os.fsencode(folder / self.plugin))
                    if not self._reg:
                        raise RuntimeError(f'{self.backend} backend initialization failed; using CPU')
                    self._loaded_id = entry['id']
                self.devices = [g.ggml_backend_dev_description(g.ggml_backend_reg_dev_get(self._reg, i)).decode('utf-8', 'replace')
                                for i in range(g.ggml_backend_reg_dev_count(self._reg))]
                if not self.devices:
                    raise RuntimeError(f'No compatible {self.backend} GPU found; using CPU')
                self.error = ''
                return True
            except Exception as error:
                self.error = str(error)
                self.devices = []
                return False


