# -*- coding: utf-8 -*-
"""One settings window for everything — the app stays two levels deep.

Before this module the settings lived at the bottom of the right sidebar, below
the preview card: on a small window they ended up in the far bottom-right corner,
had to be scrolled to, and the deeper options (model manager, GPU acceleration)
opened windows on top of that. Now: main window → this window → nothing.

Every control here is a big-target control from :mod:`ui.controls`, so check
states and choices are readable at a glance.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import engine
from strings import T
from core.features import metadata_writing_enabled
from .. import theme
from ..common import APP_TITLE
from ..controls import CheckRow, ChoiceRow, ScrollArea, fit_to_content
from .acceleration import AccelerationPanel


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.title(T('设置 · %s') % APP_TITLE)
        self.configure(bg=theme.C_BG)
        self.transient(master)
        self.protocol('WM_DELETE_WINDOW', self.on_close)

        head = tk.Frame(self, bg=theme.C_BG)
        head.pack(fill='x', padx=theme.S4, pady=(theme.S3, theme.S2))
        tk.Label(head, text=T('⚙ 设置'), bg=theme.C_BG, fg=theme.C_TEXT,
                 font=(theme.FONT, 15, 'bold')).pack(anchor='w')
        tk.Label(head, text=T('改动立刻保存，不用点确定。'), bg=theme.C_BG,
                 fg=theme.C_SUB, font=(theme.FONT, 9)).pack(anchor='w', pady=(2, 0))

        scroll = ScrollArea(self, bg=theme.C_BG, width=600)
        scroll.pack(fill='both', expand=True, padx=(theme.S4, theme.S2))
        body = scroll.inner
        self._build_detect(body)
        self._build_model(body)
        self._build_geo(body)
        self._build_output(body)
        self._build_accel(body)
        self._build_lang(body)
        scroll.bind_wheel()
        self._scroll = scroll

        foot = tk.Frame(self, bg=theme.C_BG)
        foot.pack(fill='x', padx=theme.S4, pady=theme.S3)
        ttk.Button(foot, text=T('模型管理…'), command=self.open_models).pack(side='left')
        ttk.Button(foot, text=T('关闭'), command=self.on_close).pack(side='right')

        fit_to_content(self, min_w=700, min_h=760)
        self.refresh()

    # ---------------------------------------------------------------- 卡片
    def _card(self, parent, title):
        card = tk.Frame(parent, bg=theme.C_CARD, highlightbackground=theme.C_LINE,
                        highlightthickness=1)
        card.pack(fill='x', pady=(0, theme.S3))
        tk.Label(card, text=title, bg=theme.C_CARD, fg=theme.C_SUB,
                 font=(theme.FONT, 10, 'bold')).pack(anchor='w', padx=theme.S4,
                                                     pady=(theme.S2 + 1, theme.S1))
        inner = tk.Frame(card, bg=theme.C_CARD)
        inner.pack(fill='x', padx=theme.S4, pady=(0, theme.S3))
        return inner

    @staticmethod
    def _hint(parent, text, **kw):
        lab = tk.Label(parent, text=text, bg=theme.C_CARD, fg=theme.C_SUB,
                       font=(theme.FONT, 9), anchor='w', justify='left',
                       wraplength=kw.pop('wraplength', 540), **kw)
        return lab

    # ---------------------------------------------------------------- 识别
    def _build_detect(self, body):
        card = self._card(body, T('识别'))
        app = self.app
        self.choice_frames = ChoiceRow(
            card, T('抽帧数'),
            [(T('自动'), T('自动')), ('2', '2'), ('4', '4'), ('6', '6'), ('8', '8')],
            app.var_frames)
        self.choice_frames.pack(fill='x', pady=(0, theme.S2))
        self._hint(card, T('自动：按视频时长挑 2~8 帧，够用就别改。')).pack(
            fill='x', pady=(0, theme.S3))

        row = tk.Frame(card, bg=theme.C_CARD)
        row.pack(fill='x', pady=(0, theme.S1))
        tk.Label(row, text=T('最多处理个数'), bg=theme.C_CARD, fg=theme.C_TEXT,
                 font=(theme.FONT, 10)).pack(side='left')
        tk.Label(row, text=T('个（0 = 全部）'), bg=theme.C_CARD, fg=theme.C_HINT,
                 font=(theme.FONT, 9)).pack(side='right', padx=(theme.S1, 0))
        app.sp_limit = ttk.Spinbox(row, from_=0, to=100000, width=7,
                                  textvariable=app.var_limit)
        app.sp_limit.pack(side='right')

        self.check_all = CheckRow(card, T('重新处理已改过名的'),
                                  app.var_all, bg=theme.C_CARD)
        self.check_all.pack(fill='x', pady=(theme.S2, 0))
        self._hint(card, T('不勾选时，名字已经是「视/图 + 日期」格式的文件会跳过。')).pack(
            fill='x', pady=(2, 0))

    # ---------------------------------------------------------------- 模型
    def _build_model(self, body):
        card = self._card(body, T('模型'))
        app = self.app
        row = tk.Frame(card, bg=theme.C_CARD)
        row.pack(fill='x')
        tk.Label(row, text=T('当前模型'), bg=theme.C_CARD, fg=theme.C_TEXT,
                 font=(theme.FONT, 10)).pack(side='left', padx=(0, theme.S2))
        self.btn_model = ttk.Button(row, text=T('模型管理…'), command=self.open_models)
        self.btn_model.pack(side='right')
        app.btn_model = self.btn_model
        app.cb_model = ttk.Combobox(
            row, textvariable=app.var_model, state='readonly', width=26,
            values=[item['name'] for item in engine.MODEL_CATALOG])
        app.cb_model.pack(side='right', padx=(0, theme.S2), fill='x', expand=True)
        app.cb_model.bind('<<ComboboxSelected>>',
                          lambda e: app.choose_model(app.var_model.get()))
        self._hint(card, T('主模型和配套 mmproj 要放在同一目录；点「模型管理」可以下载 4B / 2B 模型。')).pack(
            fill='x', pady=(theme.S1, 0))

    # ---------------------------------------------------------------- 定位
    def _build_geo(self, body):
        card = self._card(body, T('地名与定位'))
        app = self.app
        self.check_netgeo = CheckRow(card, T('联网精确定位'), app.var_netgeo,
                                     command=app._on_netgeo, bg=theme.C_CARD)
        self.check_netgeo.pack(fill='x')
        self.lbl_netgeo = self._hint(
            card, T('关着就是纯离线，一个网络包都不发。开启后会用在线地图接口，地址更准。'))
        self.lbl_netgeo.pack(fill='x', pady=(2, theme.S3))

        row = tk.Frame(card, bg=theme.C_CARD)
        row.pack(fill='x', pady=(0, theme.S1))
        tk.Label(row, text=T('高德 API Key'), bg=theme.C_CARD, fg=theme.C_TEXT,
                 font=(theme.FONT, 10)).pack(side='left', padx=(0, theme.S2))
        app.ent_amap = ttk.Entry(row, textvariable=app.var_amap)
        app.ent_amap.pack(side='left', fill='x', expand=True)
        app.var_amap.trace_add('write', lambda *a: app._save_amap())
        self._hint(card, T('国内坐标靠它查街道级地址，在 lbs.amap.com 免费申请；不填就一直用离线库。')).pack(
            fill='x', pady=(0, theme.S3))

        self.choice_geolv = ChoiceRow(
            card, T('地址粒度'),
            [(T('到区县'), T('到区县')), (T('到街道'), T('到街道'))],
            app.var_geolv, command=app._on_geolv)
        self.choice_geolv.pack(fill='x')

    # ---------------------------------------------------------------- 输出
    def _build_output(self, body):
        card = self._card(body, T('识别结果怎么保存'))
        app = self.app
        self.check_meta = CheckRow(card, T('写入文件元数据'), app.var_meta,
                                   command=lambda: app._save_sw('write_meta', app.var_meta),
                                   bg=theme.C_CARD)
        self.check_meta.pack(fill='x')
        if not metadata_writing_enabled():
            app.var_meta.set(False)
            self.check_meta.set_enabled(False)
        self.check_txt = CheckRow(card, T('生成 .ai.txt 摘要'), app.var_txt,
                                  command=lambda: app._save_sw('write_txt', app.var_txt),
                                  bg=theme.C_CARD)
        self.check_txt.pack(fill='x', pady=(theme.S1, 0))
        self.check_md = CheckRow(card, T('生成 目录说明.md'), app.var_md,
                                 command=lambda: app._save_sw('write_md', app.var_md),
                                 bg=theme.C_CARD)
        self.check_md.pack(fill='x', pady=(theme.S1, 0))
        self._hint(card, T('三个开关各管一件事，关掉就不做。元数据写进文件本身（照片 EXIF/XMP、视频容器标签），'
                           '文件拷到哪里都带着走。')).pack(fill='x', pady=(theme.S2, 0))

    # ---------------------------------------------------------------- 加速
    def _build_accel(self, body):
        card = self._card(body, T('GPU 加速'))
        self.panel = AccelerationPanel(card, self.app, bg=theme.C_CARD)
        self.panel.pack(fill='x')

    # ---------------------------------------------------------------- 语言
    def _build_lang(self, body):
        card = self._card(body, T('语言'))
        app = self.app
        self.choice_lang = ChoiceRow(
            card, T('界面语言'),
            [(T('中文'), T('中文')), ('English', 'English')],
            app.var_lang, command=app._on_lang_pick)
        self.choice_lang.pack(fill='x')
        self._hint(card, T('切换后需要重启软件生效。')).pack(fill='x', pady=(2, 0))

    # ---------------------------------------------------------------- 状态
    def refresh(self):
        """Called by the main window whenever the model/accelerator state changes."""
        busy = bool(self.app.running or self.app._model_busy)
        for widget in (self.choice_frames, self.check_all, self.choice_geolv,
                       self.check_netgeo, self.check_txt, self.check_md,
                       self.choice_lang):
            try:
                widget.set_enabled(not busy)
            except Exception:
                pass
        if metadata_writing_enabled():
            self.check_meta.set_enabled(not busy)
        for widget in (getattr(self.app, 'sp_limit', None),
                       getattr(self.app, 'cmb_model', None),
                       getattr(self.app, 'ent_amap', None),
                       getattr(self.app, 'btn_model', None)):
            try:
                if widget is not None:
                    widget.configure(state='disabled' if busy else
                                     ('readonly' if isinstance(widget, ttk.Combobox) else 'normal'))
            except Exception:
                pass
        try:
            self.panel.refresh()
        except Exception:
            pass

    def open_models(self):
        """Sideways switch to the model manager — same level, not a third one."""
        self.on_close()
        self.app.open_model_dialog()

    def on_close(self):
        try:
            self.panel.stop()
        except Exception:
            pass
        self.destroy()
