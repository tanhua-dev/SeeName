"""Optional Vulkan download and activation, using the model-operation queue."""
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import engine
from inference.downloads import DownloadCancelled
from strings import T
from ..common import APP_TITLE, human_size
from .. import theme


class AccelerationDialog(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.cancel_download = threading.Event()
        self.events = queue.Queue(maxsize=1)
        self.downloading = False
        self.backend = 'Vulkan'
        self._wait_job = None
        self.title(T('GPU 加速'))
        self.geometry('650x360')
        self.configure(bg=theme.C_BG)
        self.transient(master)
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        tk.Label(self, text=T('可选 GPU 加速（实验功能）'), bg=theme.C_BG,
                 fg=theme.C_TEXT, font=(theme.FONT, 13, 'bold')).pack(anchor='w', padx=16, pady=12)
        tk.Label(self, text=T('Vulkan 支持兼容的 NVIDIA / AMD / Intel 显卡；CUDA 支持 NVIDIA 显卡。需要对应显卡驱动；初始化失败时使用 CPU。'),
                 bg=theme.C_BG, fg=theme.C_SUB, wraplength=610, justify='left').pack(anchor='w', padx=16)
        self.status_text = tk.Label(self, bg=theme.C_BG, fg=theme.C_SUB, wraplength=610, justify='left')
        self.status_text.pack(anchor='w', padx=16, pady=12)
        self.progress = ttk.Progressbar(self, maximum=100)
        self.progress.pack(fill='x', padx=16)
        self.message = tk.Label(self, bg=theme.C_BG, fg=theme.C_SUB, wraplength=610, justify='left')
        self.message.pack(anchor='w', padx=16, pady=8)
        bar = tk.Frame(self, bg=theme.C_BG)
        bar.pack(fill='x', padx=16, pady=8)
        self.download_button = ttk.Button(bar, text=T('下载并启用 Vulkan'), command=lambda: self.download('Vulkan'))
        self.download_button.pack(side='left')
        self.cuda_button = ttk.Button(bar, text=T('下载并启用 CUDA'), command=lambda: self.download('CUDA'))
        self.cuda_button.pack(side='left', padx=8)
        self.cpu_button = ttk.Button(bar, text=T('使用 CPU'), command=self.cpu)
        self.cpu_button.pack(side='left', padx=8)
        self.cancel_button = ttk.Button(bar, text=T('取消下载'), command=self.cancel_download.set, state='disabled')
        self.cancel_button.pack(side='left')
        self.refresh()

    def refresh(self):
        vulkan_status = engine.accelerator.status('Vulkan')
        cuda_status = engine.accelerator.status('CUDA')
        status = vulkan_status if self.backend == 'Vulkan' else cuda_status
        busy = self.app.running or self.app._model_busy
        device = engine.runtime_status()['device']
        text = T('当前设备：%s\n组件目录：%s') % (device, status['directory'])
        if status['size']:
            text += '\n' + T('组件下载大小：%s') % human_size(status['size'])
        if status.get('devices'):
            text += '\n' + self.backend + ': ' + ', '.join(status['devices'])
        if status['error']:
            text += '\n' + status['error']
        self.status_text.configure(text=text)
        self.download_button.configure(text=T('启用 Vulkan') if vulkan_status['installed'] else T('下载并启用 Vulkan'),
            state='normal' if not busy and (vulkan_status['available'] or vulkan_status['installed']) else 'disabled')
        self.cuda_button.configure(text=T('启用 CUDA') if cuda_status['installed'] else T('下载并启用 CUDA'),
            state='normal' if not busy and (cuda_status['available'] or cuda_status['installed']) else 'disabled')
        self.cpu_button.configure(state='disabled' if busy else 'normal')

    def cpu(self):
        def work():
            engine.shutdown_server()
            engine.accelerator.enable_cpu()
            engine.start_server()
        self.app.run_model_action(work, T('已切换到 CPU'))
        self._wait_job = self.after(150, self._wait_action)
        self.refresh()

    def _wait_action(self):
        if self.app._model_busy:
            self._wait_job = self.after(150, self._wait_action)
        else:
            self._wait_job = None
            self.refresh()

    def download(self, backend):
        if self.app.running or self.app._model_busy:
            return
        self.backend = backend
        status = engine.accelerator.status(backend)
        if not status['installed']:
            prompt = T('下载 %s 加速组件（%s）并启用？') % (backend, human_size(status['size']))
            if backend == 'CUDA':
                prompt += '\n\n' + T('继续表示你已阅读并同意 NVIDIA CUDA Toolkit EULA。')
                prompt += '\nhttps://docs.nvidia.com/cuda/eula/'
            if not messagebox.askyesno(APP_TITLE, prompt, parent=self):
                return
        self.cancel_download.clear()
        self.downloading = True
        self.result = T('正在下载…')
        self.cancel_button.configure(state='normal')

        def progress(done, total):
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            self.events.put_nowait((done, total))

        def work():
            try:
                engine.accelerator.install(backend, progress, self.cancel_download.is_set)
                if self.cancel_download.is_set():
                    raise DownloadCancelled()
                engine.shutdown_server()
                engine.accelerator.enable(backend, True)
                if not engine.start_server():
                    raise RuntimeError(engine.runtime_status()['error'])
                component_error = engine.accelerator.components[backend].error
                if component_error:
                    raise RuntimeError(component_error)
                self.result = T('%s 组件已启用，识别时加载模型。') % backend
            except DownloadCancelled:
                self.result = T('下载已取消，保留进度，可重新点击下载继续。')
                raise RuntimeError(self.result) from None
            except Exception as error:
                self.result = str(error)
                raise

        self.app.run_model_action(work, T('%s 组件已启用，识别时加载模型。') % backend)
        self.refresh()
        self.after(100, self._poll)

    def _poll(self):
        try:
            done, total = self.events.get_nowait()
            self.progress.configure(value=100 * done / max(1, total))
            self.message.configure(text='%s / %s' % (human_size(done), human_size(total)))
        except queue.Empty:
            pass
        if self.app._model_busy:
            self.after(100, self._poll)
        else:
            self.downloading = False
            self.cancel_button.configure(state='disabled')
            self.message.configure(text=self.result)
            self.refresh()

    def on_close(self):
        if self.downloading:
            self.cancel_download.set()
            self.message.configure(text=T('正在取消下载，请稍候…'))
        else:
            if self._wait_job is not None:
                self.after_cancel(self._wait_job)
            self.destroy()
