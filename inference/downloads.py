"""Verified, resumable HTTPS weight downloads; no external programs."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from strings import T


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


class DownloadError(Exception):
    """为什么失败，连同「下一步能干什么」一起带出来。

    以前所有失败都是裸的英文 ``ValueError('SHA-256 mismatch; retry download: …')``，
    走到界面上就是「失败：SHA-256 mismatch; retry download: xxx.gguf」——
    小白既看不懂，也不知道该删哪个文件。现在按 ``kind`` 分类，
    卡片才好在「校验不过」这一种上多给一个「删掉重下」的按钮。

    kind 取值：verify（盘上有同名但内容不对）/ space / network / source /
    https / range / http / size / incomplete / sha / write
    """

    def __init__(self, message, kind='', path='', filename=''):
        super().__init__(message)
        self.kind = kind
        self.path = str(path or '')
        self.filename = filename or ''


def _size_text(count: int) -> str:
    """「还差 1.2 GB / 300 MB / 40 KB」——别把几兆说成 0 MB。"""
    count = int(count or 0)
    if count >= 1024 ** 3:
        return '%.2f GB' % (count / float(1024 ** 3))
    if count >= 1024 ** 2:
        return '%.0f MB' % (count / float(1024 ** 2))
    if count > 0:
        return '%d KB' % max(1, count // 1024)
    return '0'


def _network_reason(error) -> str:
    """把 urlopen 那串英文原因翻成一句人话。"""
    reason = getattr(error, 'reason', error)
    text = str(reason)
    low = text.lower()
    if 'getaddrinfo' in low or 'name or service' in low or 'nodename' in low:
        return T('域名解析失败，这台电脑上不了这个网址')
    if 'timed out' in low or 'timeout' in low or isinstance(reason, socket.timeout):
        return T('连接超时')
    if 'certificate' in low or 'ssl' in low:
        return T('安全连接（HTTPS）没建立起来')
    if 'refused' in low:
        return T('对方拒绝连接')
    return text


def model_folder(name):
    return 'Qwen3-VL-' + ('2B' if '-2B-' in name else '4B') + '-Instruct-GGUF'


def file_url(name, filename, source):
    repo = 'Qwen/' + model_folder(name)
    if source == 'modelscope':
        return f'https://modelscope.cn/models/{repo}/resolve/master/{filename}'
    if source == 'huggingface':
        return f'https://huggingface.co/{repo}/resolve/main/{filename}'
    raise DownloadError(T('下载源不认识：%s') % source, 'source')


def download_pair(name, root, source, on_progress=None, should_stop=None):
    """Only verified complete files receive their final names; partials survive cancel.

    A fixed SHA-256 identifies content across sources and prevents accepting a
    changed remote file when resuming. A mismatched partial is removed on retry,
    but an existing *final* file is never touched: that case raises
    :class:`DownloadError` with ``kind='verify'`` so the window can offer to
    delete it explicitly. Caller serializes downloads and directory changes.
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
        try:
            with path.open('rb') as stream:
                while chunk := stream.read(4 * 1024 * 1024):
                    check_stop()
                    sha.update(chunk)
                    if on_progress:
                        on_progress(label, completed, total)
        except OSError as error:
            raise DownloadError(T('读不了这个文件（可能被别的程序占着）：%s') % error,
                                'read', path=path, filename=path.name) from error
        return sha.hexdigest()

    for filename, size, expected in files:
        check_stop()
        target = folder / filename
        partial = folder / (filename + '.part')
        if target.exists():
            if target.stat().st_size == size and digest(target, 'SHA-256: ' + filename) == expected:
                completed += size
                continue
            # 不擅自删用户盘上的文件：只报清楚是哪个文件、为什么不认它，
            # 界面上给一个「删掉重下」的按钮，点了、确认了才删。
            raise DownloadError(
                T('已有同名文件，但内容和官方的对不上：%s') % filename,
                'verify', path=target, filename=filename)
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > size:
            partial.unlink()
            offset = 0
        free = shutil.disk_usage(folder).free
        if free < size - offset + 64 * 1024 * 1024:
            raise DownloadError(
                T('磁盘空间不够：%s 至少还要 %s，现在只剩 %s。\n\n腾点空间，或把模型库换到别的盘再试。')
                % (folder, _size_text(size - offset), _size_text(free)),
                'space', path=folder)
        if offset < size:
            headers = {'User-Agent': 'SeeName-model-downloader', 'Accept-Encoding': 'identity'}
            if offset:
                headers['Range'] = f'bytes={offset}-'
            url = file_url(name, filename, source)
            try:
                response = urlopen(Request(url, headers=headers), timeout=20)
            except HTTPError as error:
                raise DownloadError(
                    T('下载源说这个文件下不了（HTTP %d）：%s\n\n换个「下载来源」再试。')
                    % (error.code, filename), 'http', filename=filename) from error
            except (URLError, socket.timeout, TimeoutError, OSError) as error:
                raise DownloadError(
                    T('连不上下载服务器：%s\n\n多数是网络不通，或这个源在国内访问不了。'
                      '把底部「下载来源」换成另一个再试。') % _network_reason(error),
                    'network', filename=filename) from error
            with response:
                if not response.url.startswith('https://'):
                    raise DownloadError(T('下载被跳转到了不安全的地址，已停止。'),
                                        'https', filename=filename)
                if response.status == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                    if not match or int(match[1]) != offset or int(match[3]) != size:
                        raise DownloadError(
                            T('这个下载源返回的数据范围和预期对不上，已停止。\n\n'
                              '把模型文件夹里的 %s.part 删掉，重新下一遍。') % filename,
                            'range', filename=filename)
                elif response.status == 200:
                    offset = 0  # Server ignored Range; restart instead of appending.
                else:
                    raise DownloadError(
                        T('下载源返回了意外状态（HTTP %d），可能文件还没传完或被限流，'
                          '过一会儿再试。') % response.status, 'http', filename=filename)
                try:
                    with partial.open('ab' if offset else 'wb') as stream:
                        while True:
                            check_stop()
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            if offset + len(chunk) > size:
                                raise DownloadError(T('下载到的数据比预期大，已停止。'),
                                                    'size', filename=filename)
                            stream.write(chunk)
                            offset += len(chunk)
                            if on_progress:
                                on_progress(filename, completed + offset, total)
                except OSError as error:
                    raise DownloadError(
                        T('写文件失败（磁盘满了或没有权限）：%s') % error,
                        'write', path=partial, filename=filename) from error
        if partial.stat().st_size != size:
            raise DownloadError(
                T('下载没有下完：%s 还差 %s。\n\n点「继续」会接着下。')
                % (filename, _size_text(size - partial.stat().st_size)),
                'incomplete', filename=filename)
        if digest(partial, 'SHA-256: ' + filename) != expected:
            partial.unlink()
            raise DownloadError(
                T('%s 下完之后校验没通过（文件在传输中坏了），已删掉这一份，'
                  '点「重试」重新下。') % filename, 'sha', filename=filename)
        check_stop()
        os.replace(partial, target)
        completed += size
        if on_progress:
            on_progress(filename, completed, total)
    return str(folder)
