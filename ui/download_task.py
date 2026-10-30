# -*- coding: utf-8 -*-
"""One download state machine + one card renderer, shared by both windows.

Why this module exists
----------------------
The model list and the GPU backend list each grew their own progress handling,
and they disagreed with each other in ways users noticed immediately:

* the backend page had **one** progress bar and **one** message label shared by
  Vulkan and CUDA, and its 120 ms refresh wrote a stale "CUDA 初始化失败" into
  that label on every tick — so the Vulkan download line was wiped continuously
  and the CUDA download could never show its own progress at all;
* neither page said anything *before* the click, so a half-downloaded 1.8 GB
  model looked exactly like a fresh one: "下载 → 停止 → 再启动" all rendered the
  same words;
* "取消" was reported as a red failure even though the partial file was kept;
* the same 120 ms refresh also reset the version dropdown, so a version picked
  by the user was silently reverted before the button could act on it.

Here one transfer is one object with one state, and one card draws it:

    idle → downloading → (paused | done | failed) → downloading (resume)

Both windows then use identical wording ("下载 / 暂停 / 继续 / 重试 / 使用"), so
the button under an item always says what the next click will do.
"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from inference.downloads import DownloadCancelled
from strings import T

from . import theme
from .common import fmt_dur, human_size

IDLE = 'idle'
RUNNING = 'downloading'
PAUSED = 'paused'
DONE = 'done'
FAILED = 'failed'

TONE = {IDLE: theme.C_HINT, RUNNING: theme.C_ACCENT, PAUSED: theme.C_WARN,
        DONE: theme.C_OK, FAILED: theme.C_ERR}


class DownloadTask:
    """A single transfer: its own cancel flag, progress samples and outcome.

    ``start()`` runs ``run(progress, should_stop)`` on the worker thread that
    :meth:`ui.app.App.run_model_action` provides, so the global "one background
    model action at a time" rule still holds and the main window keeps showing
    「模型操作中…」.
    """

    def __init__(self, key, size=0):
        self.key = key
        self.size = int(size or 0)
        self.total = self.size
        self.done = 0
        self.resume = 0          # 开始前磁盘上已有的字节数
        self.stage = ''          # 'verify' = 正在算 SHA-256
        self.state = IDLE
        self.message = ''
        self.speed = 0.0
        self.eta = None
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self._samples = queue.Queue(maxsize=8)
        self._t0 = 0.0
        self._b0 = 0

    # ---------------------------------------------------------------- 查询
    @property
    def running(self) -> bool:
        return self.state == RUNNING

    @property
    def active(self) -> bool:
        return self.state in (RUNNING, PAUSED)

    def fraction(self) -> float:
        if not self.total:
            return 0.0
        return min(1.0, max(0.0, self.done / float(self.total)))

    def set_resume(self, done, total=0):
        """界面探测到的「磁盘上已有多少」，只在没在跑的时候生效。"""
        if self.state in (IDLE, FAILED):
            self.resume = max(0, int(done or 0))
            if total:
                self.total = int(total)
            self.done = self.resume

    # ---------------------------------------------------------------- 生命周期
    def start(self, app, run, default_message, done_message='') -> bool:
        if self.state == RUNNING:
            return False
        previous = self.state
        self.cancel.clear()
        self.finished.clear()
        self.message = ''
        self.stage = ''
        self.speed = 0.0
        self.eta = None
        # 断点取「界面认得的已下字节」和「磁盘上真实存在」里更大的那个：
        # 暂停瞬间可能还有几个进度样本没被读走，不能用更小的那个把进度倒回去
        self.done = max(self.done, self.resume)
        self._t0 = time.time()
        self._b0 = self.done
        self.state = RUNNING

        def work():
            try:
                text = run(self.progress, self.cancel.is_set)
                self.state = DONE
                self.stage = ''
                self.message = text or done_message
                if self.total:
                    self.done = self.total
            except DownloadCancelled:
                # 用户主动暂停不是错误：.part 留着，下次接着下。
                # 顺便把断点记下来——界面重新读磁盘是另一条路，两条都要对。
                self.state = PAUSED
                self.stage = ''
                self.resume = self.done
                self.message = T('已暂停，进度已保留；点「继续」接着下。')
            except Exception as error:
                self.state = FAILED
                self.stage = ''
                self.resume = self.done
                self.message = str(error) or error.__class__.__name__
            finally:
                self.speed = 0.0
                self.eta = None
                self.finished.set()

        if not app.run_model_action(work, default_message):
            # 另一个后台动作正在跑：状态回退，界面照旧
            if not self.finished.is_set():
                self.state = previous
            return False
        return True

    def pause(self):
        if self.state == RUNNING:
            self.cancel.set()

    def forget(self):
        """清掉上一轮的结论，重新点等于重试（.part 会接着用）。"""
        if self.state in (DONE, FAILED):
            self.state = IDLE
            self.message = ''

    # ---------------------------------------------------------------- 进度
    def progress(self, done, total=0, stage=''):
        """由后台线程调用；只留最新一个样本，界面读不走也不会积压。"""
        try:
            self._samples.get_nowait()
        except queue.Empty:
            pass
        try:
            self._samples.put_nowait((int(done or 0), int(total or 0), stage or ''))
        except queue.Full:
            pass

    def sample(self) -> bool:
        """UI 线程调用：消费样本，算出速度与剩余时间。"""
        got = False
        while True:
            try:
                done, total, stage = self._samples.get_nowait()
            except queue.Empty:
                break
            got = True
            if total:
                self.total = total
                self.done = min(done, total)
            else:
                self.done = done
            if stage:
                self.stage = stage
        if not got:
            return False
        span = time.time() - self._t0
        if span > 0.4:
            rate = (self.done - self._b0) / span
            if rate > 0:
                self.speed = rate if self.speed <= 0 else self.speed * 0.5 + rate * 0.5
        if self.speed > 2048 and self.total > self.done:
            self.eta = int((self.total - self.done) / self.speed)
        else:
            self.eta = None
        return True


# -------------------------------------------------------------------- 文案
def progress_metrics(task: DownloadTask) -> str:
    """进度栏右侧那句「已下载 1.1 GB / 1.8 GB · 6.2 MB/s · 剩余 2 分」。"""
    if task.stage == 'verify':
        return T('正在校验文件完整性，这一步进度不动是正常的')
    if task.state == RUNNING:
        parts = [T('已下载 %s / %s') % (human_size(task.done), human_size(task.total))]
        if task.speed > 0:
            parts.append('%.1f MB/s' % (task.speed / 1048576.0))
        if task.eta:
            parts.append(T('剩余 %s') % fmt_dur(task.eta))
        return ' · '.join(parts)
    if task.state == PAUSED:
        return T('已下载 %s / %s · 进度已保留') % (human_size(task.done),
                                                   human_size(task.total))
    if task.state == DONE:
        return task.message or T('已完成')
    if task.state == FAILED:
        return T('失败：%s') % (task.message or T('未知原因'))
    if task.resume:
        return T('已下载 %s / %s，点「继续」接着下') % (human_size(task.resume),
                                                       human_size(task.total))
    return ''


def state_badge(task: DownloadTask, idle_text=''):
    if task.state == RUNNING:
        if task.stage == 'verify':
            return (T('校验中'), theme.C_ACCENT)
        return (T('下载中 %d%%') % int(100 * task.fraction()), theme.C_ACCENT)
    if task.state == PAUSED:
        return (T('已暂停'), theme.C_WARN)
    if task.state == DONE:
        return (T('已完成'), theme.C_OK)
    if task.state == FAILED:
        return (T('失败'), theme.C_ERR)
    return (idle_text, theme.C_HINT)


# -------------------------------------------------------------------- 卡片
class TaskCard(tk.Frame):
    """一项任务的固定版式：标题 + 状态 / 说明 / 进度 / 指标 / 按钮。

    版式固定、位置固定，所以「开始 / 暂停 / 继续 / 重试」永远在同一个角落，
    两个窗口里长得一模一样，不用重新找按钮。
    """

    def __init__(self, master, on_action, bg=theme.C_CARD, wrap=520):
        super().__init__(master, bg=bg, highlightbackground=theme.C_LINE,
                         highlightthickness=1)
        self._bg = bg
        self._on_action = on_action
        self._frac = 0.0
        self._color = theme.C_ACCENT
        self._actions = {}

        head = tk.Frame(self, bg=bg)
        head.pack(fill='x', padx=theme.S3, pady=(theme.S2, 0))
        self.title = tk.Label(head, bg=bg, fg=theme.C_TEXT, anchor='w',
                              font=(theme.FONT, 11, 'bold'))
        self.title.pack(side='left')
        self.badge = tk.Label(head, bg=bg, fg=theme.C_HINT, font=(theme.FONT, 9, 'bold'))
        self.badge.pack(side='right')

        self.sub = tk.Label(self, bg=bg, fg=theme.C_SUB, font=(theme.FONT, 9),
                            anchor='w', justify='left', wraplength=wrap)
        self.sub.pack(fill='x', padx=theme.S3, pady=(1, 0))

        # 调用方可以往里塞额外控件（例如版本选择）；没有子控件时高度为 0
        self.extra = tk.Frame(self, bg=bg)
        self.extra.pack(fill='x', padx=theme.S3)

        # 进度条用 Canvas 自己画：要按状态换色（下载蓝 / 暂停黄 / 完成绿 /
        # 失败红），而 ttk 进度条的颜色是全局 style，改一处会牵连整个窗口
        self.bar = tk.Canvas(self, height=10, bg=bg, highlightthickness=0)
        self.bar.bind('<Configure>', lambda e: self._paint())
        self.bar_text = tk.Label(self, bg=bg, fg=theme.C_SUB, font=(theme.FONT, 9),
                                 anchor='w', justify='left', wraplength=wrap)

        foot = tk.Frame(self, bg=bg)
        foot.pack(fill='x', padx=theme.S3, pady=(theme.S1, theme.S2))
        self.btns = tk.Frame(foot, bg=bg)
        self.btns.pack(side='right')
        self.metrics = tk.Label(foot, bg=bg, fg=theme.C_SUB, font=(theme.FONT, 9),
                                anchor='w', justify='left', wraplength=wrap)
        self.metrics.pack(side='left', fill='x', expand=True)

        self.btn_primary = ttk.Button(self.btns, text=T('下载'), width=11,
                                      command=lambda: self._fire('primary'))
        self.btn_secondary = ttk.Button(self.btns, text=T('查看'),
                                        command=lambda: self._fire('secondary'))

    # ---------------------------------------------------------------- 渲染
    def render(self, title, subtitle='', badge=('', None), tone=IDLE,
               fraction=None, bar_text='', metrics='',
               primary=None, secondary=None):
        self.title.configure(text=title)
        text, color = badge
        self.badge.configure(text=text, fg=color or TONE.get(tone, theme.C_HINT))
        self.sub.configure(text=subtitle or '')
        if fraction is None:
            self.bar.pack_forget()
            self.bar_text.pack_forget()
        else:
            self._frac = max(0.0, min(1.0, float(fraction)))
            self._color = TONE.get(tone, theme.C_ACCENT)
            self.bar.pack(fill='x', padx=theme.S3, pady=(theme.S1, 0))
            if bar_text:
                self.bar_text.configure(text=bar_text)
                self.bar_text.pack(fill='x', padx=theme.S3)
            else:
                self.bar_text.pack_forget()
            self._paint()
        self.metrics.configure(text=metrics or '')
        self._button('primary', self.btn_primary, primary)
        self._button('secondary', self.btn_secondary, secondary)

    def _button(self, role, widget, spec):
        if not spec:
            widget.pack_forget()
            self._actions.pop(role, None)
            return
        spec = tuple(spec) + (True,)
        text, action, enabled = spec[0], spec[1], spec[2]
        self._actions[role] = action
        widget.configure(text=text, state='normal' if enabled else 'disabled')
        widget.pack(side='right', padx=(theme.S2, 0))

    def _fire(self, role):
        action = self._actions.get(role)
        if action:
            self._on_action(action)

    def _paint(self):
        try:
            self.bar.delete('all')
            width = max(self.bar.winfo_width(), 60)
            self.bar.create_rectangle(0, 2, width, 10, fill=theme.C_LINE, outline='')
            if self._frac > 0:
                self.bar.create_rectangle(0, 2, max(4, int(width * self._frac)), 10,
                                          fill=self._color, outline='')
        except Exception:
            pass
