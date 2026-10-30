# -*- coding: utf-8 -*-
"""Model manager: one card per model, one explicit state per download.

The old dialog mixed three states into a single progress line — "downloading",
"cancelled" and "failed" all ended with the same red error dialog — so a user who
pressed 取消 could not tell whether the download had stopped, broken, or was
still running. And nothing on the row said a partially downloaded model could
be resumed: 「下载 → 停止 → 再启动」 看起来完全一样。

Now each model is a :class:`ui.download_task.TaskCard`, whose button always says
what the next click does (下载 / 暂停 / 继续下载 / 重试 / 使用), and the row shows
how many bytes are already on disk even before you touch it.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import engine
from inference.downloads import DOWNLOADS, model_folder
from strings import T

from .. import theme
from ..common import APP_TITLE, human_size, open_path
from ..controls import ChoiceRow, ScrollArea, fit_to_content
from ..download_task import (DONE, FAILED, IDLE, PAUSED, RUNNING, DownloadTask,
                             TaskCard, progress_metrics, state_badge)

SOURCE_OPTIONS = ((T('国外：Hugging Face'), 'huggingface'),
                  (T('国内：ModelScope'), 'modelscope'))


class ModelDialog(tk.Toplevel):
    """Choose / download a supported main-model + projector pair."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.tasks: dict[str, DownloadTask] = {}
        self.cards: dict[str, TaskCard] = {}
        self._installed: dict = {}
        self._problems: dict = {}
        self._reported: set = set()     # 已经把结果显示出来的任务
        self._poll_job = None
        self._closing = False

        self.title(T('模型管理 · %s') % APP_TITLE)
        self.configure(bg=theme.C_BG)
        self.transient(master)
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self._build()
        fit_to_content(self, design_w=800, design_h=620, min_w=700, min_h=520)
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

        # 顶部只留一行汇总：即使把卡片滚出视野，也知道当前在干什么。
        # 进度条不再放这里——每张卡片自己有一条，两条会互相矛盾。
        self.lbl_task = tk.Label(self, bg=theme.C_BG, fg=theme.C_SUB,
                                 font=(theme.FONT, 10), anchor='w', justify='left',
                                 wraplength=720)
        self.lbl_task.pack(fill='x', padx=theme.S4, pady=(0, theme.S1))

        wrap = tk.Frame(self, bg=theme.C_BG)
        wrap.pack(fill='both', expand=True, padx=theme.S4, pady=theme.S2)
        self._scroll = ScrollArea(wrap, bg=theme.C_BG, width=700)
        self._scroll.pack(fill='both', expand=True)
        self._scroll.bind_wheel()
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

    # ---------------------------------------------------------------- 任务
    def _task(self, name) -> DownloadTask:
        task = self.tasks.get(name)
        if task is None:
            task = DownloadTask(name, sum(f[1] for f in DOWNLOADS.get(name, ())))
            self.tasks[name] = task
        return task

    @staticmethod
    def _partial_bytes(name) -> int:
        """磁盘上已经下到的字节数——「停止后再启动」能不能接着下，就看这个。"""
        files = DOWNLOADS.get(name, ())
        if not files:
            return 0
        try:
            folder = os.path.join(engine.models_dir(False), model_folder(name))
        except Exception:
            return 0
        done = 0
        for filename, size, _sha in files:
            try:
                done += min(os.path.getsize(os.path.join(folder, filename + '.part')), size)
            except OSError:
                pass
        return done

    def _spec(self, name) -> dict:
        task = self._task(name)
        busy = bool(self.app.running or getattr(self.app, '_model_busy', False))
        installed = name in self._installed
        downloadable = name in DOWNLOADS
        size = sum(f[1] for f in DOWNLOADS.get(name, ()))

        have = task.done if task.state == PAUSED else task.resume
        if installed:
            subtitle = T('已安装 · 占用 %s') % human_size(self._installed[name])
        elif task.state == FAILED and task.message:
            # 失败原因就写在行里，不能只丢进主窗口日志
            subtitle = task.message
        elif downloadable and have and task.state != RUNNING:
            subtitle = T('没下完 · 已有 %s / %s（%d%%）') % (
                human_size(have), human_size(size), 100 * have // max(1, size))
        elif downloadable:
            subtitle = T('可下载 · %s') % human_size(size)
        else:
            subtitle = self._problems.get(name, T('模型文件不完整'))

        if task.state in (RUNNING, PAUSED, DONE, FAILED):
            badge, tone = state_badge(task), task.state
        elif installed:
            badge, tone = (T('已安装'), theme.C_OK), DONE
        elif task.resume:
            badge, tone = (T('可继续'), theme.C_WARN), PAUSED
        else:
            badge, tone = ('', None), IDLE

        fraction = task.fraction() if task.state in (RUNNING, PAUSED) else None

        if installed:
            primary = (T('使用'), 'use', not busy)
        elif task.state == RUNNING:
            primary = (T('暂停'), 'pause', True)
        elif task.state == PAUSED:
            primary = (T('继续'), 'download', not busy)
        elif task.state == FAILED:
            primary = (T('重试'), 'download', not busy)
        elif task.resume:
            primary = (T('继续下载'), 'download', not busy)
        elif downloadable:
            primary = (T('下载'), 'download', not busy)
        else:
            primary = None

        return dict(title=name, subtitle=subtitle, badge=badge, tone=tone,
                    fraction=fraction,
                    metrics=progress_metrics(task) if fraction is not None else '',
                    primary=primary)

    # ---------------------------------------------------------------- 刷新
    def refresh(self):
        self._installed = engine.installed_models()
        self._problems = engine.model_problems()
        self.lbl_stat.configure(text=T('可用模型 %d 个 · 模型库 %s')
                                % (len(self._installed), engine.models_dir(False)))
        for widget in self.inner.winfo_children():
            widget.destroy()
        self.cards.clear()
        busy = bool(self.app.running or getattr(self.app, '_model_busy', False))
        self.choice_source.set_enabled(not busy)
        self.btn_directory.configure(state='disabled' if busy else 'normal')

        for item in engine.MODEL_CATALOG:
            name = item['name']
            task = self._task(name)
            if task.state in (IDLE, FAILED):
                task.set_resume(self._partial_bytes(name),
                                sum(f[1] for f in DOWNLOADS.get(name, ())))
            card = TaskCard(self.inner, lambda action, n=name: self._act(n, action),
                            wrap=620)
            card.pack(fill='x', padx=theme.S3, pady=(0, theme.S2))
            self.cards[name] = card
            card.render(**self._spec(name))
        self._say()

    def _render(self, name):
        card = self.cards.get(name)
        if card is not None:
            card.render(**self._spec(name))

    def _say(self):
        """顶部那行汇总：永远是「现在在干什么」或「上一件事的结果」。"""
        active = next((t for t in self.tasks.values() if t.active), None)
        if active is not None:
            self.lbl_task.configure(
                text='%s · %s' % (active.key, progress_metrics(active)),
                fg=theme.C_ACCENT if active.running else theme.C_WARN)
            return
        last = next((t for t in self.tasks.values() if t.state in (DONE, FAILED)), None)
        if last is not None:
            self.lbl_task.configure(text=last.message or progress_metrics(last),
                                    fg=theme.C_OK if last.state == DONE else theme.C_ERR)
            return
        self.lbl_task.configure(text='', fg=theme.C_SUB)

    # ---------------------------------------------------------------- 动作
    def _act(self, name, action):
        if action == 'use':
            self.use(name)
        elif action == 'pause':
            self._task(name).pause()
            self.lbl_task.configure(text=T('正在暂停 %s…') % name, fg=theme.C_WARN)
        elif action == 'download':
            self.start(name)

    def start(self, name):
        if name not in DOWNLOADS:
            return
        if self.app.running or getattr(self.app, '_model_busy', False):
            self.lbl_task.configure(text=T('有别的下载正在进行，等它结束再试。'),
                                    fg=theme.C_WARN)
            return
        task = self._task(name)
        size = sum(f[1] for f in DOWNLOADS[name])
        folder = os.path.join(engine.models_dir(False), model_folder(name))
        task.resume = self._partial_bytes(name)
        if task.resume:
            question = T('继续下载 %s？\n已下载 %s / %s，会从这里接着下。\n\n保存到：\n%s') % (
                name, human_size(task.resume), human_size(size), folder)
        else:
            question = T('下载 %s（%s）到：\n%s\n\n下载中可以随时暂停，之后点「继续」会接着下。') % (
                name, human_size(size), folder)
        if not messagebox.askyesno(T('下载模型'), question, parent=self):
            return

        engine.model_store.set_source(self.source.get())
        task.forget()
        task.total = size

        def run(progress, should_stop):
            def on_progress(label, done, total):
                # 「SHA-256: xxx」是校验阶段，进度不动的那几秒要说清楚在干什么
                stage = 'verify' if str(label).startswith('SHA-256') else ''
                progress(done, total, stage)

            engine.model_store.download(name, on_progress, should_stop)
            return T('下载完成，点这一行的「使用」即可切换到它。')

        self._reported.discard(name)
        if not task.start(self.app, run, T('模型下载结束：%s') % name):
            self.lbl_task.configure(text=T('有别的下载正在进行，等它结束再试。'),
                                    fg=theme.C_WARN)
            return
        self.refresh()
        self._ensure_poll()

    # ---------------------------------------------------------------- 轮询
    def _ensure_poll(self):
        if self._poll_job is None:
            self._poll_job = self.after(120, self._poll)

    def _poll(self):
        """只管「正在跑」的任务；不重建整表，所以不会打断用户操作。"""
        self._poll_job = None
        running = False
        finished = False
        for name, task in self.tasks.items():
            if task.state == RUNNING:
                task.sample()
                self._render(name)
                running = True
            elif task.state in (PAUSED, DONE, FAILED) and name not in self._reported:
                self._reported.add(name)
                finished = True
        self._say()

        if self._closing:
            if running:
                self._poll_job = self.after(120, self._poll)
            else:
                self._shutdown()
            return
        if finished:
            # 装完了要重新读一遍磁盘（安装状态、按钮文字都变了）
            self.refresh()
            return
        if running:
            self._poll_job = self.after(120, self._poll)

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

    # ---------------------------------------------------------------- 关闭
    def stop(self):
        """外部（主窗口关闭 / 设置页切走）调用：只停下载，不销毁窗口。"""
        for task in self.tasks.values():
            task.pause()

    def on_close(self):
        if any(t.running for t in self.tasks.values()):
            # 只标记关闭意图，让唯一的轮询循环负责收尾：
            # 以前这里另开一条 after 链，两条链互相覆盖句柄，关掉窗口后还在回调
            self._closing = True
            for task in self.tasks.values():
                task.pause()
            self.lbl_task.configure(text=T('正在停止下载…'), fg=theme.C_WARN)
            self._ensure_poll()
            return
        self._shutdown()

    def _shutdown(self):
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        self.stop()
        try:
            self._scroll.canvas.unbind_all('<MouseWheel>')
        except Exception:
            pass
        self.destroy()