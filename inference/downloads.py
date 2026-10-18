"""Verified, resumable HTTPS weight downloads; no external programs."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
from urllib.request import Request, urlopen


DOWNLOADS = {
    'Qwen3VL-4B-Instruct-Q4_K_M': (
        ('Qwen3VL-4B-Instruct-Q4_K_M.gguf', 2497281664,
         '66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a'),
        ('mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf', 453974304,
         '30ba2c7dd3127a4561b6cba9d13d0f711c91bdb38742e2f56d73c8cb596bd06d')),
    'Qwen3VL-4B-Instruct-Q8_0': (
        ('Qwen3VL-4B-Instruct-Q8_0.gguf', 4280406144,
         '054721f478bc5fa6beffb7f38eae575d45298f88cbb8d2f83ef675a727863eb1'),
        ('mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf', 453974304,
         '30ba2c7dd3127a4561b6cba9d13d0f711c91bdb38742e2f56d73c8cb596bd06d')),
    'Qwen3VL-2B-Instruct-Q4_K_M': (
        ('Qwen3VL-2B-Instruct-Q4_K_M.gguf', 1107409952,
         '089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae'),
        ('mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf', 445053216,
         'f9a68fabba69c3b81e153367b2c7521030b0fa8bb0de400c9599c8e6725f9c82')),
    'Qwen3VL-2B-Instruct-Q8_0': (
        ('Qwen3VL-2B-Instruct-Q8_0.gguf', 1834427424,
         '1e8db19207c8ce0733ddd78c2eff8a9e22c27c82f4443df94c25792ed8fe04f2'),
        ('mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf', 445053216,
         'f9a68fabba69c3b81e153367b2c7521030b0fa8bb0de400c9599c8e6725f9c82')),
}


class DownloadCancelled(Exception):
    pass


def model_folder(name):
    return 'Qwen3-VL-' + ('2B' if '-2B-' in name else '4B') + '-Instruct-GGUF'


def file_url(name, filename, source):
    repo = 'Qwen/' + model_folder(name)
    if source == 'modelscope':
        return f'https://modelscope.cn/models/{repo}/resolve/master/{filename}'
    if source == 'huggingface':
        return f'https://huggingface.co/{repo}/resolve/main/{filename}'
    raise ValueError('Unknown download source: ' + source)


def download_pair(name, root, source, on_progress=None, should_stop=None):
    """Only verified complete files receive their final names; partials survive cancel.

    A fixed SHA-256 identifies content across sources and prevents accepting a
    changed remote file when resuming. A mismatched partial is removed on retry.
    Caller serializes downloads and directory changes.
    """
    files = DOWNLOADS[name]
    folder = Path(root) / model_folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    total = sum(size for _, size, _ in files)
    completed = 0

    def check_stop():
        if should_stop and should_stop():
            raise DownloadCancelled()

    def digest(path, label):
        sha = hashlib.sha256()
        with path.open('rb') as stream:
            while chunk := stream.read(4 * 1024 * 1024):
                check_stop()
                sha.update(chunk)
                if on_progress:
                    on_progress(label, completed, total)
        return sha.hexdigest()

    for filename, size, expected in files:
        check_stop()
        target = folder / filename
        partial = folder / (filename + '.part')
        if target.exists():
            if target.stat().st_size == size and digest(target, 'SHA-256: ' + filename) == expected:
                completed += size
                continue
            # Do not destroy an existing user file that did not pass verification.
            raise ValueError('Existing file failed verification: ' + str(target))
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > size:
            partial.unlink()
            offset = 0
        if shutil.disk_usage(folder).free < size - offset + 64 * 1024 * 1024:
            raise OSError('Insufficient disk space: ' + str(folder))
        if offset < size:
            headers = {'User-Agent': 'SeeName-model-downloader', 'Accept-Encoding': 'identity'}
            if offset:
                headers['Range'] = f'bytes={offset}-'
            with urlopen(Request(file_url(name, filename, source), headers=headers), timeout=20) as response:
                if not response.url.startswith('https://'):
                    raise ValueError('Download redirected away from HTTPS')
                if response.status == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                    if not match or int(match[1]) != offset or int(match[3]) != size:
                        raise ValueError('Invalid Content-Range')
                elif response.status == 200:
                    offset = 0  # Server ignored Range; restart instead of appending.
                else:
                    raise ValueError('Unexpected HTTP status: ' + str(response.status))
                with partial.open('ab' if offset else 'wb') as stream:
                    while True:
                        check_stop()
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        if offset + len(chunk) > size:
                            raise ValueError('Download exceeds expected size')
                        stream.write(chunk)
                        offset += len(chunk)
                        if on_progress:
                            on_progress(filename, completed + offset, total)
        if partial.stat().st_size != size:
            raise ValueError('Incomplete download: ' + filename)
        if digest(partial, 'SHA-256: ' + filename) != expected:
            partial.unlink()
            raise ValueError('SHA-256 mismatch; retry download: ' + filename)
        check_stop()
        os.replace(partial, target)
        completed += size
        if on_progress:
            on_progress(filename, completed, total)
    return str(folder)
