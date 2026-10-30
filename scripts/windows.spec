# -*- mode: python ; coding: utf-8 -*-
"""Portable CPU package. The only executable is SeeName.exe."""
import os
from pathlib import Path
import re

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable,
    VarFileInfo, VarStruct, VSVersionInfo,
)

root = Path(SPECPATH).parent
version = os.environ['SEENAME_BUILD_VERSION']
numbers = tuple(map(int, re.match(r'v?(\d+)\.(\d+)\.(\d+)', version).groups())) + (0,)
version_resource = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numbers, prodvers=numbers, mask=0x3f, flags=0,
                      OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', 'tanhua'),
        StringStruct('FileDescription', '显影 SeeName'),
        StringStruct('FileVersion', version),
        StringStruct('ProductVersion', version),
        StringStruct('ProductName', 'SeeName'),
        StringStruct('OriginalFilename', 'SeeName.exe'),
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])

datas = [(str(root/'data'/'districts.csv'), 'data'),
         (str(root/'data'/'cities_world.csv'), 'data'),
         (str(root/'assets'), 'assets'), (str(root/'licenses'), 'licenses')]
datas += [(str(root/name), '.') for name in ('help_zh.md', 'help_en.md', 'NOTICE.md', 'LICENSE')]
datas += collect_data_files('llama_cpp', includes=['**/*.json', '**/*.jinja', '**/*.txt'])
datas += [(os.environ.get('SEENAME_VULKAN_CATALOG', str(root/'inference'/'vulkan-catalog.json')), 'inference')]
datas += [(os.environ.get('SEENAME_CUDA_CATALOG', str(root/'inference'/'cuda-catalog.json')), 'inference')]

a = Analysis(
    [str(root/'main.py')], pathex=[str(root)],
    binaries=collect_dynamic_libs('llama_cpp'), datas=datas,
    hiddenimports=['llama_cpp.llama_multimodal', 'av', 'PIL._tkinter_finder'],
    hookspath=[], hooksconfig={},
    runtime_hooks=[os.environ['SEENAME_BUILD_RUNTIME_HOOK']],
    excludes=['inference.ollama', 'torch', 'torchvision', 'transformers',
              'huggingface_hub', 'scipy', 'matplotlib', 'IPython', 'pytest',
              'cv2', 'pandas', 'numba', 'sympy', 'mutagen', 'piexif', 'psutil'],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='SeeName', console=False,
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    icon=str(root/'assets'/'logo.ico'), version=version_resource,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='SeeName')
