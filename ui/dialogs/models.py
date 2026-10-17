# -*- coding: utf-8 -*-
"""Local GGUF library selection; no inference on the Tk thread."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import engine
from strings import T
from .. import theme
from ..common import APP_TITLE, human_size, open_path


class ModelDialog(tk.Toplevel):
    """Choose a directory containing supported main-model/projector pairs."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.rows = {}
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
        tk.Label(foot, text=T('当前使用 CPU。本轮仅支持本地 Qwen3-VL-4B-Instruct 模型，不提供在线下载。'),
                 bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 9), wraplength=740,
                 justify='left').pack(anchor='w', pady=(0, 8))
        bar = tk.Frame(foot, bg=theme.C_BG)
        bar.pack(fill='x')
        ttk.Button(bar, text=T('刷新'), command=self.refresh).pack(side='left')
        ttk.Button(bar, text=T('打开模型库'), command=self.open_models_dir).pack(side='left', padx=8)
        self.btn_directory = ttk.Button(bar, text=T('选择模型目录…'), command=self.change_models_dir)
        self.btn_directory.pack(side='left')
        ttk.Button(bar, text=T('关闭'), command=self.on_close).pack(side='right')

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
            button = ttk.Button(row, text=T('使用') if ok else T('缺少文件'), width=10,
                                state='normal' if ok and not busy else 'disabled',
                                command=lambda n=name: self.use(n))
            button.pack(side='right', padx=8)
            self.rows[name] = {'btn': button, 'installed': ok}

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
        self.canvas.unbind_all('<MouseWheel>')
        self.destroy()
