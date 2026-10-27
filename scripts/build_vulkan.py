"""Build a matching Windows x64 Vulkan plugin and its trusted download catalog.

Requires VS 2022 C++ tools, CMake and Vulkan SDK (glslc, SPIRV-Headers included).
Uses the CPU binding installed in this Python environment as the compatibility
anchor. Does not install dependencies or upload files.
"""
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
from inference.vulkan import BINDING_COMMIT, LLAMA_COMMIT, NATIVE_FILES, digest, runtime_directory
from scripts.package_vulkan_source import SOURCE_SHA256, package_source, toolchain_info


def audit_plugin(plugin):
    roots = [Path(os.environ.get('ProgramFiles(x86)', r'C:\\Program Files (x86)')) ,
             Path(os.environ.get('ProgramFiles', r'C:\\Program Files'))]
    candidates = [candidate for root in roots for candidate in root.glob(
        'Microsoft Visual Studio/2022/*/VC/Tools/MSVC/*/bin/Hostx64/x64/dumpbin.exe')]
    tool = shutil.which('dumpbin') or (str(sorted(candidates)[-1]) if candidates else '')
    if not tool:
        raise RuntimeError('dumpbin is required to audit the component dependencies')
    exports = subprocess.check_output([tool, '/exports', str(plugin)]).decode('utf-8', 'replace')
    if not re.search(r'\bggml_backend_init\b', exports):
        raise RuntimeError('Plugin does not export ggml_backend_init')
    imports = subprocess.check_output([tool, '/dependents', str(plugin)]).decode('utf-8', 'replace')
    names = re.findall(r'^\s+([\w.-]+\.dll)\s*$', imports, re.M | re.I)
    allowed = {'ggml-base.dll', 'vulkan-1.dll', 'kernel32.dll', 'msvcp140.dll',
               'vcruntime140.dll', 'vcruntime140_1.dll', 'ucrtbase.dll'}
    unexpected = [n for n in names if n.lower() not in allowed and not n.lower().startswith('api-ms-win-crt-')]
    if not names or unexpected:
        raise RuntimeError('Unexpected Vulkan plugin dependencies: ' + repr(unexpected or names))
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vulkan-sdk', type=Path, required=True)
    parser.add_argument('--version', required=True, help='Immutable component version, e.g. v1')
    parser.add_argument('--download-base-url', default='', help='HTTPS Release asset directory; empty means local build only')
    parser.add_argument('--source-archive', type=Path, help='Optional cached, checksum-pinned source ZIP')
    parser.add_argument('--cmake', default='cmake')
    parser.add_argument('--output', type=Path, default=ROOT/'dist'/'backends')
    parser.add_argument('--work-root', type=Path, default=ROOT/'build'/'vulkan')
    parser.add_argument('--resume-work', type=Path, help='Resume a previously configured build work directory')
    args = parser.parse_args()
    if sys.platform != 'win32' or not args.version or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in args.version):
        parser.error('Use Windows x64 and a lowercase immutable version identifier.')
    if args.download_base_url and not args.download_base_url.startswith('https://'):
        parser.error('The download base URL must use HTTPS.')
    # Reuse the application preflight: exact binding commit and CPU-only runtime.
    from scripts.build_windows import preflight
    preflight()
    sdk = args.vulkan_sdk.resolve()
    for name in ('Bin/glslc.exe', 'Include/vulkan/vulkan.hpp', 'Lib/vulkan-1.lib'):
        if not (sdk/name).is_file():
            raise RuntimeError('Missing Vulkan SDK file: ' + str(sdk/name))
    identifier = f'vulkan-{LLAMA_COMMIT[:12]}-windows-x64-{args.version}'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output/(identifier+'.zip')
    catalog = output/(identifier+'.catalog.json')
    if archive.exists() or catalog.exists() or (output/(identifier+'-source.zip')).exists():
        raise RuntimeError('Output exists; choose a new immutable version or output directory.')
    work = args.resume_work.resolve() if args.resume_work else args.work_root.resolve()/uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=bool(args.resume_work))
    source_zip = args.source_archive.resolve() if args.source_archive else work/'source.zip'
    if not args.source_archive:
        with urlopen(f'https://codeload.github.com/ggml-org/llama.cpp/zip/{LLAMA_COMMIT}', timeout=60) as source, source_zip.open('wb') as target:
            shutil.copyfileobj(source, target)
    if digest(source_zip) != SOURCE_SHA256:
        raise RuntimeError('Pinned llama.cpp source archive checksum mismatch')
    source = work/('llama.cpp-'+LLAMA_COMMIT)
    if not (args.resume_work and (source/'LICENSE').is_file()):
        with zipfile.ZipFile(source_zip) as zipped:
            for item in zipped.infolist():
                if not (work/item.filename).resolve().is_relative_to(work):
                    raise ValueError('Invalid source archive path')
            zipped.extractall(work)
    environment = dict(os.environ, VULKAN_SDK=str(sdk))
    environment['PATH'] = str(sdk/'Bin') + os.pathsep + environment.get('PATH', '')
    command = [args.cmake, '-S', str(source), '-B', str(work/'native'),
               '-G', 'Visual Studio 17 2022', '-A', 'x64',
               '-DBUILD_SHARED_LIBS=ON', '-DGGML_BACKEND_DL=ON', '-DGGML_VULKAN=ON',
               '-DGGML_NATIVE=OFF', '-DGGML_CPU=OFF', '-DGGML_OPENMP=OFF',
               '-DGGML_CUDA=OFF', '-DLLAMA_BUILD_TESTS=OFF', '-DLLAMA_BUILD_TOOLS=OFF',
               '-DLLAMA_BUILD_EXAMPLES=OFF', '-DLLAMA_BUILD_SERVER=OFF', '-DLLAMA_CURL=OFF',
               '-DCMAKE_PREFIX_PATH='+str(sdk)]
    log = work/'build.log'
    print('Vulkan build log:', log, flush=True)
    with log.open('wb') as stream:
        subprocess.run(command, env=environment, stdout=stream, stderr=subprocess.STDOUT, check=True)
        subprocess.run([args.cmake, '--build', str(work/'native'), '--config', 'Release',
                        '--target', 'ggml-vulkan', '--parallel', '4'], env=environment,
                       stdout=stream, stderr=subprocess.STDOUT, check=True)
    matches = list((work/'native').rglob('ggml-vulkan.dll'))
    if len(matches) != 1:
        raise RuntimeError('Expected one ggml-vulkan.dll, found: ' + repr(matches))
    dependencies = audit_plugin(matches[0])
    package = work/'package'
    package.mkdir(exist_ok=bool(args.resume_work))
    shutil.copy2(matches[0], package/'ggml-vulkan.dll')
    shutil.copy2(source/'LICENSE', package/'LICENSE-llama.cpp.txt')
    # This is the entire allowed inventory: no helper EXEs or duplicate CPU DLLs.
    files = {p.name: dict(size=p.stat().st_size, sha256=digest(p)) for p in package.iterdir()}
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in sorted(package.iterdir()):
            zipped.write(path, path.name)
    entry = dict(id=identifier, binding_commit=BINDING_COMMIT, llama_commit=LLAMA_COMMIT,
                 url=args.download_base_url.rstrip('/')+'/'+archive.name if args.download_base_url else '',
                 size=archive.stat().st_size, sha256=digest(archive), files=files,
                 runtime_sha256={name: digest(runtime_directory()/name) for name in NATIVE_FILES},
                 dependencies=dependencies)
    catalog.write_text(json.dumps({'schema': 1, 'windows-x64': entry}, indent=2)+'\n', encoding='utf-8')
    archive.with_suffix('.zip.sha256').write_text(entry['sha256']+'  '+archive.name+'\n', encoding='ascii')
    source_package = package_source(source_zip, catalog,
                                   build_info=toolchain_info(work/'native', sdk, args.cmake))
    print('Component:', archive, flush=True)
    print('Catalog:', catalog, flush=True)
    print('Source package:', source_package, flush=True)
    print('Publish binary and source ZIPs together, verify URLs, then bundle the catalog in the application.', flush=True)


if __name__ == '__main__':
    main()
