"""Build a Windows x64 dynamic CUDA backend plus its matching CUDA runtime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.request import urlopen
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from inference.cuda import CUDA_RUNTIME_FILES
from inference.vulkan import BINDING_COMMIT, LLAMA_COMMIT, NATIVE_FILES, digest, runtime_directory
from scripts.package_vulkan_source import SOURCE_SHA256


def audit_dependencies(binary, packaged):
    roots = [Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')),
             Path(os.environ.get('ProgramFiles', r'C:\Program Files'))]
    candidates = [candidate for root in roots for candidate in root.glob(
        'Microsoft Visual Studio/2022/*/VC/Tools/MSVC/*/bin/Hostx64/x64/dumpbin.exe')]
    tool = shutil.which('dumpbin') or (str(sorted(candidates)[-1]) if candidates else '')
    if not tool:
        raise RuntimeError('dumpbin is required to audit CUDA component dependencies')
    allowed_system = {'kernel32.dll', 'ggml-base.dll', 'msvcp140.dll', 'msvcp140_1.dll',
                      'msvcp140_2.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'concrt140.dll',
                      'ucrtbase.dll', 'nvcuda.dll', 'advapi32.dll', 'bcrypt.dll', 'ntdll.dll',
                      'user32.dll', 'ole32.dll', 'shell32.dll', 'ws2_32.dll', 'version.dll',
                      'setupapi.dll', 'cfgmgr32.dll', 'gdi32.dll', 'winmm.dll', 'rpcrt4.dll',
                      'combase.dll', 'secur32.dll', 'shlwapi.dll', 'psapi.dll', 'dbghelp.dll'}
    package_names = {Path(name).name.lower() for name in packaged}
    for path in binary:
        output = subprocess.check_output([tool, '/dependents', str(path)]).decode('utf-8', 'replace')
        dependencies = re.findall(r'^\s+([\w.-]+\.dll)\s*$', output, re.M | re.I)
        unexpected = [name for name in dependencies
                      if name.lower() not in package_names and name.lower() not in allowed_system
                      and not name.lower().startswith(('api-ms-win-', 'ext-ms-win-'))]
        if unexpected:
            raise RuntimeError(f'Unpackaged dependency in {path.name}: '+repr(unexpected))


def run_logged(command, log_path):
    """Stream compiler output to Actions while retaining a complete local log."""
    with Path(log_path).open('ab') as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   bufsize=0)
        for line in iter(process.stdout.readline, b''):
            log.write(line)
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        code = process.wait()
    if code:
        raise subprocess.CalledProcessError(code, command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cuda-root', type=Path, default=os.environ.get('CUDA_PATH'))
    parser.add_argument('--version', required=True)
    parser.add_argument('--download-base-url', default='')
    parser.add_argument('--source-archive', type=Path)
    parser.add_argument('--cmake', default='cmake')
    parser.add_argument('--output', type=Path, default=ROOT/'dist'/'backends')
    parser.add_argument('--work-root', type=Path, default=ROOT/'build'/'cuda')
    args = parser.parse_args()
    if sys.platform != 'win32' or not args.version or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in args.version):
        parser.error('Use Windows x64 and a lowercase immutable version identifier.')
    if args.download_base_url and not args.download_base_url.startswith('https://'):
        parser.error('The download base URL must use HTTPS.')
    print('Checking the pinned CPU runtime and CUDA toolkit...', flush=True)
    from scripts.build_windows import preflight
    preflight()
    cuda = Path(args.cuda_root).resolve() if args.cuda_root else None
    if not cuda or not (cuda/'bin'/'nvcc.exe').is_file():
        raise RuntimeError('CUDA Toolkit 12.4 is required to build this component.')
    runtime = cuda/'bin'
    missing = [name for name in CUDA_RUNTIME_FILES if not (runtime/name).is_file()]
    if missing:
        raise RuntimeError('Missing CUDA redistributable libraries: '+', '.join(missing))
    identifier = f'cuda-12-4-{LLAMA_COMMIT[:12]}-windows-x64-{args.version}'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive, catalog = output/(identifier+'.zip'), output/(identifier+'.catalog.json')
    source_output = output/(identifier+'-source.zip')
    if any(path.exists() for path in (archive, catalog, source_output)):
        raise RuntimeError('Output exists; use a new immutable version or output directory.')
    work = args.work_root.resolve()/uuid.uuid4().hex
    work.mkdir(parents=True)
    print('Downloading the CUDA 12.4 EULA and pinned llama.cpp source...', flush=True)
    eula = work/'LICENSE-NVIDIA-CUDA.pdf'
    with urlopen('https://docs.nvidia.com/cuda/archive/12.4.1/pdf/EULA.pdf', timeout=30) as response:
        if not response.url.startswith('https://'):
            raise ValueError('CUDA EULA download redirected away from HTTPS')
        eula.write_bytes(response.read())
    if not eula.read_bytes().startswith(b'%PDF-'):
        raise RuntimeError('The CUDA 12.4.1 EULA PDF could not be verified.')
    upstream_zip = args.source_archive.resolve() if args.source_archive else work/'upstream.zip'
    if not args.source_archive:
        with urlopen(f'https://codeload.github.com/ggml-org/llama.cpp/zip/{LLAMA_COMMIT}', timeout=60) as source, upstream_zip.open('wb') as target:
            shutil.copyfileobj(source, target)
    if digest(upstream_zip) != SOURCE_SHA256:
        raise RuntimeError('Pinned llama.cpp source archive checksum mismatch')
    with zipfile.ZipFile(upstream_zip) as zipped:
        root = f'llama.cpp-{LLAMA_COMMIT}'
        for item in zipped.infolist():
            if not (work/item.filename).resolve().is_relative_to(work):
                raise ValueError('Invalid source archive path')
        zipped.extractall(work)
    print('Configuring the ggml-cuda dynamic backend...', flush=True)
    source = work/root
    build = work/'native'
    command = [args.cmake, '-S', str(source), '-B', str(build), '-G', 'Ninja Multi-Config',
               '-DBUILD_SHARED_LIBS=ON', '-DGGML_BACKEND_DL=ON', '-DGGML_CUDA=ON',
               '-DGGML_NATIVE=OFF', '-DGGML_CPU=OFF', '-DGGML_OPENMP=OFF', '-DGGML_VULKAN=OFF',
               '-DLLAMA_BUILD_TESTS=OFF', '-DLLAMA_BUILD_TOOLS=OFF', '-DLLAMA_BUILD_EXAMPLES=OFF',
               '-DLLAMA_BUILD_SERVER=OFF', '-DLLAMA_CURL=OFF', '-DCUDAToolkit_ROOT='+str(cuda),
               '-DGGML_CUDA_CCCL_VERSION=v3.4.3']
    if shutil.which('ccache'):
        command.extend(('-DCMAKE_CUDA_COMPILER_LAUNCHER=ccache',
                        '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache'))
    log = work/'build.log'
    run_logged(command, log)
    print('Compiling ggml-cuda.dll (live compiler output follows)...', flush=True)
    run_logged([args.cmake, '--build', str(build), '--config', 'Release', '--target', 'ggml-cuda',
                '--parallel', str(max(1, (os.cpu_count() or 4)-1))], log)
    matches = list(build.rglob('ggml-cuda.dll'))
    if len(matches) != 1:
        raise RuntimeError('Expected one ggml-cuda.dll, found: '+repr(matches))
    package = work/'package'
    package.mkdir()
    shutil.copy2(matches[0], package/'ggml-cuda.dll')
    for name in CUDA_RUNTIME_FILES:
        shutil.copy2(runtime/name, package/name)
    shutil.copy2(source/'LICENSE', package/'LICENSE-llama.cpp.txt')
    shutil.copy2(eula, package/'LICENSE-NVIDIA-CUDA.pdf')
    print('Auditing the CUDA DLL dependency closure...', flush=True)
    audit_dependencies([package/'ggml-cuda.dll', *(package/name for name in CUDA_RUNTIME_FILES)], package.iterdir())
    files = {p.name: dict(size=p.stat().st_size, sha256=digest(p)) for p in package.iterdir()}
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in sorted(package.iterdir()):
            zipped.write(path, path.name)
    entry = dict(id=identifier, binding_commit=BINDING_COMMIT, llama_commit=LLAMA_COMMIT,
                 url=args.download_base_url.rstrip('/')+'/'+archive.name if args.download_base_url else '',
                 size=archive.stat().st_size, sha256=digest(archive), files=files,
                 runtime_sha256={name: digest(runtime_directory()/name) for name in NATIVE_FILES},
                 cuda_toolkit='12.4', driver_provided='nvcuda.dll')
    catalog.write_text(json.dumps({'schema': 1, 'windows-x64': entry}, indent=2)+'\n', encoding='utf-8')
    archive.with_suffix('.zip.sha256').write_text(entry['sha256']+'  '+archive.name+'\n', encoding='ascii')
    with zipfile.ZipFile(source_output, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        zipped.write(upstream_zip, 'upstream/llama.cpp-'+LLAMA_COMMIT+'.zip')
        recipe = (ROOT/'scripts'/'build_cuda.py', ROOT/'scripts'/'README-cuda.md',
                  ROOT/'scripts'/'build_windows.py', ROOT/'scripts'/'windows.spec',
                  ROOT/'scripts'/'package_vulkan_source.py',
                  ROOT/'.github'/'workflows'/'windows-release.yml',
                  ROOT/'inference'/'cuda.py', ROOT/'inference'/'vulkan.py', ROOT/'inference'/'backend_component.py',
                  ROOT/'inference'/'accelerators.py', ROOT/'inference'/'llama_backend.py',
                  ROOT/'inference'/'downloads.py', ROOT/'inference'/'interfaces.py',
                  ROOT/'requirements-windows-build.txt', ROOT/'LICENSE')
        for path in recipe:
            zipped.write(path, path.relative_to(ROOT).as_posix())
        zipped.write(eula, 'licenses/LICENSE-NVIDIA-CUDA.pdf')
        zipped.write(catalog, 'cuda-catalog.json')
    source_output.with_suffix('.zip.sha256').write_text(digest(source_output)+'  '+source_output.name+'\n', encoding='ascii')
    print('CUDA component:', archive)
    print('Catalog:', catalog)
    print('Source package:', source_output)
    print('Package both CUDA runtime DLLs and EULA; NVIDIA display driver supplies nvcuda.dll.')


if __name__ == '__main__':
    main()
