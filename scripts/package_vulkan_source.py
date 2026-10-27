"""Ship the pinned upstream source and the complete Vulkan build recipe together."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA256 = '611e1075d170ab8a1854a9aca6ed6fd141a91c0719cbf67d6cf0163b44b6ddea'
RECIPE_FILES = (
    'scripts/build_vulkan.py', 'scripts/package_vulkan_source.py',
    'scripts/build_windows.py', 'scripts/README-vulkan.md',
    '.github/workflows/windows-release.yml',
    'inference/vulkan.py', 'inference/backend_component.py', 'inference/downloads.py',
    'inference/interfaces.py', 'inference/__init__.py',
    'requirements-inference.txt', 'requirements-media.txt',
    'requirements-windows-build.txt', 'LICENSE',
)


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def toolchain_info(build_directory=None, sdk=None, cmake='cmake'):
    """Record versions and compile options, excluding host/user absolute paths."""
    info = {}
    if build_directory:
        cache = Path(build_directory)/'CMakeCache.txt'
        if cache.exists():
            entries = {}
            for line in cache.read_text(encoding='utf-8').splitlines():
                match = re.fullmatch(r'([^/#:][^:]*):[^=]+=(.*)', line)
                if match:
                    entries[match[1]] = match[2]
            names = ('BUILD_SHARED_LIBS', 'GGML_BACKEND_DL', 'GGML_VULKAN',
                     'GGML_NATIVE', 'GGML_CPU', 'GGML_OPENMP', 'GGML_CUDA',
                     'LLAMA_BUILD_TESTS', 'LLAMA_BUILD_TOOLS', 'LLAMA_BUILD_EXAMPLES',
                     'LLAMA_BUILD_SERVER', 'LLAMA_CURL', 'CMAKE_GENERATOR', 'CMAKE_GENERATOR_PLATFORM')
            info['cmake_options'] = {name: entries[name] for name in names if name in entries}
            for compiler in Path(build_directory).glob('CMakeFiles/*/CMakeCXXCompiler.cmake'):
                text = compiler.read_text(encoding='utf-8')
                for name in ('CMAKE_CXX_COMPILER_ID', 'CMAKE_CXX_COMPILER_VERSION'):
                    match = re.search(r'set\(' + name + r' "([^"]+)"\)', text)
                    if match:
                        info[name] = match[1]
    for name, command in (('cmake', [cmake, '--version']),
                          ('glslc', [str(Path(sdk)/'Bin/glslc.exe'), '--version'] if sdk else None)):
        if command:
            result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            info[name] = result.stdout.decode('utf-8', 'replace').strip().splitlines()[:4]
    if sdk:
        header = Path(sdk)/'Include/vulkan/vulkan_core.h'
        if header.exists():
            info['vulkan_header_sha256'] = sha256(header)
    return info


def package_source(source_archive, catalog_path, *, build_info=None):
    source_archive, catalog_path = Path(source_archive), Path(catalog_path)
    if sha256(source_archive) != SOURCE_SHA256:
        raise ValueError('Pinned upstream source archive checksum mismatch')
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    entry = catalog['windows-x64']
    identifier = entry['id']
    if not re.fullmatch(r'[a-z0-9_-]+', identifier):
        raise ValueError('Invalid component ID')
    binary = catalog_path.parent/(identifier+'.zip')
    if not binary.is_file() or sha256(binary) != entry['sha256']:
        raise ValueError('Component ZIP does not match the catalog')
    output = catalog_path.parent/(identifier+'-source.zip')
    checksum = output.with_suffix('.zip.sha256')
    if output.exists() or checksum.exists():
        raise FileExistsError('Source output exists; do not overwrite an immutable release artifact')
    for name in RECIPE_FILES:
        if not (ROOT/name).is_file():
            raise FileNotFoundError('Missing build recipe file: ' + name)
    llama_commit = entry['llama_commit']
    upstream_name = f'upstream/llama.cpp-{llama_commit}.zip'
    with zipfile.ZipFile(source_archive) as upstream:
        license_text = upstream.read(f'llama.cpp-{llama_commit}/LICENSE')
        # The complete pristine archive contains sources, CMake and shaders.
        for name in ('ggml/src/ggml-vulkan/ggml-vulkan.cpp',
                     'ggml/src/ggml-vulkan/CMakeLists.txt', 'CMakeLists.txt'):
            upstream.getinfo(f'llama.cpp-{llama_commit}/'+name)
    readme = f'''# Vulkan component source / Vulkan 组件源码

This bundle contains the complete upstream llama.cpp source archive, the SeeName
build scripts and their Python helpers, dependency pins, licenses and the binary
catalog snapshot. No Vulkan source is represented by a download link alone.

本包提供完整上游源码、构建脚本、依赖版本、许可证及对应二进制的清单。
上游源码保留为原始 ZIP，既可解压阅读全部 C++ / CMake / shader 源码，也可
直接通过 --source-archive 构建；构建时无需再次下载 llama.cpp 源码。

## Rebuild / 重新构建

Use Windows x64, Python 3.12, VS 2022 C++ tools, CMake and Vulkan SDK including
glslc and SPIRV-Headers. See SOURCE-INFO.json for the original build settings.
Install requirements-windows-build.txt in a dedicated build environment; building
the pinned CPU Python binding requires C++ tools. SDKs, compiler binaries and
Python dependencies are external build prerequisites, not included in this ZIP.

```powershell
$env:CMAKE_ARGS = '-DGGML_NATIVE=OFF -DGGML_CUDA=OFF -DGGML_VULKAN=OFF'
python -m pip install -r requirements-windows-build.txt
python -B scripts/build_vulkan.py --vulkan-sdk C:\\VulkanSDK\\<version> --version rebuild1 --source-archive {upstream_name}
```

Run from this extracted directory. The default build is local and uploads
nothing. scripts/build_windows.py supplies the shared environment preflight;
this source bundle builds the Vulkan component, not the complete GUI application.

CPU-native DLL fingerprints depend on the actual build. Rebuilding those DLLs
requires rebuilding the base application with the newly generated catalog.
These instructions reproduce the component build, not necessarily byte-identical
binaries across different compilers/SDKs.

组件 ZIP 和本源码 ZIP 应随同一个 Release 一起提供；普通用户仅下载运行组件。
此源码包不含模型、用户配置或额外运行程序。
'''
    inventory = {}
    prefix = identifier+'-source/'
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        def add_bytes(name, data, compression=zipfile.ZIP_DEFLATED):
            archive.writestr(prefix+name, data, compress_type=compression)
            inventory[name] = dict(size=len(data), sha256=hashlib.sha256(data).hexdigest())

        for name in RECIPE_FILES:
            add_bytes(name, (ROOT/name).read_bytes())
        # Store the already compressed source without redundant recompression.
        archive.write(source_archive, prefix+upstream_name, compress_type=zipfile.ZIP_STORED)
        inventory[upstream_name] = dict(size=source_archive.stat().st_size, sha256=SOURCE_SHA256)
        add_bytes('LICENSE-llama.cpp.txt', license_text)
        add_bytes('README-SOURCE.md', readme.encode('utf-8'))
        snapshot = dict(catalog)
        snapshot['windows-x64'] = {k: v for k, v in entry.items() if k != 'source'}
        add_bytes('component/'+catalog_path.name, (json.dumps(snapshot, indent=2)+'\n').encode('utf-8'))
        info = dict(schema=1, component_id=identifier, component_sha256=entry['sha256'],
                    binding_commit=entry['binding_commit'], llama_commit=llama_commit,
                    upstream_archive_sha256=SOURCE_SHA256,
                    toolchain=build_info or {}, files=inventory)
        archive.writestr(prefix+'SOURCE-INFO.json', json.dumps(info, indent=2)+'\n')
    source_info = dict(size=output.stat().st_size, sha256=sha256(output),
                       url=entry['url'].rsplit('/', 1)[0]+'/'+output.name if entry.get('url') else '')
    checksum.write_text(source_info['sha256']+'  '+output.name+'\n', encoding='ascii')
    entry['source'] = source_info
    catalog_path.write_text(json.dumps(catalog, indent=2)+'\n', encoding='utf-8')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--build-directory', type=Path)
    parser.add_argument('--vulkan-sdk', type=Path)
    parser.add_argument('--cmake', default='cmake')
    args = parser.parse_args()
    info = toolchain_info(args.build_directory, args.vulkan_sdk, args.cmake)
    print('Source package:', package_source(args.source_archive, args.catalog, build_info=info))


if __name__ == '__main__':
    main()
