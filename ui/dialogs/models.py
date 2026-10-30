# -*- coding: utf-8 -*-
"""Model manager: library selection, download with an explicit state machine.

The old dialog mixed three states into one progress line — "downloading",
"cancelled" and "failed" all ended with the same red error dialog, so a user who
pressed 取消 could not tell whether the download had stopped, broken, or was
still running. Now the dialog owns an explicit state:

    idle → downloading → (paused | done | failed)

Every state has one deterministic label, one colour and one button caption, and
"paused" is no longer reported as an error: the partial file is kept and the
next click resumes it.
"""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import engine
from strings import T
from .. import theme
from ..common import APP_TITLE, human_size, open_path
from ..controls import ChoiceRow, ScrollArea, fit_to_content
from inference.downloads import DOWNLOADS, DownloadCancelled

SOURCE_OPTIONS = ((T('国外：Hugging Face'), 'huggingface'),
                  (T('国内：ModelScope'), 'modelscope'))


class ModelDialog(tk.Toplevel):
    """Choose / download a supported main-model + projector pair."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.rows: dict = {}
        self.cancel_download = threading.Event()
        self.downloading = False
        self.progress_queue = queue.Queue(maxsize=1)
        self.task = ''                  # 正在下载的模型名
        self.state = 'idle'             # idle / downloading / paused / done / failed
        self._status = ''
        self._status_kind = 'idle'
        self._message = ''
        self._speed_t = 0.0
        self._speed_b = 0
        self._speed = 0.0
        self._poll_job = None
        self.title(T('模型管理 · %s') % APP_TITLE)
        self.configure(bg=theme.C_BG)
        self.transient(master)
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self._build()
        fit_to_content(self, min_w=760, min_h=520)
        self.refresh()

    # ---------------------------------------------------------------- 构建
    def _build(self):
        head = tk.Frame(self, bg=theme.C_BG)
        head.pack(fill='x', padx=theme.S4, pady=(theme.S3, theme.S2))
        tk.Label(head, text=T('模型'), bg=theme.C_BG, fg=theme.C_TEXT,
                 font=(theme.FONT, 15, 'bold')).pack(anchor='w')
        tk.Label(head, text=T('主模型和配套 mmproj 要放在同一目录。没有模型时先在这里下载一个。'),
                 bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 9),
                 justify='left', wraplength=700).pack(anchor='w', pady=(2, 0))
        self.lbl_stat = tk.Label(head, bg=theme.C_BG, fg=theme.C_SUB,
                                 font=(theme.FONT, 9), justify='left', wraplength=700)
        self.lbl_stat.pack(anchor='w', pady=(4, 0))

        # —— 任务区：状态文字 + 进度，永远在同一个位置
        task = tk.Frame(self, bg=theme.C_CARD, highlightbackground=theme.C_LINE,
                        highlightthickness=1)
        task.pack(fill='x', padx=theme.S4)
        self.lbl_task = tk.Label(task, bg=theme.C_CARD, fg=theme.C_SUB,
                                 font=(theme.FONT, 10), anchor='w', justify='left',
                                 wraplength=700)
        self.lbl_task.pack(fill='x', padx=theme.S3, pady=(theme.S2, theme.S1))
        self.progress = ttk.Progressbar(task, maximum=100)
        self.progress.pack(fill='x', padx=theme.S3, pady=(0, theme.S3))

        wrap = tk.Frame(self, bg=theme.C_BG)
        wrap.pack(fill='both', expand=True, padx=theme.S4, pady=theme.S2)
        self._scroll = ScrollArea(wrap, bg=theme.C_CARD, width=700)
        self._scroll.pack(fill='both', expand=True)
        self.inner = self._scroll.inner

        foot = tk.Frame(self, bg=theme.C_BG)
        foot.pack(fill='x', padx=theme.S4, pady=(0, theme.S3))
        self.source = tk.StringVar(value=engine.model_store.source())
        self.choice_source = ChoiceRow(foot, T('下载来源'), SOURCE_OPTIONS, self.source)
        self.choice_source.pack(side='left')

        ttk.Button(foot, text=T('关闭'), command=self.on_close).pack(side='right')
        self.btn_directory = ttk.Button(foot, text=T('选择模型目录…'),
                                        command=self.change_models_dir)
        self.btn_directory.pack(side='right', padx=theme.S2)
        ttk.Button(foot, text=T('打开模型库'), command=self.open_models_dir).pack(side='right')
        ttk.Button(foot, text=T('刷新'), command=self.refresh).pack(side='right', padx=theme.S2)
        tk.Label(foot, text=T('GPU 加速在「设置」里'), bg=theme.C_BG,
                 fg=theme.C_HINT, font=(theme.FONT, 8)).pack(side='right',
                                                             padx=(0, theme.S2))

    # ---------------------------------------------------------------- 刷新
    def refresh(self):
        installed = engine.installed_models()
        problems = engine.model_problems()
        self.lbl_stat.configure(text=T('可用模型 %d 个 · 模型库 %s')
                                % (len(installed), engine.models_dir(False)))
        for widget in self.inner.winfo_children():
            widget.destroy()
        self.rows.clear()
        busy = bool(self.app.running or getattr(self.app, '_model_busy', False))
        self.btn_directory.configure(state='disabled' if busy else 'normal')
        self.choice_source.set_enabled(not busy)
        for item in engine.MODEL_CATALOG:
            name = item['name']
            ok = name in installed
            downloadable = name in DOWNLOADS
            row = tk.Frame(self.inner, bg=theme.C_CARD)
            row.pack(fill='x', padx=theme.S3, pady=theme.S2)
            left = tk.Frame(row, bg=theme.C_CARD)
            left.pack(side='left', fill='x', expand=True)
            mark, color = ('✓', theme.C_OK) if ok else ('•', theme.C_HINT)
            tk.Label(left, text='%s  %s' % (mark, name), bg=theme.C_CARD,
                     fg=theme.C_TEXT, font=(theme.FONT, 11, 'bold')).pack(anchor='w')
            if ok:
                detail = T('已安装 · 文件大小 %s') % human_size(installed[name])
            elif downloadable:
                detail = T('可下载 · %s') % human_size(sum(f[1] for f in DOWNLOADS[name]))
            else:
                detail = problems.get(name, T('模型文件不完整'))
            tk.Label(left, text=detail, bg=theme.C_CARD, fg=theme.C_SUB,
                     font=(theme.FONT, 9), wraplength=520, justify='left').pack(
                anchor='w', pady=(2, 0))

            caption, action, enabled = self._row_action(name, ok, downloadable, busy)
            button = ttk.Button(row, width=10, text=caption,
                                state='normal' if enabled else 'disabled',
                                command=lambda n=name, a=action: self._act(n, a))
            button.pack(side='right', padx=(theme.S2, 0))
            self.rows[name] = {'btn': button, 'installed': ok}

    def _row_action(self, name, ok, downloadable, busy):
        """(按钮文字, 动作, 是否可点) —— 只由一个状态决定，不会自相矛盾。"""
        if ok:
            return (T('使用'), 'use', not busy)
        if self.task == name and self.state == 'downloading':
            return (T('暂停'), 'pause', True)
        if self.task == name and self.state == 'paused':
            return (T('继续'), 'download', not busy)
        if downloadable:
            return (T('下载'), 'download', not busy)
        return (T('缺少文件'), None, False)

    def _act(self, name, action):
        if action == 'use':
            self.use(name)
        elif action == 'pause':
            self.cancel_download.set()
            self._set_status(T('正在暂停…'), 'downloading')
        elif action == 'download':
            self.start(name)

    # ---------------------------------------------------------------- 状态栏
    def _set_status(self, text, kind='idle'):
        self._status, self._status_kind = text, kind
        color = {'idle': theme.C_SUB, 'downloading': theme.C_ACCENT,
                 'paused': theme.C_WARN, 'done': theme.C_OK,
                 'failed': theme.C_ERR}.get(kind, theme.C_SUB)
        try:
            self.lbl_task.configure(text=text, fg=color)
        except Exception:
            pass

    # ---------------------------------------------------------------- 下载
    def start(self, name):
        if self.app.running or self.app._model_busy:
            return
        size = human_size(sum(f[1] for f in DOWNLOADS[name]))
        folder = os.path.join(engine.models_dir(False),
                              'Qwen3-VL-2B-Instruct-GGUF' if '-2B-' in name
                              else 'Qwen3-VL-4B-Instruct-GGUF')
        if not messagebox.askyesno(T('下载模型'),
                                   T('下载 %s（%s）到：\n%s\n\n开始后可以随时暂停，再点「继续」会接着下。')
                                   % (name, size, folder), parent=self):
            return
        engine.model_store.set_source(self.source.get())
        self.cancel_download.clear()
        self.downloading = True
        self.task = name
        self.state = 'downloading'
        self._speed_t, self._speed_b, self._speed = 0.0, 0, 0.0
        self.progress.configure(value=0)
        self._set_status(T('正在下载 %s…') % name, 'downloading')
        self.refresh()

        def progress(filename, done, total):
            try:
                self.progress_queue.get_nowait()
            except queue.Empty:
                pass
            self.progress_queue.put_nowait((filename, done, total))

        def work():
            try:
                engine.model_store.download(name, progress, self.cancel_download.is_set)
                self.state = 'done'
                self._message = T('下载完成，点这一行的「使用」即可切换到它。')
                return self._message
            except DownloadCancelled:
                # 用户主动暂停：不是错误，.part 保留，下次继续
                self.state = 'paused'
                self._message = T('已暂停，进度已保留；再点「继续」会接着下。')
                return self._message
            except Exception as error:
                self.state = 'failed'
                self._message = T('下载失败：%s') % error
                return self._message

        self.app.run_model_action(work, T('模型下载结束：%s') % name)
        self._poll()

    def _poll(self):
        self._poll_job = None
        try:
            filename, done, total = self.progress_queue.get_nowait()
            self.progress.configure(value=100 * done / max(1, total))
            now = time.time()
            if self._speed_t and now - self._speed_t > 0.4:
                inst = (done - self._speed_b) / (now - self._speed_t)
                self._speed = inst if self._speed <= 0 else self._speed * 0.6 + inst * 0.4
                self._speed_t, self._speed_b = now, done
            elif not self._speed_t:
                self._speed_t, self._speed_b = now, done
            eta = ''
            if self._speed > 0 and total > done:
                eta = ' · ' + T('剩余约 %d 秒') % int((total - done) / self._speed)
            speed = (' · %.1f MB/s' % (self._speed / 1048576.0)) if self._speed > 0 else ''
            self._set_status(
                T('正在下载 %s · %s / %s（%d%%）%s%s')
                % (os.path.basename(filename), human_size(done), human_size(total),
                   int(100 * done / max(1, total)), speed, eta), 'downloading')
        except queue.Empty:
            pass
        if self.app._model_busy:
            self._poll_job = self.after(150, self._poll)
            return
        self.downloading = False
        self.progress.configure(value=0)
        kind = {'done': 'done', 'paused': 'paused', 'failed': 'failed'}.get(self.state, 'idle')
        self._set_status(self._last_log(), kind)
        self.refresh()

    def _last_log(self):
        if self._message:
            return self._message
        return {
            'done': T('下载完成，点这一行的「使用」即可切换到它。'),
            'paused': T('已暂停，进度已保留；再点「继续」会接着下。'),
            'failed': T('下载失败：可换一个下载源再试。'),
        }.get(self.state, self._status)

    # ---------------------------------------------------------------- 其它
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
        directory = filedialog.askdirectory(
            title=T('选择 GGUF 模型目录'),
            initialdir=current if os.path.isdir(current) else os.path.expanduser('~'),
            parent=self)
        if not directory or os.path.abspath(directory) == os.path.abspath(current):
            return

        def switch():
            if not engine.set_models_dir(directory):
                raise RuntimeError(T('模型库切换失败'))
            return T('模型库已切换到 %s') % directory

        self.app.run_model_action(switch, T('模型库已切换'))
        self.refresh()

    def on_close(self):
        if self.downloading:
            self.cancel_download.set()
            self._set_status(T('正在停止下载…'), 'paused')
            self._poll_job = self.after(300, self.on_close)
            return
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        try:
            self._scroll.canvas.unbind_all('<MouseWheel>')
        except Exception:
            pass
        self.destroy()
