# -*- coding: utf-8 -*-
"""Main window, UI events and existing recognition-task orchestration."""
from __future__ import annotations

import io
import os
import queue
import re
import shutil
import threading
import time
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

import engine
import strings
from strings import T
from core.features import metadata_writing_enabled
from . import theme
from .common import APP_AUTHOR, APP_SUB, APP_TITLE, APP_VER, asset_path, cut, fmt_dur, open_path
from .widgets import Hover, Tip
from .dialogs.information import AboutDialog, HelpDialog, help_path
from .dialogs.models import ModelDialog
from .dialogs.acceleration import AccelerationDialog
from .dialogs.review import PlanDialog, RenameResultDialog, ReplaceDialog


try:
    from PIL import Image, ImageOps, ImageTk
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

_PREV_W = 280


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_evt = threading.Event()
        self.running = False
        self._model_busy = False
        self.state = 'idle'         # idle / running / stopping / stopped / done
        self.total = 0
        self.done = 0
        self.t_start = 0.0
        self._plan_job = None       # 目录改动后延迟刷新「核对并改名」可用状态
        self._thumb_ref = None          # 防止 PhotoImage 被 GC
        self._models: list[str] = []
        self._plan: list = []           # 最近一次识别出的计划表
        self._rows: dict = {}           # index -> 结果 dict（点行看详情）
        self._thumbs: dict = {}         # index -> 宫格图 bytes
        self._photo_thumbs: dict = {}   # index -> 照片缩略图 PhotoImage（点行时现读，缓存复用）
        self._tip = None                # 悬浮提示（文件名被截断时用）

        root.title('%s %s' % (APP_TITLE, APP_VER))
        root.geometry(self._saved_geometry('1120x760'))
        root.minsize(940, 620)
        root.configure(bg=theme.C_BG)
        self._icons = []                # 防止图标 PhotoImage 被 GC
        p = asset_path('logo_round.png')
        if p and _HAS_PIL:
            try:
                ic = ImageTk.PhotoImage(Image.open(p))
                self._icons.append(ic)
                root.iconphoto(True, ic)
            except Exception:
                pass
        self._setup_style()
        self._build()
        self._refresh_scope()
        self._set_run_state('idle')
        self._refresh_plan_btn()
        self._tick()
        self._poll_server()
        self._autostart()
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        # 等窗口真正显示出来再弹说明，否则会抢在主窗口前面闪一下
        root.after(800, self._maybe_help)

    # ------------------------------------------------------------ 窗口
    @staticmethod
    def _work_area() -> tuple[int, int]:
        """屏幕工作区（去掉任务栏等）。取不到就退回 1920x1080。"""
        try:
            import ctypes
            import ctypes.wintypes as wt
            ctypes.windll.user32.SetProcessDPIAware()
            rc = wt.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(
                    0x0030, 0, ctypes.byref(rc), 0):   # SPI_GETWORKAREA
                return (max(800, rc.right - rc.left), max(600, rc.bottom - rc.top))
        except Exception:
            pass
        return (1920, 1080)

    @classmethod
    def _saved_geometry(cls, default: str) -> str:
        """记住上次拉到多大的窗口（存在 config.json 的 geometry 里）。

        启动时按当前屏幕工作区裁剪：记住的尺寸若超过屏幕 80% 的宽或高，
        就作废回退到默认值（1120x760）——否则窗口会被上次的尺寸撑成
        接近全屏，多任务时不顺手。保存前也会裁剪，不会越存越大。
        """
        try:
            g = (engine.config() or {}).get('geometry') or ''
            if re.match(r'^\d{3,5}x\d{3,5}([+-]\d{1,5}[+-]\d{1,5})?$', g):
                w, h = (int(x) for x in g.split('+')[0].split('x'))
                sw, sh = cls._work_area()
                if (w > sw * 0.8 or h > sh * 0.8
                        or w > sw or h > sh):
                    return default
                return g
        except Exception:
            pass
        return default

    def on_close(self):
        for window in self.root.winfo_children():
            if isinstance(window, (ModelDialog, AccelerationDialog)):
                window.cancel_download.set()
        try:
            g = self.root.geometry()
            m = re.match(r'^(\d+)x(\d+)', g)
            if m:
                sw, sh = self._work_area()
                w = min(int(m.group(1)), sw)
                h = min(int(m.group(2)), sh)
                g = '%dx%d%s' % (w, h, g[len(m.group(0)):])
            engine.save_config(geometry=g)
        except Exception:
            pass
        self.root.destroy()

    # ------------------------------------------------------------ 启动自带运行时
    def _autostart(self):
        """后台初始化本地库，不在启动时占用模型内存。"""
        threading.Thread(target=self._autostart_bg, daemon=True).start()

    def _autostart_bg(self):
        try:
            ok = engine.start_server(on_log=lambda s: self.q.put(('log', s, 'err')))
            self.q.put(('log', T('本地推理库已就绪 · CPU') if ok else T('本地推理库不可用'),
                        'ok' if ok else 'err'))
        except Exception:
            self.q.put(('log', traceback.format_exc(), 'err'))
        self.q.put(('srv', None, None))

    # ------------------------------------------------------------ 样式
    def _setup_style(self):
        theme.setup_style(self.root)

    # ------------------------------------------------------------ 布局
    # 从上到下：标题栏 → 目录行 → 处理范围行 → 操作行 → 进度条 → 结果表 → 底部状态栏
    # 右侧是可收起的侧栏（预览 + 设置）。一眼只看到一个主要动作：▶ 开始识别。
    def _build(self):
        # 打包顺序很重要：先给状态栏/署名占位（side=bottom 先 pack 先占坑），
        # body 才不会在内容超高时把它们挤出去。
        self._build_head()
        self._build_statusbar()
        tk.Label(self.root,
                 text=T('地名数据 © OpenStreetMap 贡献者（ODbL）· 全球城市 GeoNames（CC-BY 4.0）'),
                 bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 8)).pack(side='bottom', fill='x',
                                                         padx=14, pady=(0, 4))

        # 主区：左列（控制 + 结果 + 日志抽屉），右侧栏（预览 + 设置）。
        # 侧栏**先**入队：窗口再窄它也保得住自己的宽度，被裁的只会是结果表。
        body = tk.Frame(self.root, bg=theme.C_BG)
        body.pack(fill='both', expand=True, padx=14, pady=(0, theme.S2))
        left = tk.Frame(body, bg=theme.C_BG)
        self._build_sidebar(body)
        left.pack(side='left', fill='both', expand=True)

        self._build_control(left)
        self._build_table(left)
        self._build_log_drawer(left)

        # 识别过程中不能再动的东西（改了目录或范围会让结果对不上号）。
        # 侧栏里的设置也是「运行中不要改」，所以这里统一收，别漏掉后来加的项。
        self._busy_widgets = [w for w in (
            self.ent_dir, self.cmb_preset, self.cb_video, self.cb_photos,
            self.cb_desc_photo, self.cmb_frames, self.sp_limit, self.cb_all,
            self.cb_model, self.btn_model, self.cmb_lang,
        ) if w is not None]

    def _card(self, parent, title=None):
        f = tk.Frame(parent, bg=theme.C_CARD, highlightbackground=theme.C_LINE,
                     highlightthickness=1)
        if title:
            tk.Label(f, text=title, bg=theme.C_CARD, fg=theme.C_SUB,
                     font=(theme.FONT, 10, 'bold')).pack(anchor='w', padx=theme.S4, pady=(theme.S2 + 1, 0))
        return f

    def _build_head(self):
        """标题栏：商标 + 产品名 + 副标题/作者。

        原来这行右边还挂着「自带运行时 · 端口 · 模型库」和模型服务状态，太像调试信息，
        v3.16 起统一挪到底部状态栏。
        """
        head = tk.Frame(self.root, bg=theme.C_BG)
        head.pack(fill='x', padx=14, pady=(12, 6))
        p = asset_path('logo_head.png')
        if p:
            try:
                lg = tk.PhotoImage(file=p)
                self._icons.append(lg)
                tk.Label(head, image=lg, bg=theme.C_BG).pack(side='left', padx=(0, 9))
            except Exception:
                pass
        tf = tk.Frame(head, bg=theme.C_BG)
        tf.pack(side='left')
        tk.Label(tf, text=APP_TITLE, bg=theme.C_BG, fg=theme.C_TEXT,
                 font=(theme.FONT, 15, 'bold')).pack(anchor='w')
        tk.Label(tf, text='%s　%s' % (APP_SUB, APP_AUTHOR), bg=theme.C_BG,
                 fg=theme.C_SUB, font=(theme.FONT, 9)).pack(anchor='w')
        # 使用说明：标题栏右侧常驻，第一次启动还会自己弹一次（见 _maybe_help）
        ttk.Button(head, text=T('❓ 使用说明'), command=self.open_help,
                   width=theme.button_width(12, 16)).pack(side='right', pady=(4, 0))

    def _build_control(self, parent):
        card = self._card(parent)
        card.pack(fill='x')
        inner = tk.Frame(card, bg=theme.C_CARD)
        inner.pack(fill='x', padx=12, pady=10)

        # —— 第一行：目录（右边的「浏览… / 预设」先从右缘占位，窄窗口也不会被挤出去）
        r1 = tk.Frame(inner, bg=theme.C_CARD)
        r1.pack(fill='x')
        self._preset_disp = {T(k): k for k in engine.PRESETS}
        self.var_preset = tk.StringVar(value='')
        # 没有预设时不建这个下拉，免得显示一个空框
        if engine.PRESETS:
            self.cmb_preset = ttk.Combobox(r1, textvariable=self.var_preset, width=12,
                                           state='readonly',
                                           values=list(self._preset_disp))
            self.cmb_preset.pack(side='right')
            self.cmb_preset.bind('<<ComboboxSelected>>', self._on_preset_pick)
        else:
            self.cmb_preset = None
        ttk.Button(r1, text=T('浏览…'), command=self.pick_dir).pack(side='right', padx=(0, 8))
        tk.Label(r1, text=T('目录'), bg=theme.C_CARD, fg=theme.C_TEXT,
                 font=(theme.FONT, 10)).pack(side='left')
        self.var_dir = tk.StringVar(
            value=(list(engine.PRESETS.values())[0] if engine.PRESETS else ''))
        # 换了目录，上一次的运行状态就不算数了，回到「未开始」
        self.var_dir.trace_add('write', lambda *a: self._on_dir_change())
        self.ent_dir = ttk.Entry(r1, textvariable=self.var_dir, font=(theme.FONT, 10))
        self.ent_dir.pack(side='left', fill='x', expand=True, padx=8)

        # —— 第二行：处理范围（视频 / 照片 各自独立勾选）
        r2 = tk.Frame(inner, bg=theme.C_CARD)
        r2.pack(fill='x', pady=(12, 0))
        box = ttk.Labelframe(r2, text=T(' 处理范围 '), padding=(14, 7))
        box.pack(fill='x')
        # —— 总开关「AI描述」：勾着才调模型。关掉后视频不抽帧、照片不送模型，
        #    全部只按「时间 + 地名」命名（慢机器的纯离线快路径）。
        #    状态写进 config.json（键名 ai_desc，默认开），下次打开保持。
        self.var_ai_desc = tk.BooleanVar(value=self._sw('ai_desc', True))
        self.cb_ai_desc = ttk.Checkbutton(box, text=T('AI描述'),
                                          variable=self.var_ai_desc,
                                          style='Scope.TCheckbutton',
                                          command=self._on_ai_desc)
        self.cb_ai_desc.pack(side='left')
        self.var_video = tk.BooleanVar(value=True)
        self.cb_video = ttk.Checkbutton(box, text=T('视频'), variable=self.var_video,
                                        style='Scope.TCheckbutton',
                                        command=self._refresh_scope)
        self.cb_video.pack(side='left', padx=(22, 0))
        self.var_photos = tk.BooleanVar(value=True)
        self.cb_photos = ttk.Checkbutton(box, text=T('照片'), variable=self.var_photos,
                                         style='Scope.TCheckbutton',
                                         command=self._refresh_scope)
        self.cb_photos.pack(side='left', padx=(22, 0))
        # 「照片也写内容描述」是范围选项（决定照片要不要多花 11 秒/张送模型），
        # 跟着视频/照片放一起；原来的位置让给侧栏底部的耗时说明
        # 「照片也写内容描述」常开：每次启动默认打勾（照片每张多花约 11 秒，
        # 但能拿到「奶奶戴帽微笑」这类描述）；想走只读 EXIF 的秒级快路径，
        # 临时取消勾选即可——该选项不写盘，下次打开恢复打勾。
        # v3.22 起它是「AI描述」的子选项：总开关关掉时自动置灰（见 _refresh_scope）。
        self.var_desc_photo = tk.BooleanVar(value=True)
        self.cb_desc_photo = ttk.Checkbutton(box, text=T('照片也写内容描述'),
                                             variable=self.var_desc_photo,
                                             command=self._refresh_scope)
        self.cb_desc_photo.pack(side='left', padx=(22, 0))

        # —— 第三行：只有一个主要动作。「停止识别」平时不占位置，
        #    运行时才 pack 出来（见 _show_stop），结束/停掉后自动收回去。
        r3 = tk.Frame(inner, bg=theme.C_CARD)
        r3.pack(fill='x', pady=(12, 0))
        self.btn_go = ttk.Button(r3, text=T('▶ 开始识别'), style='Go.TButton',
                                 command=self.on_go)
        self.btn_go.pack(side='left')
        self.btn_stop = ttk.Button(r3, text=T('停止识别'), style='Sec.TButton',
                                   width=theme.button_width(10, 9), command=self.on_stop,
                                   state='disabled')
        self.btn_stop.pack_forget()

        # —— 第四行：进度（左：细进度；右：本轮状态灯）
        r4 = tk.Frame(inner, bg=theme.C_CARD)
        r4.pack(fill='x', pady=(12, 0))
        self.pb = ttk.Progressbar(r4, mode='determinate', maximum=100)
        self.pb.pack(fill='x')
        r4b = tk.Frame(r4, bg=theme.C_CARD)
        r4b.pack(fill='x', pady=(4, 0))
        self.lbl_prog = tk.Label(r4b, text=T('待处理'), bg=theme.C_CARD, fg=theme.C_SUB,
                                 font=(theme.FONT, 9), anchor='w')
        self.lbl_prog.pack(side='left')
        # 状态灯：说清现在是「没开始 / 正在跑 / 已停 / 跑完了」，
        # 光看按钮的禁用状态分不出来（v3.4 反馈）
        self.lbl_state = tk.Label(r4b, text=T('● 未开始'), bg=theme.C_CARD, fg=theme.C_SUB,
                                  font=(theme.FONT, 10, 'bold'), anchor='e')
        self.lbl_state.pack(side='right')

    # ------------------------------------------------------------ 结果表
    def _build_table(self, parent):
        card = self._card(parent)
        card.pack(fill='both', expand=True, pady=(10, 0))
        tk.Label(card, text=T('处理结果'), bg=theme.C_CARD, fg=theme.C_SUB,
                 font=(theme.FONT, 10, 'bold')).pack(anchor='w', padx=theme.S4, pady=(theme.S2 + 1, theme.S1))

        cols = ('no', 'old', 'new', 'place', 'desc', 'sec', 'st')
        heads = ('#', T('原文件名'), T('新文件名'), T('地名'), T('描述'), T('耗时'), T('状态'))
        # 文件名列固定宽（名字是等宽可预估的），窗口拉宽时多余空间全给地名/描述
        widths = (36, 172, 196, 116, 108, 46, 56)
        wrap = tk.Frame(card, bg=theme.C_CARD)
        wrap.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        self.tree = ttk.Treeview(wrap, columns=cols, show='headings', height=9)
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor='w',
                             stretch=(c in ('place', 'desc')))
        vs = ttk.Scrollbar(wrap, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side='left', fill='both', expand=True)
        vs.pack(side='right', fill='y')
        self.tree.tag_configure('odd', background=theme.C_ROW_ALT)
        self.tree.tag_configure('fail', foreground=theme.C_ERR)
        self.tree.tag_configure('st_ok', background='#eefaf1')
        self.tree.tag_configure('st_skip', foreground=theme.C_SUB)
        self.tree.tag_configure('st_fail', background='#fdeceb')
        self.tree.bind('<<TreeviewSelect>>', self.on_pick_row)
        Tip(self.root, self.tree)

    # ------------------------------------------------------------ 运行日志（可折叠）
    def _build_log_drawer(self, parent):
        """日志默认收起成一条窄条，出错时自动弹开（见 log()）。"""
        self.var_log = tk.BooleanVar(value=self._sw('logopen', False))
        card = self._card(parent)
        card.pack(fill='x', pady=(10, 0))
        self.log_head = tk.Frame(card, bg=theme.C_CARD, cursor='hand2')
        self.log_head.pack(fill='x', padx=12, pady=(8, 6))
        self.lbl_log = tk.Label(self.log_head, text='', bg=theme.C_CARD, fg=theme.C_SUB,
                                font=(theme.FONT, 10, 'bold'), cursor='hand2')
        self.lbl_log.pack(side='left')
        self.log_tools = tk.Frame(self.log_head, bg=theme.C_CARD)
        ttk.Button(self.log_tools, text=T('清空'), width=theme.button_width(6, 8),
                   command=lambda: self.txt.delete('1.0', 'end')).pack(side='right')
        ttk.Button(self.log_tools, text=T('复制全部'), width=theme.button_width(8, 10),
                   command=self.copy_log).pack(side='right', padx=6)
        for w in (self.log_head, self.lbl_log):
            w.bind('<Button-1>', lambda e: self.toggle_log())

        self.log_body = tk.Frame(card, bg=theme.C_CARD)
        wrap = tk.Frame(self.log_body, bg=theme.C_CARD)
        wrap.pack(fill='both', expand=True, padx=12, pady=(0, 10))
        self.txt = tk.Text(wrap, height=7, bg=theme.C_LOG_BG, fg=theme.C_LOG_FG,
                           insertbackground=theme.C_LOG_FG, font=('Consolas', 9),
                           relief='flat', wrap='none')
        vs = ttk.Scrollbar(wrap, orient='vertical', command=self.txt.yview)
        hs = ttk.Scrollbar(wrap, orient='horizontal', command=self.txt.xview)
        self.txt.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.txt.grid(row=0, column=0, sticky='nsew')
        vs.grid(row=0, column=1, sticky='ns')
        hs.grid(row=1, column=0, sticky='ew')
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.txt.tag_configure('err', foreground='#ff7b72')
        self.txt.tag_configure('ok', foreground='#7ee787')
        self.txt.tag_configure('dim', foreground='#8b949e')
        self.txt.tag_configure('head', foreground='#79c0ff')
        self._log_apply(bool(self.var_log.get()))

    def _log_apply(self, opened: bool):
        # 用自己的标记而不是 winfo_ismapped()：见 _show_stop 的说明
        if getattr(self, '_log_shown', None) == opened and self.var_log.get() == opened:
            return
        self.var_log.set(opened)
        self.lbl_log.configure(text=(T('▾ 运行日志') if opened else T('▸ 运行日志')))
        try:
            if opened:
                self.log_tools.pack(side='right')
                self.log_body.pack(fill='both', expand=True)
            else:
                self.log_tools.pack_forget()
                self.log_body.pack_forget()
            self._log_shown = opened
        except Exception:
            pass
        try:
            engine.save_config(logopen=bool(opened))
        except Exception:
            pass
        self._sync_more_menu()

    def toggle_log(self):
        self._log_apply(not bool(self.var_log.get()))

    # ------------------------------------------------------------ 右侧栏
    def _build_sidebar(self, parent):
        self.var_side = tk.BooleanVar(value=self._sw('sidebar', True))
        sb = tk.Frame(parent, bg=theme.C_BG)
        sb.pack(side='right', fill='y', padx=(10, 0))

        # —— 收起态：一条窄边 + 一个 ⚙ 按钮
        self.sb_closed = tk.Frame(sb, bg=theme.C_CARD)
        b = ttk.Button(self.sb_closed, text='⚙', width=3,
                       command=lambda: self._side_apply(True))
        b.pack(padx=4, pady=(4, 2))
        Hover(b, lambda: T('展开侧栏（预览 + 设置）'))
        tk.Label(self.sb_closed, text=T('设置'), bg=theme.C_CARD, fg=theme.C_SUB,
                 font=(theme.FONT, 8)).pack(pady=(0, 6))

        # —— 展开态：整条侧栏共用一个滚动容器（预览在上、设置在下，
        #    以后设置再加也不会把预览挤出窗口）
        self.sb_open = tk.Frame(sb, bg=theme.C_BG)
        outer, inner, cv = self._scroller(self.sb_open, bg=theme.C_BG)
        outer.pack(fill='both', expand=True)
        self._build_preview(inner)
        self._build_settings(inner)
        self._wheel_up(cv, inner)
        self._side_apply(bool(self.var_side.get()))

    def _side_apply(self, opened: bool):
        self.var_side.set(opened)
        if getattr(self, '_side_shown', None) == opened:
            return
        try:
            self.sb_open.pack_forget()
            self.sb_closed.pack_forget()
            if opened:
                # expand=True：让侧栏吃满整个主区高度（否则只到内容的自然高度）
                self.sb_open.pack(fill='both', expand=True)
            else:
                self.sb_closed.pack(fill='y', expand=True)
            self._side_shown = opened
        except Exception:
            pass
        try:
            engine.save_config(sidebar=bool(opened))
        except Exception:
            pass

    @staticmethod
    def _sw(key, default):
        """从 config.json 读一个布尔开关（缺值/异常都返回默认）。"""
        try:
            v = (engine.config() or {}).get(key)
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
        except Exception:
            pass
        return default

    @staticmethod
    def _scroller(parent, bg=theme.C_CARD, width=316):
        """可滚动容器：整条侧栏（预览 + 设置）共用，设置项以后还会往里加。"""
        outer = tk.Frame(parent, bg=bg)
        cv = tk.Canvas(outer, bg=bg, highlightthickness=0, width=width, height=120)
        vs = ttk.Scrollbar(outer, orient='vertical', command=cv.yview)
        cv.configure(yscrollcommand=vs.set)
        cv.pack(side='left', fill='both', expand=True)
        vs.pack(side='right', fill='y')
        inner = tk.Frame(cv, bg=bg)
        iid = cv.create_window((0, 0), window=inner, anchor='nw')
        inner.bind('<Configure>',
                   lambda e: cv.configure(scrollregion=cv.bbox('all')))
        cv.bind('<Configure>', lambda e: cv.itemconfigure(iid, width=e.width))

        def _wheel(e):
            try:
                cv.yview_scroll(int(-1 * (e.delta / 120)), 'units')
            except Exception:
                pass
        for w in (outer, cv, inner):
            w.bind('<MouseWheel>', _wheel)
        return outer, inner, cv

    @staticmethod
    def _wheel_up(cv, top):
        """把滚轮事件递归绑到一个容器及其全部子孙上（Tk 不会自动往下传）。"""

        def _wheel(e):
            try:
                cv.yview_scroll(int(-1 * (e.delta / 120)), 'units')
            except Exception:
                pass

        def _rec(w):
            try:
                w.bind('<MouseWheel>', _wheel)
                for c in w.winfo_children():
                    _rec(c)
            except Exception:
                pass
        _rec(top)

    def _build_preview(self, parent):
        card = self._card(parent)
        card.pack(fill='x')
        head = tk.Frame(card, bg=theme.C_CARD)
        head.pack(fill='x', padx=12, pady=(9, 0))
        tk.Label(head, text=T('模型看到的画面'), bg=theme.C_CARD, fg=theme.C_SUB,
                 font=(theme.FONT, 10, 'bold')).pack(side='left')
        b = ttk.Button(head, text='»', width=3,
                       command=lambda: self._side_apply(False))
        b.pack(side='right')
        Hover(b, lambda: T('收起侧栏'))
        self.lbl_prev = tk.Label(card, text=T('届时显示抽帧缩略图'), bg='#eef1f5',
                                 fg=theme.C_SUB, font=(theme.FONT, 9), width=34, height=9)
        self.lbl_prev.pack(padx=12, pady=(6, 6), fill='x')
        self.lbl_prev_info = tk.Label(card, text='', bg=theme.C_CARD, fg=theme.C_TEXT,
                                      font=(theme.FONT, 9), wraplength=320, justify='left')
        self.lbl_prev_info.pack(anchor='w', padx=12, pady=(0, 10))
        ttk.Button(card, text=T('打开该目录'), command=self.open_dir).pack(
            padx=12, pady=(0, 6), fill='x')
        ttk.Button(card, text=T('打开识别报告'), command=self.open_report).pack(
            padx=12, pady=(0, 6), fill='x')
        ttk.Button(card, text=T('打开改名台账'), command=self.open_ledger).pack(
            padx=12, pady=(0, 10), fill='x')

    def _build_settings(self, parent):
        card = self._card(parent)
        card.pack(fill='x', pady=(10, 0))
        head = tk.Frame(card, bg=theme.C_CARD)
        head.pack(fill='x', padx=theme.S4, pady=(theme.S2 + 1, theme.S1))
        tk.Label(head, text=T('⚙ 设置'), bg=theme.C_CARD, fg=theme.C_SUB,
                 font=(theme.FONT, 10, 'bold')).pack(side='left')

        body = tk.Frame(card, bg=theme.C_CARD)
        body.pack(fill='x', padx=theme.S4, pady=(0, 10))

        # ① 抽帧数
        self.var_frames = tk.StringVar(value=T('自动'))
        self.cmb_frames = ttk.Combobox(self._set_row(body, T('抽帧数')),
                                       textvariable=self.var_frames,
                                       width=theme.button_width(7, 9), state='readonly',
                                       values=[T('自动'), '2', '4', '6', '8'])
        self.cmb_frames.pack(side='right')

        # ② 最多处理个数
        r = self._set_row(body, T('最多处理个数'))
        tk.Label(r, text=T('个（0=全部）'), bg=theme.C_CARD, fg=theme.C_HINT,
                 font=(theme.FONT, 8)).pack(side='right')
        self.var_limit = tk.StringVar(value='0')
        self.sp_limit = ttk.Spinbox(r, from_=0, to=100000,
                                    textvariable=self.var_limit, width=theme.button_width(6, 7))
        self.sp_limit.pack(side='right', padx=(0, 5))

        # ③ 重新处理已改过名的
        self.var_all = tk.BooleanVar(value=False)
        self.cb_all = ttk.Checkbutton(body, text=T('重新处理已改过名的'),
                                      variable=self.var_all)
        self.cb_all.pack(fill='x', anchor='w', pady=(0, 7))

        # ④ 模型（下拉 + 模型管理）
        self.var_model = tk.StringVar(value=engine.selected_model())
        self.cb_model = ttk.Combobox(self._set_row(body, T('模型')),
                                     textvariable=self.var_model,
                                     width=theme.button_width(20, 21), state='readonly',
                                     values=[item['name'] for item in engine.MODEL_CATALOG])
        self.cb_model.pack(side='right')
        self.cb_model.bind('<<ComboboxSelected>>', lambda e: self.choose_model(self.var_model.get()))
        self.btn_model = ttk.Button(body, text=T('模型管理…'),
                                    command=self.open_model_dialog)
        self.btn_model.pack(fill='x', pady=(0, 7))

        # ⑤ 照片也写内容描述 —— 已移到「处理范围」行（跟视频/照片并列）

        # ⑥ 联网精确定位（默认关：关着就是纯离线，一个网络包都不发）
        ttk.Separator(body, orient='horizontal').pack(fill='x', pady=(2, 8))
        self.var_netgeo = tk.BooleanVar(value=self._sw('net_geo', False))
        self.cb_netgeo = ttk.Checkbutton(body, text=T('联网精确定位'),
                                         variable=self.var_netgeo,
                                         command=self._on_netgeo)
        self.cb_netgeo.pack(fill='x', anchor='w')
        self.lbl_netgeo = tk.Label(
            body, bg=theme.C_CARD, fg=theme.C_SUB, font=(theme.FONT, 8), anchor='w', justify='left',
            wraplength=250,
            text=T('开启后将在有网时调用在线地图接口获取更精确地址，会产生网络请求'))
        self.lbl_netgeo.pack(fill='x', pady=(1, 7))

        r = self._set_row(body, T('高德 API Key'))
        self.var_amap = tk.StringVar(
            value=str((engine.config() or {}).get('amap_key') or ''))
        self.ent_amap = ttk.Entry(r, textvariable=self.var_amap, width=theme.button_width(17, 19))
        self.ent_amap.pack(side='right')
        self.var_amap.trace_add('write', lambda *a: self._save_amap())
        self.lbl_amap = tk.Label(
            body, bg=theme.C_CARD, fg=theme.C_SUB, font=(theme.FONT, 8), anchor='w', justify='left',
            wraplength=250,
            text=T('国内坐标用它查街道级地址，去 lbs.amap.com 免费申请；不填就一直走离线库'))
        self.lbl_amap.pack(fill='x', pady=(1, 7))

        self.var_geolv = tk.StringVar(
            value=T('到街道') if (engine.config() or {}).get('geo_level') == 'street'
            else T('到区县'))
        self.cmb_geolv = ttk.Combobox(
            self._set_row(body, T('地址粒度')), textvariable=self.var_geolv,
            width=theme.button_width(8, 10), state='readonly',
            values=[T('到区县'), T('到街道')])
        self.cmb_geolv.pack(side='right')
        self.cmb_geolv.bind('<<ComboboxSelected>>', self._on_geolv)

        # ⑦ 描述落盘（三个独立开关，默认全开）
        ttk.Separator(body, orient='horizontal').pack(fill='x', pady=(2, 8))
        self.var_meta = tk.BooleanVar(value=metadata_writing_enabled() and self._sw('write_meta', True))
        self.cb_meta = ttk.Checkbutton(body, text=T('写入文件元数据'),
                                       variable=self.var_meta,
                                       command=lambda: self._save_sw('write_meta', self.var_meta))
        if not metadata_writing_enabled():
            self.cb_meta.state(['disabled'])
        self.cb_meta.pack(fill='x', anchor='w')
        self.var_txt = tk.BooleanVar(value=self._sw('write_txt', True))
        ttk.Checkbutton(body, text=T('生成 .ai.txt 摘要'),
                        variable=self.var_txt,
                        command=lambda: self._save_sw('write_txt', self.var_txt)
                        ).pack(fill='x', anchor='w')
        self.var_md = tk.BooleanVar(value=self._sw('write_md', True))
        ttk.Checkbutton(body, text=T('生成 目录说明.md'),
                        variable=self.var_md,
                        command=lambda: self._save_sw('write_md', self.var_md)
                        ).pack(fill='x', anchor='w')
        tk.Label(body, bg=theme.C_CARD, fg=theme.C_SUB, font=(theme.FONT, 8),
                 anchor='w', justify='left', wraplength=250,
                 text=T('元数据写进文件本身（照片 EXIF/XMP、视频容器标签），'
                        '拷贝到任何地方都带着走；三个开关各管一件事，关掉就不生成。')
                 ).pack(fill='x', pady=(1, 7))

        # ⑧ 语言
        self.var_lang = tk.StringVar(
            value=T('中文') if strings.lang() == 'zh' else 'English')
        self.cmb_lang = ttk.Combobox(self._set_row(body, T('语言')),
                                     textvariable=self.var_lang,
                                     width=theme.button_width(8, 10), state='readonly',
                                     values=[T('中文'), 'English'])
        self.cmb_lang.pack(side='right')
        self.cmb_lang.bind('<<ComboboxSelected>>', self._on_lang_pick)

        # 处理范围行的耗时说明原来挤在勾选框右边，v3.18 起放到设置区底部当脚注：
        # 状态栏已经塞了模型圆点/端口/模型库路径，底部更挤，而这里本来就有空。
        # 文字会跟着勾选变化（_refresh_scope），所以用 wraplength 允许折行。
        self.lbl_scope = tk.Label(body, text='', bg=theme.C_CARD, fg=theme.C_SUB,
                                  font=(theme.FONT, 8), anchor='w', justify='left',
                                  wraplength=250)
        self.lbl_scope.pack(fill='x', pady=(4, 0))

        # ↑↑ 以后要加新开关，就往这行上面接着塞一行（放不下会自动出滚动条）

    # ------------------------------------------------------------ 新增开关的落盘
    def _save_sw(self, key, var):
        """布尔开关：勾选/取消都立刻写进 config.json（下次打开还是这个状态）。"""
        if key == 'write_meta' and not metadata_writing_enabled():
            var.set(False)
            return
        try:
            engine.save_config(**{key: bool(var.get())})
        except Exception:
            pass

    def _save_amap(self):
        try:
            engine.save_config(amap_key=str(self.var_amap.get() or '').strip())
        except Exception:
            pass

    def _on_geolv(self, _e=None):
        try:
            engine.save_config(
                geo_level=('street' if self.var_geolv.get() == T('到街道') else 'district'))
        except Exception:
            pass

    def _on_netgeo(self):
        self._save_sw('net_geo', self.var_netgeo)
        if self.var_netgeo.get() and not (self.var_amap.get() or '').strip():
            self.log(T('联网精确定位已开启，但还没填高德 API Key：'
                       '国内坐标会继续用离线库，国外坐标走 Nominatim。'), 'dim')
        self._refresh_scope()

    def _on_ai_desc(self):
        """「AI描述」总开关：写盘，并让子选项跟着可用/置灰。"""
        self._save_sw('ai_desc', self.var_ai_desc)
        # 重新打开时恢复子选项的默认状态（勾上），否则关一次就再也回不来
        if self.var_ai_desc.get() and self.var_photos.get():
            self.var_desc_photo.set(True)
        self._refresh_scope()

        # ↑↑ 以后要加新开关，就往这行上面接着塞一行（放不下会自动出滚动条）

    @staticmethod
    def _set_row(parent, label):
        """设置区的一行：左边标签，控件自己 pack 到右边。"""
        r = tk.Frame(parent, bg=theme.C_CARD)
        r.pack(fill='x', pady=(0, 7))
        tk.Label(r, text=label, bg=theme.C_CARD, fg=theme.C_TEXT,
                 font=(theme.FONT, 10)).pack(side='left')
        return r

    # ------------------------------------------------------------ 底部状态栏
    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=theme.C_BAR)
        bar.pack(side='bottom', fill='x')

        # ● 模型状态：点击可提前加载选中的本地模型。
        self.dot_srv = tk.Label(bar, text=T('● 模型未启动'), bg=theme.C_BAR, fg=theme.C_HINT,
                                font=(theme.FONT, 9), cursor='hand2')
        self.dot_srv.pack(side='left', padx=(14, 0), pady=5)
        self.dot_srv.bind('<Button-1>', lambda e: self.on_start_srv())
        self._srv_hint = T('点这里加载所选模型')
        Hover(self.dot_srv, lambda: self._srv_hint)

        ttk.Separator(bar, orient='vertical',
                      style='Bar.TSeparator').pack(side='left', fill='y',
                                                   padx=10, pady=4)
        self.lbl_port = tk.Label(bar, text='', bg=theme.C_BAR, fg=theme.C_SUB, font=(theme.FONT, 9))
        self.lbl_port.pack(side='left', pady=5)
        self._port_hint = ''
        Hover(self.lbl_port, lambda: self._port_hint)

        ttk.Separator(bar, orient='vertical',
                      style='Bar.TSeparator').pack(side='left', fill='y',
                                                   padx=10, pady=4)
        self.lbl_models = tk.Label(bar, text='', bg=theme.C_BAR, fg=theme.C_SUB,
                                   font=(theme.FONT, 9))
        self.lbl_models.pack(side='left', pady=5)
        self._models_full = ''
        Hover(self.lbl_models, lambda: self._models_full)
        # 设备和目录无需等待后台初始化。
        try:
            self.lbl_port.configure(text='llama.cpp · CPU')
            md = engine.models_dir()
            self.lbl_models.configure(text=T('模型库 %s') % cut(md, 34))
            self._models_full = md
        except Exception:
            pass

        # ⋯ 更多：核对改名 / 回滚 / 打开目录 / 打开报告 / 打开台账 / 模型管理 / 日志
        self.btn_more = tk.Menubutton(bar, text=T('⋯ 更多'), relief='flat',
                                      bg=theme.C_BAR, fg=theme.C_SUB, font=(theme.FONT, 9),
                                      padx=10, pady=2, direction='above',
                                      activebackground=theme.C_BTN_H,
                                      activeforeground=theme.C_TEXT)
        self.menu_more = tk.Menu(self.btn_more, tearoff=0, bg=theme.C_CARD, fg=theme.C_TEXT,
                                 activebackground=theme.C_ACCENT_L,
                                 activeforeground=theme.C_TEXT, borderwidth=1,
                                 relief='solid', font=(theme.FONT, 9))
        self.btn_more.configure(menu=self.menu_more)
        self.btn_more.pack(side='right', padx=(0, 12), pady=3)
        self._mi = {}
        self._mi['plan'] = self._mi_add(T('核对并改名…'), self.open_plan)
        self._mi['rollback'] = self._mi_add(T('回滚本目录改名'), self.on_rollback)
        self._mi_add(None, None)
        self._mi_add(T('打开该目录'), self.open_dir)
        self._mi_add(T('打开识别报告'), self.open_report)
        self._mi_add(T('打开改名台账'), self.open_ledger)
        self._mi_add(None, None)
        self._mi_add(T('模型管理…'), self.open_model_dialog)
        self._mi_add(None, None)
        self._mi_add(T('关于'), self.open_about)
        self._mi['log'] = self._mi_add('', self.toggle_log)
        self._sync_more_menu()

    def _mi_add(self, label, cmd) -> int:
        if label is None:
            self.menu_more.add_separator()
        else:
            self.menu_more.add_command(label=label, command=cmd)
        return max(0, self.menu_more.index('end'))

    def _mi_cfg(self, key, **kw):
        i = self._mi.get(key)
        if i is None:
            return
        try:
            self.menu_more.entryconfigure(i, **kw)
        except Exception:
            pass

    def _sync_more_menu(self):
        """菜单里跟着状态变的项：日志展开/收起、危险项禁用。"""
        if not hasattr(self, '_mi'):        # 状态栏比日志抽屉晚建，那时还没有菜单
            return
        opened = bool(getattr(self, 'var_log', None) and self.var_log.get())
        self._mi_cfg('log', label=(T('隐藏运行日志') if opened else T('显示运行日志')))

    # ------------------------------------------------------------ 日志
    def log(self, s, tag=None):
        ts = time.strftime('%H:%M:%S')
        self.txt.insert('end', '[%s] %s\n' % (ts, s), tag or ())
        self.txt.see('end')
        # 报错了就把日志抽屉弹开——收起状态下用户会看不到失败原因
        if tag == 'err' and not self.var_log.get() and not getattr(self, '_no_autolog', False):
            try:
                self.root.after(60, lambda: self._log_apply(True))
            except Exception:
                pass
        # 控制台也留一份，便于排障
        try:
            print(s, flush=True)
        except Exception:
            pass

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.txt.get('1.0', 'end'))

    # ------------------------------------------------------------ 交互
    def pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.var_dir.get() or 'D:\\')
        if d:
            self.var_dir.set(os.path.normpath(d))
            self._refresh_scope()

    def _on_preset_pick(self, *a):
        """预设下拉选中：把显示名映射回 engine.PRESETS 的原始键。"""
        key = self._preset_disp.get(self.var_preset.get())
        if key:
            self.var_dir.set(engine.PRESETS[key])

    def _on_lang_pick(self, *a):
        """语言下拉：切换即存 config.json，提示重启后生效。"""
        want = 'zh' if self.var_lang.get() == T('中文') else 'en'
        if want == strings.lang():
            return
        strings.set_lang(want)
        try:
            engine.save_config(lang=want)
        except Exception:
            pass
        messagebox.showinfo(T('语言'), T('语言已切换，重启软件后生效。'))
    # ------------------------------------------------------------ 忙时锁控件
    def _set_busy(self, busy: bool):
        """识别过程中锁住目录 / 范围 / 参数，免得中途改了和结果对不上号。

        菜单里的「回滚本目录改名」也要一并锁：它还原的是磁盘上的文件，
        半路回滚会和正在写的计划表打架。
        """
        spec = ['disabled'] if busy else ['!disabled']
        for w in getattr(self, '_busy_widgets', []):
            try:
                w.state(spec)
            except Exception:
                pass
        self._mi_cfg('rollback', state=('disabled' if busy else 'normal'))

    # ------------------------------------------------------------ 运行状态
    def _set_run_state(self, s, **kw):
        """把「现在到底在干嘛」写清楚：按钮文字 + 状态灯 + 停止按钮的显隐。

        「停止识别」平时不占位（主界面只有一个主要动作），只有真在跑的时候才
        pack 出来；停掉或跑完自动收回。
        """
        self.state = s
        go, stop = self.btn_go, self.btn_stop
        if s == 'idle':
            go.configure(text=T('▶ 开始识别'), state='normal')
            self._show_stop(False)
            self.lbl_state.configure(text=T('● 未开始'), fg=theme.C_SUB)
        elif s == 'running':
            go.configure(text=T('识别中…'), state='disabled')
            self._show_stop(True)
            stop.configure(text=T('停止识别'), state='normal')
            self.lbl_state.configure(text=T('● 识别中…'), fg=theme.C_ACCENT)
        elif s == 'stopping':
            go.configure(text=T('正在停止…'), state='disabled')
            self._show_stop(True)
            stop.configure(text=T('停止中…'), state='disabled')
            self.lbl_state.configure(text=T('● 正在停止…'), fg=theme.C_WARN)
        elif s == 'stopped':
            go.configure(text=T('▶ 重新开始识别'), state='normal')
            stop.configure(text=T('停止识别'), state='disabled')
            self._show_stop(False)
            self.lbl_state.configure(text=T('● 已手动停止 · 完成 %d/%d')
                                     % (kw.get('i', self.done), kw.get('n', self.total)),
                                     fg=theme.C_WARN)
        elif s == 'done':
            go.configure(text=T('↻ 再识别一次'), state='normal')
            stop.configure(text=T('停止识别'), state='disabled')
            self._show_stop(False)
            tail = (T(' · 用时 %s') % fmt_dur(kw['el'])) if kw.get('el') else ''
            self.lbl_state.configure(text=T('● 识别完成 · %d 条%s')
                                     % (kw.get('n', len(self._plan)), tail), fg=theme.C_OK)

    def _show_stop(self, show: bool):
        """「停止识别」按钮的显隐（平时不占位置）。

        用自己的标记而不是 winfo_ismapped()：窗口没经过 update 之前（比如自检里
        root 还是 withdrawn 状态）ismapped 一律返回 0，判断不出来。
        """
        show = bool(show)
        if getattr(self, '_stop_shown', None) == show:
            return
        try:
            if show:
                self.btn_stop.pack(side='left', padx=(10, 0))
            else:
                self.btn_stop.pack_forget()
            self._stop_shown = show
        except Exception:
            pass

    def _refresh_plan_btn(self):
        """目录里已经有计划表时，菜单里的「核对并改名…」才可点。"""
        if self.running:
            self._mi_cfg('plan', state='disabled')
            return
        d = os.path.abspath(self.var_dir.get().strip())
        try:
            has = bool(os.path.isdir(d) and engine.load_plan(d))
        except Exception:
            has = False
        self._mi_cfg('plan', state=('normal' if has else 'disabled'))

    def _on_dir_change(self):
        if self.running:
            return
        self._set_run_state('idle')
        if self._plan_job:
            try:
                self.root.after_cancel(self._plan_job)
            except Exception:
                pass
        self._plan_job = self.root.after(400, self._refresh_plan_btn)

    # ------------------------------------------------------------ 处理范围
    def _refresh_scope(self):
        """把「这次到底会处理什么」清楚地写在界面上。"""
        v, p = bool(self.var_video.get()), bool(self.var_photos.get())
        ai = bool(getattr(self, 'var_ai_desc', None) and self.var_ai_desc.get())
        # 「照片也写内容描述」是「AI描述」的子选项：总开关关着就置灰并清掉勾选
        if p and ai:
            self.cb_desc_photo.state(['!disabled'])
        else:
            self.cb_desc_photo.state(['disabled'])
            self.var_desc_photo.set(False)
        if not v and not p:
            self.lbl_scope.configure(text=T('两个都没勾选 · 不会处理任何文件'), fg=theme.C_ERR)
            return
        bits = []
        if v:
            bits.append(T('视频：抽帧送模型，约 11 秒/条') if ai
                        else T('视频：只读拍摄时间和 GPS，秒级'))
        if p:
            bits.append(T('照片：写内容描述，约 11 秒/张') if (ai and self.var_desc_photo.get())
                        else T('照片：只读拍摄时间和 GPS，秒级'))
        if not ai:
            bits.append(T('纯离线，不调用模型'))
        # 开了落盘，模型会多写一段 100~200 字的长描述，每条多花几秒，先说清楚
        # （设置区比这里晚建，首次调用时这些变量可能还不存在）
        g = lambda k: bool(getattr(self, k, None) and getattr(self, k).get())
        if g('var_meta') or g('var_txt') or g('var_md'):
            bits.append(T('并写入元数据/摘要，每条多几秒'))
        if g('var_netgeo'):
            bits.append(T('联网精确定位：开'))
        self.lbl_scope.configure(text='　·　'.join(bits), fg=theme.C_SUB)

    def open_plan(self, auto=False):
        """打开「核对并改名」窗口（时间/地点/描述都能手改）。"""
        d = os.path.abspath(self.var_dir.get().strip())
        if not os.path.isdir(d):
            messagebox.showerror(APP_TITLE, T('目录不存在：\n%s') % d)
            return
        items = engine.load_plan(d)
        if not items:
            if not auto:
                messagebox.showinfo(
                    APP_TITLE,
                    T('还没有识别结果。先点「开始识别」，跑完会自动打开这个窗口。'))
            return
        dlg = PlanDialog(self.root, d, items)
        self.root.wait_window(dlg)      # 注意：PlanDialog 本身就是 Toplevel，没有 .top
        if dlg.applied:
            done, failed, skipped = dlg.applied
            self.log(T('已按核对后的表格改名：成功 %d 个，跳过 %d 个（时间空白），失败 %d 个')
                     % (done, skipped, failed), 'ok')
            if failed:
                self.log(T('失败的文件保持原名，可在表格里改掉后重试'), 'dim')
            # 改名后目录说明.md 里的文件名全变了：拿新名字重写一份
            # （元数据和 .ai.txt 写在文件本身/跟着文件走，不用重做）
            try:
                if bool((engine.config() or {}).get('write_md', True)):
                    engine.finalize(d, engine.load_plan(d), write_meta=False,
                                    write_txt=False, write_md=True)
            except Exception:
                pass
            self._reload_tree(d)
            self._refresh_plan_btn()
            RenameResultDialog(self.root, d, dlg.detail or {})

    def _reload_tree(self, d):
        """按计划表刷新结果列表。"""
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for i, it in enumerate(engine.load_plan(d), 1):
            st, stag = self._status_of(it)
            tags = ['odd'] if i % 2 else []
            if stag:
                tags.append(stag)
            self._rows[it.get('index', i)] = it
            self.tree.insert('', 'end', tags=tuple(tags), values=(
                it.get('index', i), it.get('old', ''),
                it.get('new', '') or T('（时间空白）'),
                it.get('place', '') or '—',
                it.get('desc', '') or '—',
                '%.1fs' % (it.get('secs') or 0), st))

    def open_dir(self):
        d = self.var_dir.get()
        if os.path.isdir(d):
            open_path(d)
        else:
            messagebox.showwarning(APP_TITLE, T('目录不存在'))

    def open_report(self):
        """识别阶段写的对照报告（改名结果不写在这里，改名看台账）。"""
        sp = engine.state_paths(self.var_dir.get())
        p = sp['report']
        if os.path.exists(p):
            open_path(p)
        else:
            open_path(sp['dir'])

    def open_ledger(self):
        """改名台账：新文件名 → 手机原始名，「回滚本目录改名」就是按它还原的。"""
        sp = engine.state_paths(self.var_dir.get())
        p = sp['ledger']
        if os.path.exists(p):
            open_path(p)
        else:
            messagebox.showinfo(APP_TITLE, T('这个目录还没有改名记录。\n' + '台账在「核对并改名」里点过「应用改名」之后才会生成。'))

    def on_start_srv(self):
        """底部状态圆点：在后台加载所选模型。"""
        name = self.var_model.get().strip() or engine.MODEL
        if not engine.model_exists(name):
            self.open_model_dialog()
            return

        def load():
            if not engine.start_server():
                raise RuntimeError(engine.runtime_status()['error'])
            ok, error = engine.warmup(name)
            if not ok:
                raise RuntimeError(error)

        self.run_model_action(load, T('模型已加载 · %s') % name)

    def run_model_action(self, action, message):
        if self.running or self._model_busy:
            return False
        self._model_busy = True
        self.cb_model.configure(state='disabled')
        self.dot_srv.configure(text=T('● 模型操作中…'), fg=theme.C_ACCENT)

        def work():
            try:
                action()
                self.q.put(('model_done', (True, message), None))
            except Exception as error:
                self.q.put(('model_done', (False, str(error)), None))

        threading.Thread(target=work, daemon=True).start()
        return True

    def choose_model(self, name):
        if self.running or self._model_busy:
            return False
        self.var_model.set(name)
        return self.run_model_action(lambda: engine.select_model(name), T('已选用模型：%s') % name)

    def open_about(self):
        for w in self.root.winfo_children():
            if isinstance(w, AboutDialog) and w.winfo_exists():
                w.lift()
                w.focus_set()
                return
        AboutDialog(self.root, self)

    def open_help(self):
        for w in self.root.winfo_children():
            if isinstance(w, HelpDialog) and w.winfo_exists():
                w.lift()
                w.focus_set()
                return
        HelpDialog(self.root, self)

    def _maybe_help(self):
        """首次启动自动弹一次说明；用户勾了「下次不再显示」就写进 config。"""
        try:
            if not engine.installed_models():
                messagebox.showinfo(APP_TITLE, T('未找到本地模型，请在模型管理中下载 4B / 2B 模型，或选择已有模型目录。'))
                self.open_model_dialog()
                return
            if self._sw('help_autoshow', True):
                self.open_help()
        except Exception:
            pass

    def open_model_dialog(self):
        for w in self.root.winfo_children():
            if isinstance(w, ModelDialog) and w.winfo_exists():
                w.lift()
                w.focus_set()
                return
        ModelDialog(self.root, self)

    def open_acceleration(self):
        for window in self.root.winfo_children():
            if isinstance(window, AccelerationDialog):
                window.lift()
                return
        AccelerationDialog(self.root, self)

    def on_rollback(self):
        d = os.path.abspath(self.var_dir.get())
        if not os.path.isdir(d):
            messagebox.showwarning(APP_TITLE, T('目录不存在'))
            return
        if not messagebox.askyesno(APP_TITLE, T('按台账把 %s 里已改名的文件还原为原名？') % d):
            return
        n = engine.rollback(d, on_log=lambda s: self.log(s, 'dim'))
        # 台账清了，计划表里的「已改名」标记也要跟着清，否则状态列会继续显示已改名
        try:
            items = engine.load_plan(d)
            for it in items:
                it['applied'] = False
                it['err'] = ''
            if items:
                engine.save_plan(d, items)
                # 文件名都回到原名了，目录说明.md 也按原名重写一份
                if bool((engine.config() or {}).get('write_md', True)):
                    engine.finalize(d, items, write_meta=False,
                                    write_txt=False, write_md=True)
        except Exception:
            pass
        self._reload_tree(d)
        messagebox.showinfo(APP_TITLE, T('已还原 %d 个文件') % n)
    def on_go(self):
        if self.running or self._model_busy:
            return
        d = os.path.abspath(self.var_dir.get().strip())
        if not os.path.isdir(d):
            messagebox.showerror(APP_TITLE, T('目录不存在：\n%s') % d)
            return
        videos = bool(self.var_video.get())
        photos = bool(self.var_photos.get())
        if not videos and not photos:
            messagebox.showwarning(APP_TITLE, T('请先勾选要处理的内容：视频、照片，或两个都勾。'))
            return
        n_vid = len(engine.list_videos(d)) if videos else 0
        n_pic = len(engine.list_photos(d)) if photos else 0
        if n_vid == 0 and n_pic == 0:
            messagebox.showwarning(
                APP_TITLE, T('该目录下没找到%s')
                % (T('视频或照片') if videos and photos
                   else (T('视频') if videos else T('照片'))))
            return
        model = self.var_model.get().strip() or engine.MODEL
        # AI 描述关着时一次模型调用都没有，自然也不要求先下载模型
        ai_desc = bool(self.var_ai_desc.get())
        need_model = ai_desc and (n_vid > 0 or (photos and bool(self.var_desc_photo.get())))
        if need_model and not engine.model_exists(model):
            if messagebox.askyesno(
                    APP_TITLE,
                    T('模型「%s」缺少配套文件。\n\n现在打开「模型管理」下载模型或选择本地目录吗？') % model):
                self.open_model_dialog()
            return

        frames = 0 if self.var_frames.get() == T('自动') else int(self.var_frames.get())
        try:
            limit = int(self.var_limit.get() or 0)
        except ValueError:
            limit = 0

        cfg = engine.Config(
            d, apply=False, frames=frames, limit=limit,
            all_files=self.var_all.get(), model=model,
            videos=videos, photos=photos,
            describe_photos=bool(self.var_desc_photo.get()),
            net_geo=bool(self.var_netgeo.get()),
            amap_key=str(self.var_amap.get() or '').strip(),
            geo_level=('street' if self.var_geolv.get() == T('到街道') else 'district'),
            write_meta=bool(self.var_meta.get()),
            write_txt=bool(self.var_txt.get()),
            write_md=bool(self.var_md.get()),
            ai_desc=ai_desc)

        self._plan = []
        self.tree.delete(*self.tree.get_children())
        self.total, self.done = 0, 0
        self.t_start = time.time()
        self.stop_evt.clear()
        self.running = True
        self._rows.clear()
        self._thumbs.clear()
        self._photo_thumbs.clear()   # 文件马上要改名了，旧缩略图全部作废
        self._set_run_state('running')
        self._mi_cfg('plan', state='disabled')
        self._set_busy(True)
        self.pb.configure(value=0, maximum=100)
        self.lbl_prog.configure(text=T('准备中…'))
        what = []
        if videos:
            what.append(T('视频 %d 个') % n_vid)
        if photos:
            what.append(T('照片 %d 张') % n_pic)
        self.log(T('——— 开始识别\u3000目录：%s\u3000%s ———') % (d, ' ／ '.join(what)), 'head')

        t = threading.Thread(target=self._work, args=(cfg,), daemon=True)
        self.worker = t
        t.start()

    def _work(self, cfg: engine.Config):
        try:
            res = engine.run_batch(
                cfg,
                on_log=lambda s: self.q.put(('log', s, None)),
                on_item=lambda r: self.q.put(('item', r, None)),
                on_progress=lambda i, n, s2: self.q.put(('prog', (i, n, s2), None)),
                should_stop=self.stop_evt.is_set,
                on_phase=lambda s: self.q.put(('phase', s, None)),
            )
            self._plan = res.get('plan') or []
        except Exception:
            self.q.put(('log', traceback.format_exc(), 'err'))
        finally:
            self.q.put(('end', None, None))

    def on_stop(self):
        if self.running:
            self.stop_evt.set()
            self._set_run_state('stopping')
            self.log(T('已请求停止，正在收尾…'), 'dim')

    # ------------------------------------------------------------ 队列轮询
    def _poll_server(self):
        threading.Thread(target=self._server_bg, daemon=True).start()
        self.root.after(5000, self._poll_server)

    def _server_bg(self):
        info = engine.server_info()
        models = engine.list_models()
        self.q.put(('server', (info, models, engine.runtime_status()), None))

    def _poll(self):
        """由 _tick 调用：消费队列 → 刷新界面。"""
        n = 0
        while n < 400:
            try:
                kind, payload, tag = self.q.get_nowait()
            except queue.Empty:
                break
            n += 1
            if kind == 'log':
                self.log(payload, tag)
            elif kind == 'phase':
                if payload != T('完成'):
                    self.lbl_prog.configure(text='%s…' % payload)
            elif kind == 'item':
                self.add_item(payload)
            elif kind == 'prog':
                self.update_prog(*payload)
            elif kind == 'server':
                self.update_server(*payload)
            elif kind == 'srv':
                pass
            elif kind == 'model_done':
                self._model_busy = False
                self.cb_model.configure(state='readonly')
                ok, message = payload
                self.log(message, 'ok' if ok else 'err')
                self.update_server(engine.server_info(), engine.list_models(), engine.runtime_status())
                for window in self.root.winfo_children():
                    if isinstance(window, (ModelDialog, AccelerationDialog)):
                        window.refresh()
            elif kind == 'end':
                self.finish()

    def _tick(self):
        try:
            self._poll()
        except Exception:
            self.log(traceback.format_exc(), 'err')
        self.root.after(120, self._tick)

    # ------------------------------------------------------------ 界面更新
    def update_server(self, info, models, rt=None):
        """Show readiness and loaded models without claiming file size is RAM/VRAM."""
        self._models = models or []
        rt = rt or engine.runtime_status()
        self.lbl_port.configure(text='%s · %s' % (rt['backend'], rt['device']))
        self._port_hint = rt.get('error') or rt.get('acceleration_error') or T('本地推理，不需要端口或服务进程')
        self.lbl_models.configure(text=T('模型库 %s') % cut(rt['models_dir'], 34))
        self._models_full = rt['models_dir']
        values = list(dict.fromkeys(models + [item['name'] for item in engine.MODEL_CATALOG]))
        self.cb_model.configure(values=values)
        dot = self.dot_srv
        self._srv_hint = rt.get('error') or T('点这里加载所选模型')
        if self._model_busy or rt.get('busy'):
            dot.configure(text=T('● 模型操作中…'), fg=theme.C_ACCENT, cursor='arrow')
        elif rt.get('error'):
            dot.configure(text=T('● 模型操作失败'), fg=theme.C_WARN, cursor='hand2')
        elif not info.get('up'):
            dot.configure(text=T('● 模型未初始化'), fg=theme.C_HINT, cursor='hand2')
        elif not engine.model_exists(self.var_model.get(), {name: 0 for name in models}):
            self._srv_hint = T('请选择配套的 GGUF 主模型与 mmproj 文件')
            dot.configure(text=T('● 模型文件不完整'), fg=theme.C_WARN, cursor='hand2')
        elif any(m['name'] == self.var_model.get() for m in info.get('models', [])):
            self._srv_hint = T('模型已加载 · %s') % self.var_model.get()
            dot.configure(text=T('● 模型已加载'), fg=theme.C_OK, cursor='hand2')
        else:
            self._srv_hint = T('识别时自动加载，也可点击提前加载')
            dot.configure(text=T('● 模型待加载'), fg=theme.C_HINT, cursor='hand2')

    def update_prog(self, i, total, stats):
        self.total, self.done = total, i
        pct = (i / total * 100) if total else 0
        self.pb.configure(value=pct)
        el = stats.get('elapsed', 0)
        avg = stats.get('avg', 0) or 0
        left = avg * max(0, total - i)
        self.lbl_prog.configure(
            text=T('已处理 %d/%d · 平均 %.1fs/条 · 已用 %s · 预计剩余 %s')
                 % (i, total, avg, fmt_dur(el), fmt_dur(left)))
        if self.state == 'running':
            self.btn_go.configure(text=T('识别中 %d/%d') % (i, total))
            self.lbl_state.configure(text=T('● 识别中 %d/%d') % (i, total), fg=theme.C_ACCENT)

    @staticmethod
    def _status_of(it):
        """一行现在处于什么状态（配合 tag 上色）。"""
        if not it.get('ok', True):
            return T('识别失败'), 'fail'
        if it.get('applied'):
            return T('已改名'), 'st_ok'
        if it.get('err'):
            return T('改名失败'), 'st_fail'
        if not str(it.get('time') or '').strip():
            return T('待补时间'), 'st_skip'
        if it.get('new') and it['new'] != it.get('old'):
            return T('待改名'), ''
        return T('无需改名'), 'st_skip'

    def add_item(self, r):
        idx = r.get('index', 0)
        # 七列要和表头一一对应：# 原文件名 新文件名 地名 描述 耗时 状态
        # （v3.3 拆列时这里少塞了一个值，导致「描述」列显示成耗时）
        # 存完整值（列宽固定，超出的部分由表格自己裁掉），鼠标停住能看到全名
        if not r.get('ok'):
            self.tree.insert('', 'end', tags=('fail',),
                             values=(idx, r.get('old', ''), T('（失败）'),
                                     r.get('err', ''), '—', '—', T('识别失败')))
            return
        st, stag = self._status_of(r)
        tags = ['odd'] if idx % 2 else []
        if stag:
            tags.append(stag)
        self.tree.insert('', 'end', tags=tuple(tags),
                         values=(idx, r.get('old', ''),
                                 r.get('new', '') or T('（时间空白）'),
                                 r.get('place', '') or '—',
                                 r.get('desc', '') or '—',
                                 '%.1fs' % (r.get('t_model') or 0), st))
        self._rows[idx] = r
        if r.get('thumb'):
            self._thumbs[idx] = r['thumb']
        self.tree.yview_moveto(1.0)
        self.show_thumb(r)

    def _info_text(self, r):
        """右侧详情文字：处理中和事后点行都用它。"""
        info = (T('%s\n→ %s\n\n地名：%s\n描述：%s\n')
                % (r.get('old', ''), r.get('new', '') or T('（时间空白，不改名）'),
                   r.get('place') or T('（无）'), r.get('desc') or T('（无）')))
        if r.get('kind') == 'photo':
            if r.get('frames'):
                # 照片只送原图 1 张（不抽帧、不拼宫格），别显示 0×0 宫格
                info += (T('\n原图 1 张 / %s / %d token')
                         % (r.get('how', ''), r.get('tokens', 0)))
            else:
                info += T('\n照片：只读拍摄时间与 GPS，不送模型')
        elif r.get('frames'):
            info += (T('\n%d 帧 / %s / 宫格 %d×%d / %d token')
                     % (r.get('frames', 0), r.get('how', ''),
                        r['grid'][0], r['grid'][1], r.get('tokens', 0)))
        if r.get('gps_src'):
            tag = {'gps': T('文件自带 GPS'),
                   'near': T('相邻文件（可标到镇）'),
                   'far': T('邻近文件（只能标到城市）')}.get(r.get('gps_level'), '')
            info += T('\n地名来源：%s%s') % (r['gps_src'], ('　' + tag) if tag else '')
        if r.get('err'):
            info += T('\n失败原因：%s') % r['err']
        return info

    def _show_photo_file(self, r, idx=None):
        """照片没有模型宫格：点行时直接读原图缩放来显示。

        12MP 的 JPEG 缩到 340 宽约 0.1–0.2 秒，只发生在点击那一刻，不影响识别速度。
        改名后旧文件名已不存在，所以先试新名再试旧名。
        """
        if not (_HAS_PIL and r.get('kind') == 'photo'):
            return False
        if idx is not None and idx in self._photo_thumbs:
            ph = self._photo_thumbs[idx]
            self._thumb_ref = ph
            self.lbl_prev.configure(image=ph, text='',
                                    width=ph.width(), height=ph.height())
            return True
        d = os.path.abspath(self.var_dir.get().strip())
        for name in (r.get('new'), r.get('old')):
            if not name:
                continue
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            try:
                with Image.open(p) as im:
                    # JPEG 解码阶段直接按 1/2、1/4… 降采样，比解码完再缩快好几倍
                    try:
                        im.draft('RGB', (700, 700))
                    except Exception:
                        pass
                    im = ImageOps.exif_transpose(im)     # 按拍摄方向摆正
                    w = _PREV_W
                    if im.width > w:
                        h = max(1, int(im.height * w / im.width))
                        im = im.resize((w, h), Image.Resampling.LANCZOS)
                    ph = ImageTk.PhotoImage(im.convert('RGB'))
                self._thumb_ref = ph
                self.lbl_prev.configure(image=ph, text='',
                                        width=ph.width(), height=ph.height())
                if idx is not None:
                    if len(self._photo_thumbs) > 60:     # 缓存上限，防吃内存
                        self._photo_thumbs.clear()
                    self._photo_thumbs[idx] = ph
                return True
            except Exception:
                return False
        return False

    def _show_photo(self, data):
        if not (_HAS_PIL and data):
            return False
        try:
            im = Image.open(io.BytesIO(data))
            w = _PREV_W
            h = max(1, int(im.height * w / im.width))
            im = im.resize((w, h), Image.Resampling.LANCZOS)
            self._thumb_ref = ImageTk.PhotoImage(im)
            self.lbl_prev.configure(image=self._thumb_ref, text='', width=w, height=h)
            return True
        except Exception:
            return False

    def show_thumb(self, r):
        if not self._show_photo(r.get('thumb')):
            return
        self.lbl_prev_info.configure(text=self._info_text(r))

    def on_pick_row(self, _evt=None):
        """点结果表里的一行 → 右侧显示它的完整信息（名字被截断时很有用）。"""
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], 'values')
        if not vals:
            return
        try:
            idx = int(str(vals[0]).strip())
        except (ValueError, TypeError):
            return
        r = self._rows.get(idx)
        if not r:
            self.lbl_prev_info.configure(
                text=T('%s\n→ %s\n\n（这一条是上次识别留下的，没有更多细节）')
                     % (vals[1], vals[2]))
            return
        if not self._show_photo(self._thumbs.get(idx)):
            if not self._show_photo_file(r, idx):
                self.lbl_prev.configure(image='', text=T('（没有可预览的画面）'),
                                        width=34, height=9)
                self._thumb_ref = None
        self.lbl_prev_info.configure(text=self._info_text(r))

    def finish(self):
        self.running = False
        self._set_busy(False)
        el = time.time() - self.t_start
        d = os.path.abspath(self.var_dir.get().strip())
        n = len(self._plan) if self._plan else len(engine.load_plan(d) or [])
        if self.stop_evt.is_set():          # 用户中途按了「停止识别」
            self._set_run_state('stopped', i=self.done, n=self.total)
            self.lbl_prog.configure(text=T('已停止 · 完成 %d/%d · 用时 %s')
                                    % (self.done, self.total, fmt_dur(el)))
            self.log(T('——— 已手动停止（已完成的 %d 条已写进计划表）———') % len(self._plan),
                     'head')
        else:
            self._set_run_state('done', n=n, el=el)
            if self.total:
                self.pb.configure(value=100)
            self.lbl_prog.configure(text=T('识别完成 · 共 %d 条 · 用时 %s') % (n, fmt_dur(el)))
            self.log(T('——— 识别结束 ———'), 'head')
        self._refresh_plan_btn()
        if not n:
            self.log(T('没有可改名的项目'), 'dim')
        elif not self.stop_evt.is_set():
            self.log(T('文件名还没有改动。正在打开「核对并改名」窗口…'), 'ok')
            self.root.after(300, lambda: self.open_plan(auto=True))
        else:
            self.log(T('想接着处理就点「核对并改名…」（只含已识别的部分），' + '或改完参数再「▶ 重新开始识别」'), 'dim')

    # ------------------------------------------------------------ 自检
    def selftest(self):
        self.log(T('自检：控件构建正常'), 'ok')
        try:
            _n1 = len(engine.load_districts())
            _n2 = len(engine.load_world())
            self.log(T('自检：地名库加载正常（国内县区 %d 条 / 全球城市 %d 条）')
                     % (_n1, _n2), 'ok' if _n1 and _n2 else 'warn')
        except Exception as _e:
            self.log(T('自检：地名库加载失败：%r') % (_e,), 'err')
        d = ModelDialog(self.root, self)
        self.root.update_idletasks()
        self.log(T('自检：模型管理对话框构建正常（列出 %d 个候选模型）')
                 % len(engine.MODEL_CATALOG), 'ok')
        d.on_close()
        hp = help_path()
        hd = HelpDialog(self.root, self)
        self.root.update_idletasks()
        self.log(T('自检：使用说明窗口构建正常（%d 段 · %s）')
                 % (hd.n_seg, os.path.basename(hp) if hp else T('（缺文件）')),
                 'ok' if hd.n_seg > 20 and hp else 'warn')
        hd.on_close()
        pd = PlanDialog(self.root, os.path.dirname(os.path.dirname(os.path.abspath(__file__))), [
            {'old': 'share_demo_0001.mp4', 'kind': 'video', 'time': '20251011_1430',
             'place': T('成都肖家河街道'), 'desc': T('洗车'), 'new': T('20251011_1430_成都肖家河街道_洗车.mp4'),
             'ok': True, 'index': 1, 'secs': 12.1},
            {'old': 'IMG20251011090000.jpg', 'kind': 'photo', 'time': '',
             'place': '', 'desc': '', 'new': '', 'ok': True, 'index': 2, 'secs': 0.0},
            {'old': 'share_demo_0002.mp4', 'kind': 'video', 'time': '20251012_0800',
             'place': '', 'desc': T('林间散步'), 'new': T('20251012_0800_林间散步.mp4'),
             'ok': True, 'index': 3, 'secs': 11.6},
        ])
        self.root.update_idletasks()
        self.log(T('自检：核对并改名窗口构建正常（%d 行）') % len(pd.rows), 'ok')
        rd = ReplaceDialog(pd, pd.rows)
        self.root.update_idletasks()
        self.log(T('自检：查找替换窗口构建正常'), 'ok')
        rd.on_close()
        pd.on_cancel()
        for s in ('idle', 'running', 'stopping', 'stopped', 'done'):
            self._set_run_state(s, i=3, n=10, el=65.0)
            self.root.update_idletasks()
        self._set_run_state('idle')
        self.log(T('自检：运行状态机（未开始/识别中/停止中/已停止/已完成）文案切换正常'), 'ok')
        rd = RenameResultDialog(self.root, os.path.dirname(os.path.dirname(os.path.abspath(__file__))), {
            'ok': [('share_demo_0001.mp4', T('视20251011成都肖家河_洗车.mp4'))],
            'fail': [('share_demo_0002.mp4', T('视20251011成都肖家河_洗车.mp4'),
                      T('目标文件名已存在'))],
            'skip': [('IMG20251011090000.jpg', '')],
            'counts': (1, 1, 1)})
        self.root.update_idletasks()
        self.log(T('自检：改名结果窗口构建正常'), 'ok')
        rd.on_close()
        self._refresh_plan_btn()
        # —— v3.22：AI 描述总开关 ——
        try:
            _ai0 = bool(self.var_ai_desc.get())
            self.var_ai_desc.set(False)
            self._refresh_scope()
            _off_grey = 'disabled' in self.cb_desc_photo.state()
            self.var_ai_desc.set(True)
            self._refresh_scope()
            _on_live = 'disabled' not in self.cb_desc_photo.state()
            self.var_ai_desc.set(_ai0)
            self._refresh_scope()
            self.log(T('自检：AI 描述开关构建正常'),
                     'ok' if (_off_grey and _on_live) else 'warn')
        except Exception as _e:
            self.log(T('自检：AI 描述开关异常：%r') % (_e,), 'err')
        # —— v3.20：联网精确定位 + 描述落盘 ——
        try:
            import geo
            _la, _lg = geo.wgs2gcj(30.66, 104.06)
            _okg = abs(_la - 30.6576) < 0.01 and abs(_lg - 104.0625) < 0.01
            self.log(T('自检：WGS-84→GCJ-02 坐标转换 %s（30.66,104.06 -> %.5f,%.5f）')
                     % (T('正常') if _okg else T('异常'), _la, _lg),
                     'ok' if _okg else 'err')
            self.log(T('    联网精确定位当前：%s（开关默认关闭，关闭时一个网络包都不发）')
                     % (T('开') if self.var_netgeo.get() else T('关')), 'dim')
        except Exception as _e:
            self.log(T('自检：联网定位模块异常：%r') % (_e,), 'err')
        try:
            import meta as _mdb
            import tempfile
            _td = tempfile.mkdtemp(prefix='seename_meta_')
            _p = os.path.join(_td, 'selftest.jpg')
            Image.new('RGB', (240, 160), (150, 170, 200)).save(_p, 'JPEG', quality=85)
            _ok, _msg = _mdb.write_meta(_p, '自检·标题', '自检长描述：这是一段用于回读校验的中文。',
                                        '自检,元数据')
            self.log(T('自检：JPEG 元数据写入 + 回读校验 %s（%s）')
                     % (T('正常') if _ok else T('失败'), _msg), 'ok' if _ok else 'err')
            _ok2, _m2 = _mdb.write_sidecar(_p, '成都郫都区', '自检长描述。')
            _ok3, _m3 = _mdb.write_dir_md(_td, [(os.path.basename(_p), '成都郫都区', '自检长描述。')])
            self.log(T('自检：.ai.txt / 目录说明.md 写入 %s')
                     % (T('正常') if (_ok2 and _ok3) else T('失败')),
                     'ok' if (_ok2 and _ok3) else 'err')
            shutil.rmtree(_td, ignore_errors=True)
        except Exception as _e:
            self.log(T('自检：元数据模块异常：%r') % (_e,), 'err')
        ad = AboutDialog(self.root, self)
        self.root.update_idletasks()
        self.log(T('自检：关于窗口构建正常（隐私文案随联网开关变化）'), 'ok')
        ad.destroy()
        # —— v3.16 布局：底部状态栏 / 侧栏折叠 / 日志抽屉 / ⋯更多 菜单 ——
        st = T('● 模型未初始化')
        self.dot_srv.configure(text=st, fg=theme.C_HINT)
        self.dot_srv.configure(text=T('● 模型已加载'), fg=theme.C_OK)
        self.update_server({'up': True, 'models': []}, [], engine.runtime_status())
        self.update_server({'up': False, 'models': []}, [], engine.runtime_status())
        self.root.update_idletasks()
        self.log(T('自检：本地模型状态与设备显示正常'), 'ok')
        _n = self.menu_more.index('end') + 1
        self.log(T('自检：⋯更多菜单构建正常（%d 项：核对改名/回滚/打开…/模型管理/日志）')
                 % _n, 'ok' if _n >= 8 else 'warn')
        self._mi_cfg('plan', state='normal')
        self._mi_cfg('plan', state='disabled')
        self._mi_cfg('rollback', state='normal')
        self._mi_cfg('rollback', state='disabled')
        self.root.update_idletasks()
        self.log(T('自检：⋯更多菜单项禁用/启用正常'), 'ok')
        for op in (True, False, True):
            self._side_apply(op)
            self.root.update_idletasks()
        self.log(T('自检：右侧栏展开/收起正常（收起只剩 ⚙ 按钮）'), 'ok')
        for op in (True, False, True):
            self._log_apply(op)
            self.root.update_idletasks()
        self._log_apply(False)
        self.root.update_idletasks()
        self.log(T('自检：运行日志抽屉展开/收起正常'), 'ok')
        # 「停止识别」平时藏起来，跑起来才出现——这是本次布局的核心改动
        _seen = []
        for s in ('idle', 'running', 'stopping', 'stopped', 'done'):
            self._set_run_state(s, i=3, n=10, el=65.0)
            self.root.update_idletasks()
            _seen.append(bool(getattr(self, '_stop_shown', False)))
        self._set_run_state('idle')
        _want = [False, True, True, False, False]
        self.log(T('自检：停止按钮显隐（未开始/识别中/停止中/已停止/已完成 = %s）')
                 % _seen, 'ok' if _seen == _want else 'err')
        self.log(T('自检：手工时间解析 %r -> %s')
                 % ('20251011_1430', engine.parse_manual_time('20251011_1430')), 'dim')
        try:
            self.log('ffmpeg = %s' % engine.FF(), 'dim')
        except RuntimeError as error:
            self.log(str(error), 'warn')
        rs = engine.runtime_status()
        self.log(T('=== 推理后端：%s · %s') % (rs['backend'], rs['device']), 'dim')
        self.log(T('模型库 = %s') % engine.models_dir(), 'dim')
        self.log(T('预设目录：%s') % engine.PRESETS, 'dim')
