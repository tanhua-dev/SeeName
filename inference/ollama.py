# -*- coding: utf-8 -*-
"""Existing Ollama adapter, including its private process and HTTP transport."""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from typing import Callable
import urllib.request

from PIL import Image

from strings import T
from .interfaces import BackendStatus, InferenceResult, LoadedModel


class OllamaRuntime:
    """Shared transport for inference and model downloads; does not import engine."""

    def __init__(self, *, app_dir: str, user_data: str,
                 config: Callable[[], dict], save_config: Callable[..., dict],
                 default_port: int = 11500, keep_alive: str = '15m', opener=None):
        self.app_dir = app_dir
        self.user_data = user_data
        self.data_dir = os.path.join(user_data, 'data')
        self.runtime_dir = os.path.join(app_dir, 'runtime', 'ollama')
        self.is_win = sys.platform == 'win32' or os.name == 'nt'
        self.is_mac = sys.platform == 'darwin'
        self.config = config
        self.save_config = save_config
        self.default_port = default_port
        self.keep_alive = keep_alive
        self.creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self.state = {'port': 0, 'proc': None, 'source': '', 'exe': ''}
        self.opener = opener if opener is not None else urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def find_runtime(self):
        """定位推理运行时，返回 (exe, source)，source = 'bundled' | 'system'。

        优先用软件自带的 runtime\\ollama —— 用户因此不需要另外安装 Ollama；
        实在找不到才退回系统安装的 Ollama（Mac 上就是 Ollama.app / brew 装的）。
        """
        exe = 'ollama.exe' if self.is_win else 'ollama'
        cands = [
            (os.path.join(self.runtime_dir, exe), 'bundled'),
            (os.path.join(self.app_dir, exe), 'bundled'),
        ]
        if self.is_win:
            la = os.environ.get('LOCALAPPDATA', '')
            if la:
                cands.append((os.path.join(la, 'Programs', 'Ollama', 'ollama.exe'), 'system'))
        else:
            cands += [
                ('/Applications/Ollama.app/Contents/Resources/ollama', 'system'),
                ('/usr/local/bin/ollama', 'system'),
                ('/opt/homebrew/bin/ollama', 'system'),
            ]
        for c, src in cands:
            if c and os.path.isfile(c):
                return c, src
        w = shutil.which('ollama')
        if w:
            return w, 'system'
        raise RuntimeError(T('未找到推理运行时：软件自带的 runtime 目录缺失，'
                             '系统里也没装 Ollama。'))

    def models_dir(self, create=True) -> str:
        """模型库目录。默认就是软件自己的 models 文件夹（模型包下载到这里）。

        Mac 上 .app 内部是只读的，所以顺序反过来：软件旁边有 models 整包
        （便携分发）就用它；没有才落到 ~/Library/Application Support/SeeName/models。
        """
        d = self.config().get('models_dir')
        if not d:
            near = os.path.join(self.app_dir, 'models')
            if os.path.isdir(near) or not (self.is_mac and getattr(sys, 'frozen', False)):
                d = near
            else:
                d = os.path.join(self.user_data, 'models')
        if create:
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass
        return d

    def server_port(self) -> int:
        return int(self.state['port'] or self.config().get('port') or self.default_port)

    def server_url(self) -> str:
        return 'http://127.0.0.1:%d' % self.server_port()

    def url(self, path: str) -> str:
        return self.server_url() + path

    def port_open(self, t=1.2, port=None) -> bool:
        s = socket.socket()
        s.settimeout(t)
        try:
            s.connect(('127.0.0.1', int(port or self.server_port())))
            return True
        except Exception:
            return False
        finally:
            s.close()

    def is_ollama(self, port, t=1.5) -> bool:
        """该端口上跑的确实是 Ollama 服务吗？"""
        try:
            req = urllib.request.Request('http://127.0.0.1:%d/' % int(port))
            with self.opener.open(req, timeout=t) as r:
                return b'ollama' in r.read(400).lower()
        except Exception:
            return False

    def server_log_path(self) -> str:
        os.makedirs(self.data_dir, exist_ok=True)
        return os.path.join(self.data_dir, 'runtime.log')

    def runtime_status(self) -> dict:
        """运行时信息，供界面展示。"""
        try:
            exe, src = self.find_runtime()
            found = True
        except Exception:
            exe, src, found = '', 'none', False
        return {
            'found': found, 'exe': exe, 'source': src,
            'bundled': src == 'bundled',
            'label': {'bundled': T('软件自带运行时'), 'system': T('系统安装的 Ollama'),
                      'none': T('未找到运行时')}.get(src, src),
            'port': self.server_port(), 'url': self.server_url(),
            'models_dir': self.models_dir(),
            'running': self.port_open(0.4),
        }

    def start_server(self, on_log=None, timeout=120) -> bool:
        """确保有可用的推理服务；没有就用「自带运行时」拉起来。

        自带运行时使用私有端口 + 软件自己的模型库，所以用户不需要另外装 Ollama。
        """
        say = on_log or (lambda s: None)
        if self.port_open(1.0):                      # 已经在跑，直接复用
            return True
        try:
            exe, src = self.find_runtime()
        except Exception as e:
            say(T('[错误] %s') % e)
            return False
        self.state['exe'], self.state['source'] = exe, src

        base = int(self.config().get('port') or self.default_port)
        for p in [base] + [base + i for i in range(1, 6)]:
            if self.port_open(0.4, p):               # 端口被别的程序占了，换一个
                continue
            env = dict(os.environ)
            for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
                      'ALL_PROXY', 'all_proxy'):
                env.pop(k, None)
            env['OLLAMA_HOST'] = '127.0.0.1:%d' % p
            env['OLLAMA_MODELS'] = self.models_dir()
            env['OLLAMA_KEEP_ALIVE'] = self.keep_alive
            env['OLLAMA_ORIGINS'] = '*'
            try:
                os.makedirs(self.data_dir, exist_ok=True)
                lf = open(self.server_log_path(), 'ab')
                lf.write((T('\n\n===== 启动 %s（端口 %d，模型库 %s）=====\n')
                          % (exe, p, self.models_dir())).encode('utf-8'))
                lf.flush()
                proc = subprocess.Popen(
                    [exe, 'serve'], stdout=lf, stderr=subprocess.STDOUT,
                    env=env, creationflags=self.creationflags,
                    # POSIX 上放进独立进程组，退出时才能连 runner 子进程一起杀
                    start_new_session=not self.is_win)
                self.state['proc'] = proc
                self.state['port'] = p
                self.save_config(port=p)
            except Exception as e:
                say(T('[错误] 启动自带运行时失败：%r') % e)
                continue
            say(T('推理运行时已启动（%s，端口 %d）') % (src, p))
            t0 = time.time()
            while time.time() - t0 < timeout:
                if self.port_open(0.6):
                    return True
                if proc.poll() is not None:
                    say(T('[错误] 运行时进程提前退出（code=%s），详见 %s')
                        % (proc.returncode, self.server_log_path()))
                    break
                time.sleep(0.8)
        return False

    def shutdown_server(self):
        """退出/切换模型库时收掉自己拉起来的服务，释放显存。

        连 runner 子进程一起杀（terminate 只杀得了直接子进程，加载着模型的
        runner 会变成孤儿继续占显存）：Windows 用 taskkill /T，POSIX 用进程组
        信号（start_server 里已经开了独立会话）。
        """
        p = self.state.get('proc')
        if p is not None and p.poll() is None:
            killed = False
            if not self.is_win:
                try:
                    os.killpg(os.getpgid(p.pid), 9)
                    killed = True
                except Exception:
                    killed = False
            if not killed:
                try:
                    subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=self.creationflags, timeout=10)
                    killed = True
                except Exception:
                    killed = False
            if not killed:
                try:
                    p.terminate()
                except Exception:
                    pass
            t0 = time.time()
            while time.time() - t0 < 8 and self.port_open(0.3):
                time.sleep(0.3)
        self.state['proc'] = None

    def server_info(self) -> dict:
        """查询服务与显存占用（/api/ps）。"""
        if not self.port_open(0.6):
            return {'up': False, 'models': []}
        try:
            req = urllib.request.Request(self.url('/api/ps'))
            with self.opener.open(req, timeout=4) as r:
                d = json.loads(r.read().decode('utf-8'))
            out = []
            for m in d.get('models') or []:
                out.append({
                    'name': m.get('name') or m.get('model') or '',
                    'size': m.get('size') or 0,
                    'vram': m.get('size_vram') or 0,
                })
            return {'up': True, 'models': out}
        except Exception:
            return {'up': True, 'models': []}


class OllamaBackend:
    """InferenceBackend implementation using the existing local Ollama runtime."""

    def __init__(self, runtime: OllamaRuntime, *, warmup_prompt: str, context_size: int = 2048):
        self._runtime = runtime
        self._warmup_prompt = warmup_prompt
        self._context_size = context_size

    def start(self, on_log=None, timeout=120) -> bool:
        return self._runtime.start_server(on_log=on_log, timeout=timeout)

    def load(self, model: str, timeout=180) -> tuple[bool, str]:
        try:
            image = io.BytesIO()
            Image.new('RGB', (64, 64), (128, 128, 128)).save(image, 'JPEG')
            self.describe(image.getvalue(), model=model, prompt=self._warmup_prompt, timeout=timeout)
            return True, ''
        except Exception as error:
            return False, repr(error)

    def describe(self, image: bytes, *, model: str, prompt: str,
                 timeout=300, max_tokens: int = 64) -> InferenceResult:
        body = json.dumps({
            'model': model,
            'messages': [{'role': 'user', 'content': prompt or self._warmup_prompt,
                          'images': [base64.b64encode(image).decode('ascii')]}],
            'stream': False, 'keep_alive': self._runtime.keep_alive,
            'options': {'temperature': 0.2, 'num_ctx': self._context_size, 'num_predict': max_tokens},
        }).encode('utf-8')
        req = urllib.request.Request(self._runtime.url('/api/chat'), data=body,
                                     headers={'Content-Type': 'application/json'})
        t0 = time.time()
        with self._runtime.opener.open(req, timeout=timeout) as r:
            d = json.loads(r.read().decode('utf-8'))
        return InferenceResult((d.get('message') or {}).get('content', ''),
                               time.time() - t0, d.get('prompt_eval_count') or 0, d)

    def status(self) -> BackendStatus:
        info = self._runtime.server_info()
        return BackendStatus(info['up'], tuple(
            LoadedModel(model['name'], model['size'], model['vram'])
            for model in info['models']))

    def diagnostics(self) -> dict:
        info = self._runtime.runtime_status()
        return dict(backend='Ollama', device='', models_dir=info['models_dir'],
                    initialized=info['running'], found=info['found'], busy='', error='')

    def close(self) -> None:
        self._runtime.shutdown_server()


MODEL_CATALOG = [
    {'name': 'qwen3-vl:4b-instruct', 'size': T('约 3.3 GB'), 'tag': T('推荐'),
     'note': T('视觉主力。4B 量化后可全部进显存，实测生成约 55–62 tok/s，本机首选。')},
    {'name': 'qwen3-vl:8b-instruct', 'size': T('约 6.0 GB'), 'tag': '',
     'note': T('识别质量更高，但需 8GB 以上显存；6GB 显存会溢出到内存、明显变慢。')},
    {'name': 'minicpm-v', 'size': T('约 5.5 GB'), 'tag': '',
     'note': T('通用视觉模型，中文场景描述尚可，速度中等。')},
    {'name': 'qwen2.5vl:7b', 'size': T('约 6.0 GB'), 'tag': '',
     'note': T('上一代视觉模型；7B 在本机溢出到 CPU，单张图约 191 秒，不推荐。')},
    {'name': 'llama3.2-vision:11b', 'size': T('约 7.9 GB'), 'tag': '',
     'note': T('英文场景表现更好，中文描述偏弱，显存需求大。')},
    {'name': 'gemma3:4b', 'size': T('约 3.3 GB'), 'tag': '',
     'note': T('Google 多模态小模型，可作为备选对比。')},
]


def _norm_model(n: str) -> str:
    """去掉 registry / namespace 前缀，只留 name:tag，用于比较。"""
    n = (n or '').strip()
    return n.split('/')[-1] if n else ''


class OllamaModelStore:
    """ModelStore implementation sharing the inference transport and configuration."""

    catalog = MODEL_CATALOG

    def __init__(self, runtime: OllamaRuntime):
        self._runtime = runtime

    def directory(self, create=True) -> str:
        return self._runtime.models_dir(create=create)

    def set_directory(self, path, on_log=None) -> bool:
        """切换模型库目录（重启自带服务使其生效）。"""
        say = on_log or (lambda s: None)
        path = os.path.abspath(path)
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            say(T('[错误] 无法使用该目录：%r') % e)
            return False
        self._runtime.save_config(models_dir=path)
        say(T('模型库已切换为 %s，正在重启推理服务…') % path)
        self._runtime.shutdown_server()
        self._runtime.state['port'] = 0
        return self._runtime.start_server(on_log=say)

    def system_ollama_models(self) -> str:
        """探测本机已安装的 Ollama 模型库，用于一键复用（免去重新下载模型包）。"""
        cands = []
        env = os.environ.get('OLLAMA_MODELS')
        if env:
            cands.append(env)
        cands.append(os.path.join(os.path.expanduser('~'), '.ollama', 'models'))
        la = os.environ.get('LOCALAPPDATA')
        if la:
            cands.append(os.path.join(la, 'Ollama', 'models'))
        for c in cands:
            try:
                if c and os.path.isdir(c) and os.listdir(c):
                    return c
            except Exception:
                pass
        return ''

    def local_models_in(self, path: str) -> int:
        """粗略统计某个模型库里有多少个模型（按 manifests 下的文件计）。"""
        n = 0
        for _root, _dirs, files in os.walk(os.path.join(path, 'manifests')):
            n += len([f for f in files if not f.startswith('.')])
        return n

    def source(self) -> str:
        """当前模型下载源；空字符串表示 Ollama 官方源。"""
        return (self._runtime.config().get('registry') or '').strip()

    def set_source(self, url: str) -> str:
        """设置模型下载源（传空字符串即恢复 Ollama 官方源）。"""
        url = (url or '').strip().rstrip('/')
        for p in ('http://', 'https://'):
            if url.startswith(p):
                url = url[len(p):]
        self._runtime.save_config(registry=url)
        return url

    def installed(self, retry: int = 2) -> dict:
        """返回 {模型名: 字节数}；服务不可用或查询失败时返回空字典。

        服务刚启动的瞬间 /api/tags 可能返回 5xx，所以这里带重试。
        """
        for i in range(retry + 1):
            try:
                req = urllib.request.Request(self._runtime.url('/api/tags'))
                with self._runtime.opener.open(req, timeout=8) as r:
                    d = json.loads(r.read().decode('utf-8'))
                out = {}
                for m in d.get('models') or []:
                    n = m.get('name') or m.get('model') or ''
                    if n:
                        out[n] = m.get('size') or 0
                return out
            except Exception:
                if i < retry:
                    time.sleep(0.6)
        return {}

    def exists(self, name: str, installed: dict | None = None) -> bool:
        """判断模型是否已下载（忽略 registry 前缀，兼容省略 :latest 的写法）。"""
        got = installed if installed is not None else self.installed()
        if not got:
            return False
        want = _norm_model(name)
        for k in got:
            kk = _norm_model(k)
            if kk == want:
                return True
            if ':' not in want and kk == want + ':latest':
                return True
        return False

    def problems(self) -> dict[str, str]:
        return {}

    def download(self, name: str, on_progress=None, should_stop=None) -> str:
        """流式下载模型。

        下载源取 config 里的 registry（默认空＝Ollama 官方源）；填了自定义镜像
        时会拼成「镜像/模型名」再拉取。
        on_progress(status, completed, total) 会被频繁调用，用于刷新进度。
        返回 'success' / 'stopped'；服务端报错抛 RuntimeError。
        """
        reg = (self._runtime.config().get('registry') or '').strip().rstrip('/')
        full = name if (not reg or '/' in name) else (reg + '/' + name)
        body = json.dumps({'model': full, 'stream': True}).encode('utf-8')
        req = urllib.request.Request(self._runtime.url('/api/pull'), data=body,
                                     headers={'Content-Type': 'application/json'})
        last = ''
        with self._runtime.opener.open(req, timeout=3600) as r:
            for raw in r:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    d = json.loads(raw.decode('utf-8'))
                except Exception:
                    continue
                if d.get('error'):
                    msg = str(d['error'])
                    low = msg.lower()
                    if 'not exist' in low or 'not found' in low or 'no such' in low:
                        msg = T('模型不存在，请检查名称拼写（%s）') % msg
                    raise RuntimeError(msg)
                st = d.get('status') or ''
                last = st
                if on_progress:
                    on_progress(st, d.get('completed') or 0, d.get('total') or 0)
                if should_stop and should_stop():
                    return 'stopped'
        return last or 'success'
