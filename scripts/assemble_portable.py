"""把「CPU 应用包 + GPU 组件 + 模型」拼成一个解压即用的整包。

为什么单独一个脚本，而不是塞进 build_windows.py：
那个脚本的 audit() 明确禁止包内出现 models/ 和 *.gguf（CPU 纯净包必须如此），
smoke() 也按「模型在外部目录」验收。硬塞会把 CI 的验收标准搅浑。这里做后处理，
输入是已经通过验收的应用目录，输出是面向普通用户的整包。

真正要紧的是 --component-dir 那一段：会拿应用内的可信 catalog 逐个核对文件名、
大小和 SHA-256，对不上直接报错退出。软件自己加载组件前也会验一次，但那是用户
解压之后的事了——到那一步才失败，用户只会看到「加速用不了」，查不出原因。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SEVENZIP_CANDIDATES = (
    r'C:\Program Files\7-Zip\7z.exe',
    r'C:\Program Files (x86)\7-Zip\7z.exe',
)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def find_7z() -> str:
    for candidate in SEVENZIP_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which('7z') or shutil.which('7za')
    return found or ''


def read_catalog(app_dir: Path, backend: str) -> dict:
    path = app_dir / '_internal' / 'inference' / (backend.lower() + '-catalog.json')
    if not path.is_file():
        raise SystemExit('应用目录里找不到 %s 的 catalog：%s\n'
                         '说明这个应用包不是带 --%s-catalog 构建的，'
                         '打出来的整包即使带了 DLL 也不会被识别。'
                         % (backend, path, backend.lower()))
    data = json.loads(path.read_text(encoding='utf-8'))
    entry = data.get('windows-x64')
    if not entry:
        raise SystemExit('%s 的 catalog 是空的（%s 里没有 windows-x64 条目）。\n'
                         '空 catalog = status() 永远返回未安装，autopick 不会启用它。'
                         % (backend, path))
    return entry


def check_component(folder: Path, entry: dict) -> list:
    """与 inference/backend_component.py 的 _verify() 逐条对齐。"""
    problems = []
    expected = entry.get('files') or {}
    if not expected:
        raise SystemExit('catalog 里没有文件清单，无法校验组件')
    actual = {item.name for item in folder.iterdir() if item.is_file()}
    missing = set(expected) - actual
    extra = actual - set(expected)
    if missing:
        problems.append('缺少文件：' + '、'.join(sorted(missing)))
    if extra:
        problems.append('多出文件：' + '、'.join(sorted(extra)))
    for name, info in sorted(expected.items()):
        path = folder / name
        if not path.is_file():
            continue
        if path.stat().st_size != info.get('size'):
            problems.append('%s 大小不对：catalog %s，实际 %s'
                            % (name, info.get('size'), path.stat().st_size))
            continue
        if digest(path) != info.get('sha256'):
            problems.append('%s 的 SHA-256 与 catalog 不符' % name)
    return problems


def copy_models(models_dir: Path, target: Path, only: list) -> list:
    """只拷 GGUF 与同名说明，跳过空目录（比如还没下载的 2B 目录）。"""
    copied = []
    groups = [models_dir / name for name in only] if only else \
        [item for item in sorted(models_dir.iterdir()) if item.is_dir()]
    for group in groups:
        if not group.is_dir():
            raise SystemExit('模型子目录不存在：%s' % group)
        files = [item for item in group.iterdir()
                 if item.is_file() and item.suffix.lower() in ('.gguf', '.txt', '.md', '.json')]
        if not any(item.suffix.lower() == '.gguf' for item in files):
            print('跳过（没有 gguf）：%s' % group.name, flush=True)
            continue
        group_target = target / group.name
        group_target.mkdir(parents=True, exist_ok=True)
        for item in files:
            shutil.copy2(item, group_target / item.name)
            copied.append('%s/%s' % (group.name, item.name))
    if not copied:
        raise SystemExit('没有拷到任何模型文件，--models-dir 指对了吗：%s' % models_dir)
    return copied


def readme_text(flavor: str, backend: str, version: str, models: list) -> str:
    lines = ['显影 SeeName %s · Windows x64 便携版' % version, '']
    if backend:
        lines += ['这个包已内置 %s 显卡加速组件，解压后直接双击 SeeName.exe。' % backend, '']
    else:
        lines += ['解压后直接双击 SeeName.exe。', '']
    lines += ['—— 使用 ——',
              '1. 把整个文件夹解压出来（不要只解压 SeeName.exe）',
              '2. 双击 SeeName.exe',
              '3. 选一个装着照片/视频的文件夹，点「▶ 开始识别」',
              '4. 看完结果点「核对并改名」，确认后再应用',
              '',
              '请保留 _internal 文件夹，里面是 Python、Tk、llama.cpp 和视频解码库，',
              '删掉或单独挪走 SeeName.exe 都会打不开。不需要另外装 Python 或 FFmpeg。',
              '']
    if models:
        lines += ['—— 模型 ——',
                  '这个包已带模型，第一次点「开始识别」就能直接用，不用下载：']
        lines += ['  models/' + item for item in models]
    else:
        lines += ['—— 模型 ——',
                  '这个包不含模型（模型约 2.8 GB，单独下载更灵活）。',
                  '第一次启动会提示去「模型管理」下载，默认走国内 ModelScope 源，',
                  '支持断点续传。下完存在本文件夹的 models 里，整个文件夹拷走还能用。']
    lines += ['']
    if backend == 'CUDA':
        lines += ['—— 显卡要求 ——',
                  '需要 NVIDIA 显卡和 527.41 或更新的驱动。驱动太旧会在加速面板里',
                  '写明原因并自动改用 CPU，不影响正常改名，只是慢一些。',
                  '']
    elif backend == 'Vulkan':
        lines += ['—— 显卡要求 ——',
                  '需要支持 Vulkan 的显卡和驱动（近十年的独显/核显基本都支持）。',
                  '跑不起来会自动改用 CPU，不影响正常改名，只是慢一些。',
                  '']
    lines += ['—— 关于加速 ——',
              '第一次启动会自动判断用不用显卡。判断不了就先用 CPU，之后可以在',
              '「设置 → GPU 加速」里手动切换。',
              '',
              '—— 杀毒软件 ——',
              '这个 exe 没有数字签名，部分杀毒软件可能误报。加白名单即可。',
              '']
    return '\n'.join(lines) + '\n'


def compress(folder: Path, archive_base: Path) -> Path:
    """返回真正写出来的压缩包路径。"""
    seven = find_7z()
    if seven:
        print('用 7-Zip 压缩：%s' % seven, flush=True)
        archive = Path(str(archive_base) + '.7z')
        subprocess.run([seven, 'a', '-t7z', '-mx=5', '-mmt=on', '-bso0', '-bsp0',
                        str(archive), folder.name],
                       cwd=str(folder.parent), check=True)
        return archive
    print('没找到 7-Zip，退回 zip', flush=True)
    archive = Path(str(archive_base) + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6,
                         allowZip64=True) as package:
        for item in sorted(folder.rglob('*')):
            if item.is_file():
                package.write(item, Path(folder.name) / item.relative_to(folder))
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path, required=True,
                        help='已通过 build_windows.py 验收的 SeeName 应用目录')
    parser.add_argument('--version', required=True, help='例如 v2.0.0')
    parser.add_argument('--flavor', required=True,
                        help='包名后缀，例如 vulkan-免模型 / cuda-含模型')
    parser.add_argument('--backend', choices=('none', 'vulkan', 'cuda'), default='none')
    parser.add_argument('--component-dir', type=Path,
                        help='backends/ 下那一份组件目录，目录名必须与 catalog 的 id 一致')
    parser.add_argument('--models-dir', type=Path, help='模型根目录（下面按子目录分组）')
    parser.add_argument('--model-group', action='append', default=[],
                        help='只打包这几个模型子目录，可重复；默认全部')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'bundles')
    parser.add_argument('--no-compress', action='store_true', help='只生成目录，不压缩')
    args = parser.parse_args()

    app = args.app_dir.resolve()
    for required in ('SeeName.exe', '_internal'):
        if not (app / required).exists():
            raise SystemExit('应用目录不完整，缺少 %s：%s' % (required, app))

    name = '显影SeeName_%s_%s' % (args.version, args.flavor)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    staging = output / name
    if staging.exists():
        raise SystemExit('目标已存在，换个 --output 或先删掉：%s' % staging)

    entry = None
    if args.backend != 'none':
        if not args.component_dir:
            raise SystemExit('--backend %s 必须配 --component-dir' % args.backend)
        entry = read_catalog(app, {'vulkan': 'Vulkan', 'cuda': 'CUDA'}[args.backend])
        component = args.component_dir.resolve()
        if component.name != entry['id']:
            raise SystemExit('组件目录名与 catalog 的 id 不一致：\n  目录 %s\n  catalog %s\n'
                             '名字对不上，软件就找不到这份组件。'
                             % (component.name, entry['id']))
        print('校验 %s 组件（%d 个文件）…' % (args.backend, len(entry['files'])), flush=True)
        problems = check_component(component, entry)
        if problems:
            raise SystemExit('组件与 catalog 对不上：\n  ' + '\n  '.join(problems))
        print('组件校验通过', flush=True)

    print('铺开应用目录…', flush=True)
    shutil.copytree(app, staging)

    if args.backend != 'none':
        target = staging / 'backends' / entry['id']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(args.component_dir.resolve(), target)
        print('已放入 backends/%s' % entry['id'], flush=True)

    models = []
    if args.models_dir:
        print('拷模型…', flush=True)
        models = copy_models(args.models_dir.resolve(), staging / 'models', args.model_group)
        print('已拷 %d 个模型文件' % len(models), flush=True)

    (staging / '使用说明.txt').write_text(
        readme_text(args.flavor, {'vulkan': 'Vulkan', 'cuda': 'CUDA'}.get(args.backend, ''),
                    args.version, models),
        encoding='utf-8-sig')

    info = {'version': args.version, 'flavor': args.flavor,
            'backend': args.backend or 'none',
            'component_id': entry['id'] if entry else '',
            'models': models, 'models_included': bool(models)}
    upstream = app / 'BUILD-INFO.json'
    if upstream.is_file():
        info['application'] = json.loads(upstream.read_text(encoding='utf-8'))
    (staging / 'BUNDLE-INFO.json').write_text(json.dumps(info, ensure_ascii=False, indent=2) + '\n',
                                              encoding='utf-8')

    files = [item for item in sorted(staging.rglob('*')) if item.is_file()]
    total = sum(item.stat().st_size for item in files)
    print('整包：%d 个文件，%.2f GB' % (len(files), total / 1024 ** 3), flush=True)

    if args.no_compress:
        print('目录：%s' % staging, flush=True)
        return 0

    archive = compress(staging, output / name)
    checksum = Path(str(archive) + '.sha256')
    value = digest(archive)
    checksum.write_text('%s *%s\n' % (value, archive.name), encoding='utf-8')
    print('压缩包：%s（%.2f GB）' % (archive, archive.stat().st_size / 1024 ** 3), flush=True)
    print('SHA256：%s' % value, flush=True)
    print('校验文件：%s' % checksum, flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:
        print('组装失败：%s' % error, file=sys.stderr)
        raise SystemExit(1)
