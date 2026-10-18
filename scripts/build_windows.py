"""Build, verify and archive the same Windows package locally or in Actions."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = {'Pillow': '13.0.0', 'numpy': '2.3.2', 'av': '19.0.1',
            'llama-cpp-python': '0.4.3', 'PyInstaller': '6.22.3',
            'pyinstaller-hooks-contrib': '2026.8'}
BINDING_COMMIT = 'ce261d12b5810fb4c6e695efc85d5e8cf23f0630'


def preflight():
    if sys.platform != 'win32' or struct.calcsize('P') != 8 or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('Use 64-bit Windows and x64 Python for this build.')
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError('This build is pinned to Python 3.12 x64.')
    for name, expected in VERSIONS.items():
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            actual = 'not installed'
        if actual != expected:
            raise RuntimeError(f'{name}: expected {expected}, found {actual}. '
                               'Install requirements-windows-build.txt in a build environment.')
    dist = metadata.distribution('llama-cpp-python')
    origin = json.loads(dist.read_text('direct_url.json') or '{}')
    commit = origin.get('vcs_info', {}).get('commit_id')
    if commit != BINDING_COMMIT:
        raise RuntimeError('Install the pinned llama-cpp-python Git revision from requirements-inference.txt; '
                           'this environment has a different or unrecorded source revision.')
    import tkinter
    import llama_cpp
    from llama_cpp.llama_multimodal import Qwen3VLChatHandler
    import av
    if not callable(Qwen3VLChatHandler) or not hasattr(llama_cpp.Llama, 'abort'):
        raise RuntimeError('The required Qwen3-VL/MTMD binding is unavailable.')
    if llama_cpp.llama_supports_gpu_offload():
        raise RuntimeError('Use a CPU-only llama-cpp-python build for this package.')
    return commit


def hash_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def audit(application):
    files = sorted(path for path in application.rglob('*') if path.is_file())
    executables = [path.relative_to(application).as_posix() for path in files if path.suffix.lower() == '.exe']
    if executables != ['SeeName.exe']:
        raise RuntimeError('Unexpected executable inventory: ' + repr(executables))
    for path in files:
        relative = path.relative_to(application)
        if path.suffix.lower() == '.gguf' or path.name.lower() == 'config.json':
            raise RuntimeError('Models or local settings were bundled: ' + str(relative))
        if set(part.lower() for part in relative.parts) & {'models', 'ollama', '_dev_scratch', '.workbuddy-ai'}:
            raise RuntimeError('Unexpected private/runtime directory: ' + str(relative))
    return files


def smoke(application, work, model_directory):
    environment = dict(os.environ)
    for key in list(environment):
        if key.upper().startswith('CONDA') or key.upper() in ('PYTHONPATH', 'PYTHONHOME', 'TCL_LIBRARY', 'TK_LIBRARY'):
            environment.pop(key)
    windows = Path(os.environ.get('SystemRoot', r'C:\Windows')).resolve()
    environment['PATH'] = str(windows/'System32') + os.pathsep + str(windows)
    output = work/'package-check.json'
    command = [str(application/'SeeName.exe'), '--package-check', str(output)]
    if model_directory:
        command += ['--check-model-directory', str(Path(model_directory).resolve())]
    subprocess.run(command, check=True, cwd=work, env=environment,
                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=600 if model_directory else 180)
    result = json.loads(output.read_text(encoding='utf-8'))
    if not result.get('success'):
        raise RuntimeError('Package acceptance checks failed: ' + result.get('error', str(output)))
    external = []
    security_modules = []
    defender = Path(os.environ.get('ProgramData', r'C:\ProgramData'))/'Microsoft'/'Windows Defender'/'Platform'
    for value in result['native_modules']:
        path = Path(value).resolve()
        if not path.is_relative_to(application) and not path.is_relative_to(windows):
            # AMSI may inject this Windows security component into the Python
            # process. It is not a bundled dependency or a Conda DLL.
            if path.name.lower() == 'mpoav.dll' and path.is_relative_to(defender.resolve()):
                security_modules.append(path.name)
            else:
                external.append(value)
    if external:
        raise RuntimeError('Native modules were loaded outside the package/Windows: ' + repr(external))
    result['host_security_modules'] = security_modules
    return result


def git_state():
    try:
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
        return revision, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='v0.0.0-dev')
    parser.add_argument('--output', type=Path, default=ROOT/'dist'/'windows')
    parser.add_argument('--work-root', type=Path, default=ROOT/'build'/'windows')
    parser.add_argument('--verify-model-directory', default='')
    parser.add_argument('--vulkan-catalog', type=Path, help='Trusted Vulkan component catalog to bundle')
    args = parser.parse_args()
    if not re.fullmatch(r'v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', args.version):
        parser.error('Version must look like v1.2.3 or v1.2.3-rc.1.')
    commit = preflight()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stem = f'SeeName-{args.version}-windows-x64-cpu'
    archive = output/(stem+'.zip')
    checksum = output/(stem+'.zip.sha256')
    manifest = output/(stem+'.build-info.json')
    for path in (archive, checksum, manifest):
        if path.exists():
            raise RuntimeError(f'Output already exists: {path}. Use a different version or output directory.')
    work = args.work_root.resolve()/uuid.uuid4().hex
    work.mkdir(parents=True)
    if args.vulkan_catalog:
        sys.path.insert(0, str(ROOT))
        from inference.vulkan import VulkanComponent
        VulkanComponent(lambda: {}, lambda **kw: None, catalog=args.vulkan_catalog)._entry()
        shutil.copy2(args.vulkan_catalog, work/'vulkan-catalog.json')
    print('Build work directory:', work, flush=True)
    hook = work/'runtime_profile.py'
    hook.write_text('import sys\nsys._seename_metadata_write_disabled = True\n'
                    f'sys._seename_version = {args.version!r}\n', encoding='utf-8')
    environment = dict(os.environ, SEENAME_BUILD_VERSION=args.version,
                       SEENAME_BUILD_RUNTIME_HOOK=str(hook), PYTHONUTF8='1',
                       PYTHONDONTWRITEBYTECODE='1')
    if args.vulkan_catalog:
        environment['SEENAME_VULKAN_CATALOG'] = str(work/'vulkan-catalog.json')
    else:
        environment.pop('SEENAME_VULKAN_CATALOG', None)
    command = [sys.executable, '-B', '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--distpath', str(work/'dist'), '--workpath', str(work/'analysis'),
               str(ROOT/'scripts'/'windows.spec')]
    log = work/'pyinstaller.log'
    print('Building SeeName.exe (PyInstaller log:', str(log)+')', flush=True)
    with log.open('wb') as stream:
        built = subprocess.run(command, env=environment, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    if built.returncode:
        print('\n'.join(log.read_text(encoding='utf-8', errors='replace').splitlines()[-50:]), file=sys.stderr)
        raise RuntimeError('PyInstaller failed. Full log: ' + str(log))
    application = work/'dist'/'SeeName'
    audit(application)
    print('Verifying hidden GUI, PyAV and native inference libraries with a clean PATH...', flush=True)
    try:
        checked = smoke(application, work, args.verify_model_directory)
    except Exception:
        if (work/'package-check.json').exists():
            print((work/'package-check.json').read_text(encoding='utf-8'), file=sys.stderr)
        raise
    shutil.copyfile(ROOT/'scripts'/'README-windows.md', application/'README-WINDOWS.md')
    revision, dirty = git_state()
    info = dict(version=args.version, platform='windows', architecture='x64', device='CPU',
                layout='onedir', models_included=False, metadata_writing_enabled=False,
                python=platform.python_version(), dependencies=VERSIONS,
                binding_commit=commit, source_revision=revision, source_dirty=dirty,
                checks=dict(gui=checked['gui'], media=checked['media'],
                            native_library=checked['native_inference_library'],
                            model_inference=checked.get('model_inference'),
                            host_security_modules=checked['host_security_modules'],
                            native_module_count=len(checked['native_modules'])))
    (application/'BUILD-INFO.json').write_text(json.dumps(info, indent=2)+'\n', encoding='utf-8')
    files = audit(application)
    info['files'] = [dict(path=path.relative_to(application).as_posix(),
                          size=path.stat().st_size, sha256=hash_file(path)) for path in files]
    temporary = work/(stem+'.zip')
    print('Creating verified archive...', flush=True)
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for path in files:
            package.write(path, 'SeeName/'+path.relative_to(application).as_posix())
    digest = hash_file(temporary)
    # Only publish artifacts after all acceptance checks have passed.
    shutil.copyfile(temporary, archive)
    checksum.write_text(f'{digest} *{archive.name}\n', encoding='ascii')
    manifest.write_text(json.dumps(info, indent=2)+'\n', encoding='utf-8')
    print('Package:', archive, flush=True)
    print('SHA256:', digest, flush=True)
    print('Verified application directory:', application, flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('BUILD FAILED:', error, file=sys.stderr)
        raise SystemExit(1)
