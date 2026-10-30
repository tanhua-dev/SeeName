# -*- coding: utf-8 -*-
"""Local GGUF library selection; no inference on the Tk thread."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import engine
from strings import T
from .. import theme
from ..common import APP_TITLE, human_size, open_path
from inference.downloads import DOWNLOADS, DownloadCancelled


class ModelDialog(tk.Toplevel):
    """Choose a directory containing supported main-model/projector pairs."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.rows = {}
        self.cancel_download = threading.Event()
        self.downloading = False
        self.progress_queue = queue.Queue(maxsize=1)
        self.title(T('模型管理 · %s') % APP_TITLE)
        self.geometry('790x590')
        self.minsize(700, 470)
        self.configure(bg=theme.C_BG)
        self.transient(master)
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self._build()
        self.refresh()

    def _build(self):
        head = tk.Frame(self, bg=theme.C_BG)
        head.pack(fill='x', padx=14, pady=(12, 6))
        tk.Label(head, text=T('本地视觉模型 · GGUF'), bg=theme.C_BG, fg=theme.C_TEXT,
                 font=(theme.FONT, 13, 'bold')).pack(anchor='w')
        tk.Label(head, text=T('选择已有模型目录。主模型和匹配的 mmproj 文件须放在同一目录；也会检查下一层子目录。'),
                 bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 9), justify='left',
                 wraplength=740).pack(anchor='w', pady=(3, 0))
        self.lbl_stat = tk.Label(head, bg=theme.C_BG, fg=theme.C_SUB,
                                 font=(theme.FONT, 9), justify='left', wraplength=740)
        self.lbl_stat.pack(anchor='w', pady=(5, 0))
        wrap = tk.Frame(self, bg=theme.C_BG)
        wrap.pack(fill='both', expand=True, padx=14)
        self.canvas = tk.Canvas(wrap, bg=theme.C_CARD, highlightthickness=1,
                                highlightbackground=theme.C_LINE)
        vs = ttk.Scrollbar(wrap, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vs.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        vs.pack(side='right', fill='y')
        self.inner = tk.Frame(self.canvas, bg=theme.C_CARD)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind_all('<MouseWheel>', self._on_wheel)
        foot = tk.Frame(self, bg=theme.C_BG)
        foot.pack(fill='x', padx=14, pady=12)
        tk.Label(foot, text=T('2B 和 4B 均可下载 Q4 / Q8 主模型，共用各自的 Q8 视觉投影。可在“GPU 加速”中配置设备。'),
                 bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 9), wraplength=740,
                 justify='left').pack(anchor='w', pady=(0, 8))
        sources = tk.Frame(foot, bg=theme.C_BG)
        sources.pack(fill='x')
        self.source = tk.StringVar(value=engine.model_store.source())
        self.source_buttons = []
        for label, value in ((T('国外：Hugging Face'), 'huggingface'), (T('国内：ModelScope'), 'modelscope')):
            button = ttk.Radiobutton(sources, text=label, value=value, variable=self.source)
            button.pack(side='left', padx=4)
            self.source_buttons.append(button)
        self.btn_cancel = ttk.Button(sources, text=T('取消下载'), command=self.cancel_download.set, state='disabled')
        self.btn_cancel.pack(side='right')
        self.progress = ttk.Progressbar(foot, maximum=100)
        self.progress.pack(fill='x', pady=4)
        self.progress_text = tk.Label(foot, bg=theme.C_BG, fg=theme.C_SUB, wraplength=740)
        self.progress_text.pack(anchor='w')
        bar = tk.Frame(foot, bg=theme.C_BG)
        bar.pack(fill='x')
        ttk.Button(bar, text=T('刷新'), command=self.refresh).pack(side='left')
        ttk.Button(bar, text=T('打开模型库'), command=self.open_models_dir).pack(side='left', padx=8)
        self.btn_directory = ttk.Button(bar, text=T('选择模型目录…'), command=self.change_models_dir)
        self.btn_directory.pack(side='left')
        ttk.Button(bar, text=T('关闭'), command=self.on_close).pack(side='right')
        ttk.Button(bar, text=T('GPU 加速…'), command=self.app.open_acceleration).pack(side='right', padx=8)

    def _on_wheel(self, ev):
        try:
            w = self.winfo_containing(ev.x_root, ev.y_root)
            if w is not None and str(w.winfo_toplevel()) == str(self):
                self.canvas.yview_scroll(int(-ev.delta / 120), 'units')
        except tk.TclError:
            pass

    def refresh(self):
        installed = engine.installed_models()
        problems = engine.model_problems()
        self.lbl_stat.configure(text=T('可用模型 %d 个 · 模型库 %s') % (len(installed), engine.models_dir(False)))
        for widget in self.inner.winfo_children():
            widget.destroy()
        self.rows.clear()
        busy = self.app.running or getattr(self.app, '_model_busy', False)
        self.btn_directory.configure(state='disabled' if busy else 'normal')
        for item in engine.MODEL_CATALOG:
            name = item['name']
            ok = name in installed
            row = tk.Frame(self.inner, bg=theme.C_CARD)
            row.pack(fill='x', padx=10, pady=10)
            left = tk.Frame(row, bg=theme.C_CARD)
            left.pack(side='left', fill='x', expand=True)
            tk.Label(left, text=('✓ ' if ok else '• ') + name, bg=theme.C_CARD,
                     fg=theme.C_TEXT, font=(theme.FONT, 10, 'bold')).pack(anchor='w')
            text = ((T('已配对 · 文件大小 %s') % human_size(installed[name])) if ok
                    else problems.get(name, T('模型文件不完整')))
            tk.Label(left, text=text, bg=theme.C_CARD, fg=theme.C_SUB,
                     font=(theme.FONT, 9), wraplength=560, justify='left').pack(anchor='w', pady=(3, 0))
            downloadable = name in DOWNLOADS
            if downloadable and not ok:
                text += ' · ' + human_size(sum(f[1] for f in DOWNLOADS[name]))
                # Include the full pair size before a download is requested.
                left.winfo_children()[-1].configure(text=text)
            button = ttk.Button(row, text=T('使用') if ok else T('下载') if downloadable else T('缺少文件'), width=10,
                                state='normal' if (ok or downloadable) and not busy else 'disabled',
                                command=lambda n=name, ready=ok: self.use(n) if ready else self.download(n))
            button.pack(side='right', padx=8)
            self.rows[name] = {'btn': button, 'installed': ok}

    def download(self, name):
        if self.app.running or self.app._model_busy:
            return
        size = human_size(sum(f[1] for f in DOWNLOADS[name]))
        if not messagebox.askyesno(T('下载模型'),
                T('下载 %s（%s）到：\n%s\n\n是否开始下载？') %
                (name, size, os.path.join(engine.models_dir(False),
                 'Qwen3-VL-2B-Instruct-GGUF' if '-2B-' in name else 'Qwen3-VL-4B-Instruct-GGUF')), parent=self):
            return
        engine.model_store.set_source(self.source.get())
        self.cancel_download.clear()
        self.downloading = True
        self.download_result = T('正在下载…')
        self.btn_cancel.configure(state='normal')
        for button in self.source_buttons:
            button.configure(state='disabled')

        def progress(filename, done, total):
            try:
                self.progress_queue.get_nowait()
            except queue.Empty:
                pass
            self.progress_queue.put_nowait((filename, done, total))

        def work():
            try:
                engine.model_store.download(name, progress, self.cancel_download.is_set)
                self.download_result = T('模型下载完成，可点击“使用”：%s') % name
            except DownloadCancelled:
                self.download_result = T('下载已取消，保留进度，可重新点击下载继续。')
                raise RuntimeError(T('下载已取消，保留进度，可重新点击下载继续。')) from None
            except Exception as error:
                self.download_result = str(error)
                raise

        self.app.run_model_action(work, T('模型下载完成，可点击“使用”：%s') % name)
        self.refresh()
        self.after(100, self._poll_download)

    def _poll_download(self):
        try:
            filename, done, total = self.progress_queue.get_nowait()
            self.progress.configure(value=100 * done / max(1, total))
            self.progress_text.configure(text='%s · %s / %s' % (filename, human_size(done), human_size(total)))
        except queue.Empty:
            pass
        if self.app._model_busy:
            self.after(100, self._poll_download)
        else:
            self.downloading = False
            self.btn_cancel.configure(state='disabled')
            for button in self.source_buttons:
                button.configure(state='normal')
            self.progress_text.configure(text=self.download_result)
            self.refresh()

    def use(self, name):
        if self.app.choose_model(name):
            self.on_close()

    def open_models_dir(self):
        try:
            open_path(engine.models_dir())
        except Exception as error:
            messagebox.showwarning(T('模型管理'), T('无法打开目录：%r') % error, parent=self)

    def change_models_dir(self):
        if self.app.running or getattr(self.app, '_model_busy', False):
            return
        current = engine.models_dir(False)
        directory = filedialog.askdirectory(title=T('选择 GGUF 模型目录'),
                                            initialdir=current if os.path.isdir(current) else os.path.expanduser('~'),
                                            parent=self)
        if not directory or os.path.abspath(directory) == os.path.abspath(current):
            return

        def switch():
            if not engine.set_models_dir(directory):
                raise RuntimeError(T('模型库切换失败'))

        self.app.run_model_action(switch, T('模型库已切换到 %s') % directory)
        self.refresh()

    def on_close(self):
        if self.downloading:
            self.cancel_download.set()
            self.progress_text.configure(text=T('正在取消下载，请稍候…'))
            return
        self.canvas.unbind_all('<MouseWheel>')
        self.destroy()
