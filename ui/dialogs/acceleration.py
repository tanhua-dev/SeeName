# -*- coding: utf-8 -*-
"""GPU acceleration: an embeddable panel plus a standalone dialog wrapper.

The panel is embedded in the settings window so that the whole app stays two
levels deep (main window → settings). The dialog wrapper is kept for callers
that still want a window of their own.
"""
from __future__ import annotations

import queue
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import engine
from inference.downloads import DownloadCancelled
from strings import T
from .. import theme
from ..common import APP_TITLE, human_size
from ..controls import fit_to_content, gpu_advice

def pretty_version(identifier: str) -> str:
    """组件的 id 是 llama 版本加哈希，直接显示没人看得懂，收敛成 v1.2.3-rc.3。"""
    tail = identifier.split('-windows-x64-')[-1]
    tail = re.sub(r'v(\d+)-(\d+)-(\d+)', r'v\1.\2.\3', tail)
    tail = re.sub(r'-(rc|beta|alpha)-(\d+)', r'-\1.\2', tail)
    return tail


BACKENDS = (('Vulkan', T('Vulkan（推荐）'), T('NVIDIA / AMD / Intel 都能用，显存不够时可借用系统内存')),
            ('CUDA', T('CUDA（实验）'), T('仅 NVIDIA；显存不足时反而更慢，无 tensor core 的老卡不建议')))


class AccelerationPanel(tk.Frame):
    """Vulkan / CUDA component management, without its own window."""

    def __init__(self, master, app, bg=theme.C_CARD):
        super().__init__(master, bg=bg)
        self.app = app
        self._bg = bg
        self.cancel_download = threading.Event()
        self.events: queue.Queue = queue.Queue(maxsize=1)
        self.downloading = False
        self.backend = 'Vulkan'
        self._result = ''
        self._wait_job = None
        self._poll_job = None
        self.version_vars: dict = {}
        self.version_combos: dict = {}
        self.version_labels: dict = {}
        self.action_buttons: dict = {}
        self._build()
        self.refresh()

    # ---------------------------------------------------------------- 构建
    def _build(self):
        bg = self._bg
        tk.Label(self, text=T('可选 GPU 加速（实验功能）'), bg=bg, fg=theme.C_TEXT,
                 font=(theme.FONT, 11, 'bold')).pack(anchor='w')
        tk.Label(self, text=T('装了才能用显卡；不装就用 CPU。显卡驱动要自己装好。'),
                 bg=bg, fg=theme.C_SUB, font=(theme.FONT, 9),
                 justify='left', wraplength=520).pack(anchor='w', pady=(2, theme.S2))

        self.lbl_device = tk.Label(self, bg=bg, fg=theme.C_SUB, font=(theme.FONT, 10),
                                   justify='left', anchor='w', wraplength=520)
        self.lbl_device.pack(anchor='w')
        self.lbl_advice = tk.Label(self, bg=bg, fg=theme.C_SUB, font=(theme.FONT, 9),
                                   justify='left', anchor='w', wraplength=520)
        self.lbl_advice.pack(anchor='w', pady=(0, theme.S2))

        for backend, title, hint in BACKENDS:
            box = tk.Frame(self, bg=bg)
            box.pack(fill='x', pady=(0, theme.S2))
            head = tk.Frame(box, bg=bg)
            head.pack(fill='x')
            tk.Label(head, text=title, bg=bg, fg=theme.C_TEXT,
                     font=(theme.FONT, 10, 'bold')).pack(side='left')
            button = ttk.Button(head, text=T('启用 %s') % backend,
                                command=lambda name=backend: self.download(name))
            button.pack(side='right')
            variable = tk.StringVar()
            combo = ttk.Combobox(head, textvariable=variable, state='readonly', width=28)
            combo.pack(side='right', padx=(0, theme.S2))
            combo.bind('<<ComboboxSelected>>', lambda _e, name=backend: self._refresh_action(name))
            tk.Label(box, text=hint, bg=bg, fg=theme.C_SUB, font=(theme.FONT, 9),
                     justify='left', anchor='w', wraplength=520).pack(anchor='w', pady=(1, 0))
            self.version_vars[backend] = variable
            self.version_combos[backend] = combo
            self.version_labels[backend] = {}
            self.action_buttons[backend] = button

        self.progress = ttk.Progressbar(self, maximum=100)
        self.progress.pack(fill='x', pady=(theme.S2, theme.S1))
        self.message = tk.Label(self, bg=bg, fg=theme.C_SUB, font=(theme.FONT, 9),
                                justify='left', anchor='w', wraplength=520)
        self.message.pack(anchor='w')

        bar = tk.Frame(self, bg=bg)
        bar.pack(fill='x', pady=(theme.S2, 0))
        self.cpu_button = ttk.Button(bar, text=T('使用 CPU（不加速）'), command=self.cpu)
        self.cpu_button.pack(side='left')
        self.cancel_button = ttk.Button(bar, text=T('暂停下载'), state='disabled',
                                        command=self.cancel_download.set)
        self.cancel_button.pack(side='left', padx=theme.S2)

    # ---------------------------------------------------------------- 状态
    def busy(self) -> bool:
        return bool(self.app.running or self.app._model_busy)

    def _selected_version(self, backend, status=None):
        status = status or engine.accelerator.status(backend)
        selected_id = self.version_labels[backend].get(self.version_vars[backend].get())
        return next((item for item in status.get('versions', []) if item['id'] == selected_id), None)

    def _refresh_action(self, backend):
        selected = self._selected_version(backend)
        available = bool(selected and selected.get('available'))
        self.action_buttons[backend].configure(
            text=T('启用 %s') % backend if selected and selected['installed']
            else T('下载并启用 %s') % backend,
            state='normal' if (not self.busy() and available) else 'disabled')

    def refresh(self):
        try:
            rt = engine.runtime_status()
            device = rt.get('device') or T('CPU')
        except Exception:
            device = T('CPU')
        self.lbl_device.configure(text=T('当前设备：%s') % device)
        try:
            self.lbl_advice.configure(text=gpu_advice())
        except Exception:
            self.lbl_advice.configure(text='')

        busy = self.busy()
        for backend, _title, _hint in BACKENDS:
            status = engine.accelerator.status(backend)
            labels, choices = [], {}
            for item in status.get('versions', []):
                label = pretty_version(item['id'])
                label += ' · ' + (T('已安装') if item['installed'] else T('可下载'))
                labels.append(label)
                choices[label] = item['id']
            self.version_labels[backend] = choices
            combo = self.version_combos[backend]
            combo.configure(values=labels, state='readonly' if (labels and not busy) else 'disabled')
            selected_label = next((lb for lb, ident in choices.items()
                                   if ident == status.get('id')), '')
            if selected_label:
                self.version_vars[backend].set(selected_label)
            selected = self._selected_version(backend, status)
            available = bool(selected and selected.get('available'))
            self.action_buttons[backend].configure(
                text=T('启用 %s') % backend if selected and selected['installed']
                else T('下载并启用 %s') % backend,
                state='normal' if (not busy and available) else 'disabled')
            error = status.get('error') or ''
            if error and backend == 'CUDA':
                self.message.configure(text=error, fg=theme.C_ERR)
        self.cpu_button.configure(state='disabled' if busy else 'normal')

    # ---------------------------------------------------------------- 动作
    def cpu(self):
        self._set_message(T('正在切换到 CPU…'), theme.C_SUB)

        def work():
            engine.shutdown_server()
            engine.accelerator.enable_cpu()
            engine.start_server()
            return T('已切换到 CPU')

        if self.app.run_model_action(work, T('已切换到 CPU')):
            self._watch()

    def download(self, backend, component_id=None):
        if self.busy():
            return
        self.backend = backend
        status = engine.accelerator.status(backend)
        wanted = component_id or self.version_labels[backend].get(self.version_vars[backend].get())
        selected = next((item for item in status.get('versions', []) if item['id'] == wanted), None)
        if not selected:
            self._set_message(T('没有可用的 %s 组件版本。') % backend, theme.C_WARN)
            return
        component_id = selected['id']
        if not selected['installed']:
            prompt = T('下载 %s 加速组件（%s）并启用？') % (backend, human_size(selected['size']))
            if backend == 'CUDA':
                prompt += '\n\n' + T('继续表示你已阅读并同意 NVIDIA CUDA Toolkit EULA。')
                prompt += '\nhttps://docs.nvidia.com/cuda/eula/'
            if not messagebox.askyesno(APP_TITLE, prompt, parent=self.winfo_toplevel()):
                return
        self.cancel_download.clear()
        self.downloading = True
        self.cancel_button.configure(state='normal')
        self.progress.configure(value=0)
        self._set_message(T('正在下载 %s 组件…') % backend, theme.C_ACCENT)

        def progress(done, total):
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            self.events.put_nowait((done, total))

        def work():
            try:
                engine.accelerator.install(backend, progress, self.cancel_download.is_set,
                                           component_id)
                if self.cancel_download.is_set():
                    raise DownloadCancelled()
                component = engine.accelerator.components[backend]
                if component._loaded_id and component._loaded_id != component_id:
                    engine.accelerator.enable(backend, True, component_id)
                    return T('%s 已准备好；重启 SeeName 后生效。') % backend
                engine.shutdown_server()
                engine.accelerator.enable(backend, True, component_id)
                if not engine.start_server():
                    raise RuntimeError(engine.runtime_status()['error'])
                component_error = engine.accelerator.components[backend].error
                if component_error:
                    raise RuntimeError(component_error)
                return T('%s 组件已启用，识别时加载模型。') % backend
            except DownloadCancelled:
                # 用户主动暂停不是错误：保留 .part，下次接着下
                self._paused = True
                return T('已暂停下载，进度已保留；再点一次「下载并启用」会接着下。')

        self._paused = False
        self.app.run_model_action(work, T('%s 组件已启用。') % backend)
        self._watch()

    def _watch(self):
        self.refresh()
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        self._poll_job = self.after(120, self._poll)

    def _poll(self):
        self._poll_job = None
        try:
            done, total = self.events.get_nowait()
            self.progress.configure(value=100 * done / max(1, total))
            self.message.configure(text='%s / %s' % (human_size(done), human_size(total)),
                                   fg=theme.C_SUB)
        except queue.Empty:
            pass
        if self.busy():
            self._poll_job = self.after(120, self._poll)
            return
        self.downloading = False
        self.cancel_button.configure(state='disabled')
        if getattr(self, '_paused', False):
            self._set_message(T('已暂停下载，进度已保留；再点一次会接着下。'), theme.C_WARN)
        elif self._result:
            self._set_message(self._result, theme.C_SUB)
        self.refresh()

    def _set_message(self, text, color=theme.C_SUB):
        try:
            self.message.configure(text=text, fg=color)
        except Exception:
            pass

    def stop(self):
        """Called when the host window closes mid-download."""
        for job in (self._poll_job, self._wait_job):
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
        if self.downloading:
            self.cancel_download.set()


class AccelerationDialog(tk.Toplevel):
    """Standalone window (kept for callers that do not use the settings dialog)."""

    def __init__(self, master, app):
        super().__init__(master)
        self.title(T('GPU 加速'))
        self.configure(bg=theme.C_BG)
        self.transient(master)
        self.panel = AccelerationPanel(self, app, bg=theme.C_BG)
        self.panel.pack(fill='both', expand=True, padx=theme.S4, pady=theme.S4)
        ttk.Button(self, text=T('关闭'), command=self._close).pack(pady=(0, theme.S3))
        fit_to_content(self, min_w=580, min_h=380)
        self.protocol('WM_DELETE_WINDOW', self._close)

    def refresh(self):
        self.panel.refresh()

    def _close(self):
        self.panel.stop()
        self.destroy()

    @property
    def cancel_download(self):
        return self.panel.cancel_download
