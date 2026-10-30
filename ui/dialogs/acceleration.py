# -*- coding: utf-8 -*-
"""GPU acceleration: an embeddable panel plus a standalone dialog wrapper.

The panel is embedded in the settings window so that the whole app stays two
levels deep (main window → settings): 下载 Vulkan / CUDA 组件就在这一页里完成，
不另开窗口，也就不会有第三层。

Why it was rewritten
--------------------
Both backends used to share **one** progress bar and **one** message label, and
the 120 ms refresh wrote a stale "CUDA 初始化失败" into that label on every tick.
Consequences users hit directly:

* downloading Vulkan wiped its own progress line as soon as the label was
  repainted, so the panel looked frozen;
* downloading CUDA could never show progress at all — the error text won first;
* that same refresh also reset the version dropdown, so a version the user had
  just picked was silently reverted;
* a Vulkan download failure wrote nothing visible anywhere (only the CUDA branch
  of that refresh displayed an error).

Now each backend owns a :class:`ui.download_task.DownloadTask` and a
:class:`ui.download_task.TaskCard`: its own bar, its own words, its own buttons.
Nothing but the poll loop touches a card, and the poll loop only samples — it
never rewrites the device/advice/error text, so the two can no longer fight.
"""
from __future__ import annotations

import os
import re
import time
import tkinter as tk
from tkinter import messagebox, ttk

import engine
from strings import T

from .. import theme
from ..common import APP_TITLE, human_size
from ..controls import fit_to_content, gpu_advice
from ..download_task import (DONE, FAILED, IDLE, PAUSED, RUNNING, DownloadTask,
                             TaskCard, progress_metrics, state_badge)


def pretty_version(identifier: str) -> str:
    """组件的 id 是 llama 版本加哈希，直接显示没人看得懂，收敛成 v1.2.3-rc.3。"""
    tail = identifier.split('-windows-x64-')[-1]
    tail = re.sub(r'v(\d+)-(\d+)-(\d+)', r'v\1.\2.\3', tail)
    tail = re.sub(r'-(rc|beta|alpha)-(\d+)', r'-\1.\2', tail)
    return tail


BACKENDS = (('Vulkan', T('Vulkan（推荐）'),
             T('NVIDIA / AMD / Intel 都能用，显存不够时可借用系统内存')),
            ('CUDA', T('CUDA（实验）'),
             T('仅 NVIDIA；显存不足时反而更慢，没有 tensor core 的老卡不建议')))


class AccelerationPanel(tk.Frame):
    """Vulkan / CUDA component management, without its own window."""

    def __init__(self, master, app, bg=theme.C_CARD):
        super().__init__(master, bg=bg)
        self.app = app
        self._bg = bg
        self.tasks: dict[str, DownloadTask] = {}
        self.cards: dict[str, TaskCard] = {}
        self.version_vars: dict = {}
        self.version_combos: dict = {}
        self.version_rows: dict = {}
        self._version_map: dict = {}
        self._picked: dict = {}          # 用户在下拉框里明确选过的组件 id
        self._reported: set = set()
        self._poll_job = None
        self._advice = ''
        self._advice_ts = 0.0
        self._build()
        self.refresh()

    # ---------------------------------------------------------------- 构建
    def _build(self):
        bg = self._bg
        tk.Label(self, text=T('可选 GPU 加速（实验功能）'), bg=bg, fg=theme.C_TEXT,
                 font=(theme.FONT, 11, 'bold')).pack(anchor='w')
        tk.Label(self, text=T('装了才能用显卡；不装就用 CPU。显卡驱动要自己装好。'),
                 bg=bg, fg=theme.C_SUB, font=(theme.FONT, 9),
                 justify='left', wraplength=520).pack(anchor='w', pady=(2, theme.S1))

        self.lbl_device = tk.Label(self, bg=bg, fg=theme.C_SUB, font=(theme.FONT, 10),
                                   justify='left', anchor='w', wraplength=520)
        self.lbl_device.pack(anchor='w')
        self.lbl_advice = tk.Label(self, bg=bg, fg=theme.C_SUB, font=(theme.FONT, 9),
                                   justify='left', anchor='w', wraplength=520)
        self.lbl_advice.pack(anchor='w', pady=(0, theme.S2))

        for backend, _title, _hint in BACKENDS:
            card = TaskCard(self, lambda action, b=backend: self._act(b, action),
                            bg=bg, wrap=500)
            card.pack(fill='x', pady=(0, theme.S2))
            self.cards[backend] = card
            self.tasks[backend] = DownloadTask(backend)
            self._build_version_row(backend, card)

        self.message = tk.Label(self, bg=bg, fg=theme.C_SUB, font=(theme.FONT, 9),
                                justify='left', anchor='w', wraplength=520)
        self.message.pack(fill='x', pady=(theme.S1, 0))

        bar = tk.Frame(self, bg=bg)
        bar.pack(fill='x', pady=(theme.S2, 0))
        self.cpu_button = ttk.Button(bar, text=T('使用 CPU（不加速）'), command=self.cpu)
        self.cpu_button.pack(side='left')

    def _build_version_row(self, backend, card):
        """版本选择只在真有多个版本时才出现——常见情况只有一个，不占版面。"""
        row = tk.Frame(card.extra, bg=self._bg)
        tk.Label(row, text=T('组件版本'), bg=self._bg, fg=theme.C_SUB,
                 font=(theme.FONT, 9)).pack(side='left', padx=(0, theme.S2))
        variable = tk.StringVar()
        combo = ttk.Combobox(row, textvariable=variable, state='readonly', width=26)
        combo.pack(side='left')
        combo.bind('<<ComboboxSelected>>', lambda _e, b=backend: self._pick_version(b))
        self.version_vars[backend] = variable
        self.version_combos[backend] = combo
        self.version_rows[backend] = row

    # ---------------------------------------------------------------- 状态
    def busy(self) -> bool:
        return bool(self.app.running or getattr(self.app, '_model_busy', False))

    def _task(self, backend) -> DownloadTask:
        task = self.tasks.get(backend)
        if task is None:
            task = DownloadTask(backend)
            self.tasks[backend] = task
        return task

    def _selected(self, backend, status=None):
        status = status or engine.accelerator.status(backend)
        wanted = self._picked.get(backend) or status.get('id')
        return next((item for item in status.get('versions', []) if item['id'] == wanted),
                    None)

    @staticmethod
    def _partial_bytes(status) -> int:
        """已经下到一半的组件包大小；有值就说明能接着下，别让人以为是重头开始。"""
        path = str(status.get('directory') or '') + '.zip.part'
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _pick_version(self, backend):
        label = self.version_vars[backend].get()
        component_id = self._version_map.get(backend, {}).get(label)
        if not component_id:
            return
        self._picked[backend] = component_id
        status = engine.accelerator.status(backend)
        self._task(backend).set_resume(self._partial_bytes(status),
                                       int(status.get('size') or 0))
        self.cards[backend].render(**self._spec(backend, status))

    def _sync_versions(self, backend, status):
        mapping = {}
        for item in status.get('versions', []):
            label = pretty_version(item['id'])
            label += ' · ' + (T('已安装') if item['installed'] else T('可下载'))
            mapping[label] = item['id']
        self._version_map[backend] = mapping

        chosen = self._picked.get(backend) or status.get('id')
        if chosen not in mapping.values():
            chosen = status.get('id')
        variable = self.version_vars[backend]
        current_id = mapping.get(variable.get())
        if current_id != chosen:
            label = next((lb for lb, ident in mapping.items() if ident == chosen), '')
            if label:
                variable.set(label)

        row = self.version_rows[backend]
        running = self._task(backend).running
        if len(mapping) > 1:
            self.version_combos[backend].configure(values=list(mapping))
            row.pack(fill='x', pady=(theme.S1, 0))
            self.version_combos[backend].configure(
                state='disabled' if running else 'readonly')
        else:
            row.pack_forget()

    def _spec(self, backend, status=None) -> dict:
        status = status or engine.accelerator.status(backend)
        task = self._task(backend)
        busy = self.busy()
        selected = self._selected(backend, status)
        installed = bool(selected and selected.get('installed'))
        enabled = bool(status.get('enabled'))
        error = status.get('error') or ''

        hint = next(h for b, _t, h in BACKENDS if b == backend)
        size = int((selected or {}).get('size') or status.get('size') or 0)
        if installed and enabled:
            state = T('已启用 · 识别时会自动加载这块显卡')
        elif installed:
            state = T('已安装，还没启用 · 点右边的按钮启用')
        elif task.resume:
            state = T('没下完 · 已有 %s / %s') % (human_size(task.resume), human_size(size))
        else:
            state = T('未安装 · 需要下载 %s') % human_size(size)
        subtitle = hint + '\n' + state
        if error:
            subtitle += '\n' + T('上次加载失败：%s') % error

        if task.state == IDLE:
            if installed and enabled:
                badge, tone = (T('已启用'), theme.C_OK), DONE
            elif installed:
                badge, tone = (T('已安装'), theme.C_OK), DONE
            elif task.resume:
                badge, tone = (T('可继续'), theme.C_WARN), PAUSED
            else:
                badge, tone = ('', None), IDLE
        else:
            badge, tone = state_badge(task), task.state

        fraction = task.fraction() if task.state in (RUNNING, PAUSED) else None

        if task.state == RUNNING:
            primary = (T('暂停'), 'pause', True)
        elif task.state == PAUSED:
            primary = (T('继续'), 'download', not busy)
        elif task.state == FAILED:
            primary = (T('重试'), 'download', not busy)
        elif installed and enabled:
            primary = (T('已启用'), None, False)
        elif installed:
            primary = (T('启用'), 'enable', not busy)
        elif task.resume:
            primary = (T('继续下载'), 'download', not busy)
        elif status.get('available') or size:
            primary = (T('下载并启用'), 'download', not busy)
        else:
            primary = None

        return dict(title=backend, subtitle=subtitle, badge=badge, tone=tone,
                    fraction=fraction,
                    metrics=progress_metrics(task) if fraction is not None else '',
                    primary=primary)

    def refresh(self):
        try:
            device = engine.runtime_status().get('device') or T('CPU')
        except Exception:
            device = T('CPU')
        self.lbl_device.configure(text=T('当前设备：%s') % device)

        # 探测显卡要起一次 nvidia-smi（最长 8 秒）：缓存 20 秒，
        # 别在下载轮询里反复调，否则界面会一卡一卡的
        now = time.time()
        if not self._advice or now - self._advice_ts > 20:
            try:
                self._advice = gpu_advice()
            except Exception:
                self._advice = ''
            self._advice_ts = now
        self.lbl_advice.configure(text=self._advice)

        for backend, _title, _hint in BACKENDS:
            status = engine.accelerator.status(backend)
            self._sync_versions(backend, status)
            task = self._task(backend)
            if not task.active:
                task.size = int(status.get('size') or 0)
                task.set_resume(self._partial_bytes(status), task.size)
            self.cards[backend].render(**self._spec(backend, status))
        self.cpu_button.configure(state='disabled' if self.busy() else 'normal')

    def _note(self, text, color=theme.C_SUB):
        try:
            self.message.configure(text=text, fg=color)
        except Exception:
            pass

    # ---------------------------------------------------------------- 动作
    def _act(self, backend, action):
        if action == 'pause':
            self._task(backend).pause()
            self._note(T('正在暂停 %s 组件的下载…') % backend, theme.C_WARN)
        elif action == 'download':
            self.download(backend)
        elif action == 'enable':
            self.enable(backend)

    def cpu(self):
        self._note(T('正在切换到 CPU…'), theme.C_SUB)

        def work():
            engine.shutdown_server()
            engine.accelerator.enable_cpu()
            engine.start_server()
            return T('已切换到 CPU')

        if self.app.run_model_action(work, T('已切换到 CPU')):
            self._note(T('正在切换到 CPU…'), theme.C_SUB)

    def enable(self, backend):
        if self.busy():
            return
        self._note(T('正在启用 %s…') % backend, theme.C_ACCENT)

        selected = self._selected(backend)
        component_id = selected['id'] if (selected and selected.get('installed')) else None

        def work():
            engine.shutdown_server()
            engine.accelerator.enable(backend, True, component_id)
            if not engine.start_server():
                raise RuntimeError(engine.runtime_status().get('error')
                                   or T('无法启动本地推理服务'))
            error = engine.accelerator.components[backend].error
            if error:
                raise RuntimeError(error)
            return T('%s 已启用 · 识别时会自动加载显卡。') % backend

        self.app.run_model_action(work, T('%s 已启用。') % backend)

    def download(self, backend):
        if self.busy():
            self._note(T('有别的下载正在进行，等它结束再试。'), theme.C_WARN)
            return
        status = engine.accelerator.status(backend)
        selected = self._selected(backend, status)
        if not selected:
            self._note(T('没有可用的 %s 组件版本。') % backend, theme.C_WARN)
            return

        task = self._task(backend)
        task.size = task.total = int(selected.get('size') or 0)
        task.resume = self._partial_bytes(status)
        component_id = selected['id']

        if not selected.get('installed'):
            if task.resume:
                question = (T('继续下载 %s 加速组件？\n已下载 %s / %s，会从这里接着下。')
                            % (backend, human_size(task.resume), human_size(task.total)))
            else:
                question = (T('下载 %s 加速组件（%s）并启用？')
                            % (backend, human_size(task.total)))
            if backend == 'CUDA':
                question += '\n\n' + T('继续表示你已阅读并同意 NVIDIA CUDA Toolkit EULA。')
                question += '\nhttps://docs.nvidia.com/cuda/eula/'
            if not messagebox.askyesno(APP_TITLE, question, parent=self.winfo_toplevel()):
                return

        task.forget()
        self._note(T('正在下载 %s 组件…') % backend, theme.C_ACCENT)

        def run(progress, should_stop):
            engine.accelerator.install(
                backend, lambda done, total: progress(done, total), should_stop,
                component_id)
            # install() 已校验并落地：这里不能再因为「按过暂停」把它当成中途取消，
            # 否则明明装好了却显示「已暂停」
            component = engine.accelerator.components[backend]
            if component._loaded_id and component._loaded_id != component_id:
                engine.accelerator.enable(backend, True, component_id)
                return T('%s 组件已准备好，重启 SeeName 后生效。') % backend
            engine.shutdown_server()
            engine.accelerator.enable(backend, True, component_id)
            if not engine.start_server():
                raise RuntimeError(engine.runtime_status().get('error')
                                   or T('无法启动本地推理服务'))
            if component.error:
                raise RuntimeError(component.error)
            return T('%s 已启用 · 识别时会自动加载显卡。') % backend

        self._reported.discard(backend)
        if not task.start(self.app, run, T('%s 组件已就绪。') % backend):
            self._note(T('有别的下载正在进行，等它结束再试。'), theme.C_WARN)
            return
        self.refresh()
        self._ensure_poll()

    # ---------------------------------------------------------------- 轮询
    def _ensure_poll(self):
        if self._poll_job is None:
            self._poll_job = self.after(120, self._poll)

    def _poll(self):
        """只采样任务、只重画对应的卡片——不碰设备/建议/错误文字。"""
        self._poll_job = None
        running = False
        just_finished = []
        for backend, task in self.tasks.items():
            if task.state == RUNNING:
                task.sample()
                self.cards[backend].render(**self._spec(backend))
                running = True
            elif task.state in (PAUSED, DONE, FAILED) and backend not in self._reported:
                self._reported.add(backend)
                just_finished.append(backend)
        for backend in just_finished:
            task = self.tasks[backend]
            # 结束的那一刻只有这里知道结果：不写这一句，面板会一直停在
            # 「正在下载…」上，而那其实早就结束了
            color = {DONE: theme.C_OK, PAUSED: theme.C_WARN}.get(task.state, theme.C_ERR)
            self._note(task.message or progress_metrics(task), color)
        if just_finished:
            self.refresh()
            return
        if running:
            self._poll_job = self.after(120, self._poll)

    def stop(self):
        """Called when the host window closes mid-download."""
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        for task in self.tasks.values():
            task.pause()


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
        fit_to_content(self, design_w=620, design_h=460, min_w=560, min_h=420)
        self.protocol('WM_DELETE_WINDOW', self._close)

    def refresh(self):
        self.panel.refresh()

    def stop(self):
        self.panel.stop()

    def _close(self):
        self.panel.stop()
        self.destroy()
