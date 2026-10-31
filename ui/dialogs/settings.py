# -*- coding: utf-8 -*-
"""One settings window, split into「常用」and「高级」.

The window is the only place settings live now (the right sidebar is gone — its
270px went back to the result table, which needs 956px for all seven columns).
Two levels deep at most: main window → this window → nothing else.

Layout rule: whatever a first-time user needs is in 常用 and visible without
scrolling; everything technical is folded under 高级. Nothing is hidden behind a
third window — model manager switches sideways, GPU acceleration is inline.
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
    def __init__(self, master, app, focus_section=None):
        super().__init__(master)
        self.app = app
        self._focus_section = focus_section
        self._card_frames = []
        self.title(T('设置 · %s') % APP_TITLE)
        self.configure(bg=theme.C_BG)
        self.transient(master)
        self.protocol('WM_DELETE_WINDOW', self.on_close)

        head = tk.Frame(self, bg=theme.C_BG)
        head.pack(fill='x', padx=theme.S4, pady=(theme.S3, theme.S2))
        tk.Label(head, text=T('⚙ 设置'), bg=theme.C_BG, fg=theme.C_TEXT,
                 font=(theme.FONT, 15, 'bold')).pack(anchor='w')
        tk.Label(head, text=T('上面三项改完就够用；技术项都在「高级」里，不看也不影响。'),
                 bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 9)).pack(anchor='w', pady=(2, 0))

        scroll = ScrollArea(self, bg=theme.C_BG, width=600)
        scroll.pack(fill='both', expand=True, padx=(theme.S4, theme.S2))
        body = scroll.inner

        self._group(body, T('常用'))
        self._build_scope(body)
        self._build_model(body)
        self._build_save(body)

        self._build_advanced(body)
        scroll.bind_wheel()
        self._scroll = scroll

        foot = tk.Frame(self, bg=theme.C_BG)
        foot.pack(fill='x', padx=theme.S4, pady=theme.S3)
        ttk.Button(foot, text=T('模型管理…'), command=self.open_models).pack(side='left')
        ttk.Button(foot, text=T('关于'), style='Link.TButton',
                   command=lambda: app.open_about()).pack(side='left', padx=(10, 0))
        ttk.Button(foot, text=T('关闭'), command=self.on_close).pack(side='right')

        fit_to_content(self, design_w=720, design_h=700, min_w=640, min_h=520)
        self.refresh()
        if focus_section:
            self.after(80, lambda: self.focus_section(focus_section))

    def focus_section(self, name):
        """把滚动视图带到某一组。

        主窗口状态栏点「● 模型待加载」会传 'model' 过来——用户从「按开始没反应」
        点进来，应该直接看到模型那一组，而不是自己在一屏设置里找。
        """
        idx = {'scope': 0, 'model': 1, 'save': 2}.get(name)
        try:
            if idx is None or idx >= len(self._card_frames):
                return
            w = self._card_frames[idx]
            self.update_idletasks()
            total = max(1, self._scroll.inner.winfo_height())
            y = w.winfo_y() / float(total)
            self._scroll.canvas.yview_moveto(min(1.0, max(0.0, y - 0.02)))
        except Exception:
            pass

    # ---------------------------------------------------------------- 版式
    def _group(self, parent, title):
        """分组标题：常用 / 高级之间的一道视觉分隔。"""
        tk.Label(parent, text=title, bg=theme.C_BG, fg=theme.C_SUB,
                 font=(theme.FONT, 11, 'bold')).pack(anchor='w', pady=(0, theme.S1))

    def _card(self, parent, title):
        card = tk.Frame(parent, bg=theme.C_CARD, highlightbackground=theme.C_LINE,
                        highlightthickness=1)
        card.pack(fill='x', pady=(0, theme.S3))
        # 记下顺序，focus_section() 靠它定位（范围=0 / 模型=1 / 保存=2）
        self._card_frames.append(card)
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

    # ---------------------------------------------------------------- 处理范围
    def _build_scope(self, body):
        """处理范围：从主界面第二行搬过来的四个勾选。

        主界面现在只有三步，勾选挤在那里会让人先看见一堆开关再看见「开始识别」。
        搬进设置后主界面干净了，而范围说明仍在主界面第 3 步右侧实时显示
        （见 App._refresh_scope），改完立刻能看到会发生什么。
        """
        card = self._card(body, T('处理范围'))
        app = self.app

        def scope_changed():
            """改完立刻算一遍「这次会处理什么」，主界面和这里同时更新。"""
            app._refresh_scope()
            try:
                self.lbl_scope.configure(text=app.lbl_scope.cget('text'),
                                         fg=app.lbl_scope.cget('fg'))
            except Exception:
                pass

        app.cb_ai_desc = CheckRow(card, T('AI描述'), app.var_ai_desc,
                                  command=lambda: (app._on_ai_desc(), scope_changed()),
                                  bg=theme.C_CARD)
        app.cb_ai_desc.pack(fill='x')
        self._hint(card, T('关掉就不调用模型，只按拍摄时间和 GPS 命名，秒级完成。')).pack(
            fill='x', pady=(2, theme.S2))

        row = tk.Frame(card, bg=theme.C_CARD)
        row.pack(fill='x')
        app.cb_video = CheckRow(row, T('视频'), app.var_video,
                                command=scope_changed, bg=theme.C_CARD)
        app.cb_video.pack(side='left')
        app.cb_photos = CheckRow(row, T('照片'), app.var_photos,
                                 command=scope_changed, bg=theme.C_CARD)
        app.cb_photos.pack(side='left', padx=(20, 0))

        app.cb_desc_photo = CheckRow(card, T('照片也写内容描述'), app.var_desc_photo,
                                     command=scope_changed, bg=theme.C_CARD)
        app.cb_desc_photo.pack(fill='x', pady=(theme.S2, 0))
        self.lbl_scope = self._hint(card, '')
        self.lbl_scope.pack(fill='x', pady=(2, 0))
        scope_changed()

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

    # ---------------------------------------------------------------- 保存方式
    def _build_save(self, body):
        """把「写元数据 / .ai.txt / 目录说明.md」三个开关合成一个二选一。

        三个开关各管一件事，对第一次用的人是三行看不懂的技术名词；而实际选择
        只有两种：要么只改名（干净），要么连说明文件一起留（完整）。细项仍在
        高级里，想单独调的人照样能调。
        """
        card = self._card(body, T('保存方式'))
        app = self.app
        self.var_save = tk.StringVar(value=self._save_mode())
        self.choice_save = ChoiceRow(
            card, T('结果怎么留'),
            [(T('简洁'), T('简洁')), (T('详细'), T('详细'))],
            self.var_save, command=self._on_save_mode)
        self.choice_save.pack(fill='x')
        self.lbl_save = self._hint(card, '')
        self.lbl_save.pack(fill='x', pady=(2, 0))
        self._paint_save()

    def _save_mode(self) -> str:
        app = self.app
        detailed = bool(app.var_txt.get() or app.var_md.get() or app.var_meta.get())
        return T('详细') if detailed else T('简洁')

    def _on_save_mode(self, *a):
        detailed = (self.var_save.get() == T('详细'))
        app = self.app
        for key, var in (('write_txt', app.var_txt), ('write_md', app.var_md)):
            var.set(detailed)
            app._save_sw(key, var)
        if metadata_writing_enabled():
            app.var_meta.set(detailed)
            app._save_sw('write_meta', app.var_meta)
        self._paint_save()

    def _paint_save(self):
        if self.var_save.get() == T('详细'):
            text = T('改名，并留下 .ai.txt 摘要和「目录说明.md」，照片/视频还会写入元数据。')
        else:
            text = T('只改文件名，不多出一个文件。想留说明书再切到「详细」。')
        self.lbl_save.configure(text=text)

    # ---------------------------------------------------------------- 高级
    def _build_advanced(self, body):
        """高级设置默认折叠：打开软件先看到的三格里没有一项技术名词。"""
        try:
            opened = bool((engine.config() or {}).get('adv_open', False))
        except Exception:
            opened = False
        self.var_adv = tk.BooleanVar(value=opened)

        bar = tk.Frame(body, bg=theme.C_BG)
        bar.pack(fill='x', pady=(theme.S2, theme.S1))
        self.btn_adv = ttk.Button(bar, text='', style='Sec.TButton',
                                  command=self._toggle_adv)
        self.btn_adv.pack(side='left')
        tk.Label(bar, text=T('抽帧数 · 定位 · 元数据 · GPU 加速 · 语言'),
                 bg=theme.C_BG, fg=theme.C_HINT,
                 font=(theme.FONT, 9)).pack(side='left', padx=(10, 0))

        self.adv = tk.Frame(body, bg=theme.C_BG)
        self._build_detect(self.adv)
        self._build_geo(self.adv)
        self._build_output(self.adv)
        self._build_accel(self.adv)
        self._build_lang(self.adv)
        self._apply_adv(opened)

    def _apply_adv(self, opened: bool):
        """展开/收起高级区。只改界面，不写盘。"""
        self.var_adv.set(bool(opened))
        try:
            if opened:
                self.adv.pack(fill='x', pady=(theme.S2, 0))
            else:
                self.adv.pack_forget()
            self.btn_adv.configure(text=(T('▾ 高级设置') if opened else T('▸ 高级设置')))
        except Exception:
            pass

    def _toggle_adv(self):
        opened = not bool(self.var_adv.get())
        self._apply_adv(opened)
        try:
            engine.save_config(adv_open=opened)
        except Exception:
            pass

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

    # ---------------------------------------------------------------- 输出细项
    def _build_output(self, body):
        card = self._card(body, T('识别结果怎么保存（细项）'))
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
        self._hint(card, T('上面「保存方式」就是这三项的总开关；这里可以单独挑。'
                           '元数据写进文件本身（照片 EXIF/XMP、视频容器标签），拷到哪都带着走。')).pack(
            fill='x', pady=(theme.S2, 0))

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
                       self.choice_lang, self.choice_save, self.app.cb_ai_desc,
                       self.app.cb_video, self.app.cb_photos):
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
        # 高级里的细项被改过（比如在别处切了保存方式）时，二选一要跟着走
        try:
            self.var_save.set(self._save_mode())
            self._paint_save()
        except Exception:
            pass

    def open_models(self):
        """Sideways switch to the model manager — same level, not a third one."""
        self.on_close()
        self.app.open_model_dialog()

    def stop(self):
        """主窗口关闭 / 切到别的二级窗口时先停掉组件下载。"""
        try:
            self.panel.stop()
        except Exception:
            pass

    def on_close(self):
        self.stop()
        self.destroy()
