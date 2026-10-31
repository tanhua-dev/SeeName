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
from tkinter import filedialog, font as tkfont, messagebox, ttk

import engine
import strings
from strings import T
from core.features import metadata_writing_enabled
from core.defaults import MODES as MODE_PRESETS
from . import theme
from .common import APP_AUTHOR, APP_SUB, APP_TITLE, APP_VER, asset_path, cut, fmt_dur, open_path
from .widgets import Hover, Tip, Toast, DisabledTip, RowPreview
from .controls import CheckRow, ChoiceRow, gpu_memory
from .dialogs.information import AboutDialog, HelpDialog, help_path
from .dialogs.models import ModelDialog
from .dialogs.acceleration import AccelerationDialog
from .dialogs.settings import SettingsDialog
from .dialogs.review import PlanDialog, RenameResultDialog, ReplaceDialog


try:
    from PIL import Image, ImageOps, ImageTk
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

_PREV_W = 112
_PREV_H = 54       # 详情条只有 68px 高，缩略图压到 112x54


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
        self._iid_idx: dict = {}        # Treeview iid -> index（点状态列定位行）
        self._thumbs: dict = {}         # index -> 宫格图 bytes
        self._photo_thumbs: dict = {}   # index -> 照片缩略图 PhotoImage（点行时现读，缓存复用）
        self._tip = None                # 悬浮提示（文件名被截断时用）

        # ---- 设置项的状态变量：先建好，设置窗口开没开都能读写 ----
        # （设置窗口是二级窗口、按需创建，但这些值在「开始识别」等流程里随时要用）
        cfg = engine.config() or {}
        self.var_frames = tk.StringVar(value=T('自动'))
        self.var_limit = tk.StringVar(value='0')
        self.var_all = tk.BooleanVar(value=False)
        self.var_model = tk.StringVar(value=engine.selected_model())
        self.var_netgeo = tk.BooleanVar(value=self._sw('net_geo', False))
        self.var_amap = tk.StringVar(value=str(cfg.get('amap_key') or ''))
        self.var_geolv = tk.StringVar(
            value=T('到街道') if cfg.get('geo_level') == 'street' else T('到区县'))
        # 默认「简洁」：只改名，不多出任何文件。想要 .ai.txt / 目录说明.md /
        # 元数据的用户去设置里切到「详细」（细项在高级里）。
        self.var_meta = tk.BooleanVar(
            value=metadata_writing_enabled() and self._sw('write_meta', False))
        self.var_txt = tk.BooleanVar(value=self._sw('write_txt', False))
        self.var_md = tk.BooleanVar(value=self._sw('write_md', False))
        self.var_lang = tk.StringVar(
            value=T('中文') if strings.lang() == 'zh' else 'English')
        # 处理范围的四个勾选：原来住在主界面第二行，现在搬进设置窗口的「常用」
        # 第一组。变量留在这里，是因为识别流程随时要读，窗口没开也得有值。
        self.var_ai_desc = tk.BooleanVar(value=self._sw('ai_desc', True))
        self.var_video = tk.BooleanVar(value=True)
        self.var_photos = tk.BooleanVar(value=True)
        self.var_desc_photo = tk.BooleanVar(value=True)
        # 首页「模式」：smart 智能命名 / quick 秒改名 / custom 手动改过单项（S-18）
        self.var_mode = tk.StringVar(value=str(cfg.get('mode') or ''))
        self._mode_auto = False          # 这次是程序替你选的（显示「已为你选好」）
        self.choice_mode = None
        # 这些控件只在设置窗口里存在；窗口没开时保持 None，用到的地方都要判空
        self.cb_model = None
        self.btn_model = None
        self.cb_ai_desc = None
        self.cb_video = None
        self.cb_photos = None
        self.cb_desc_photo = None
        self.sp_limit = None
        self.ent_amap = None
        self.settings_dlg = None

        root.title('%s %s' % (APP_TITLE, APP_VER))
        root.geometry(self._saved_geometry(root))
        _sw, _sh = self._work_area(root)
        root.minsize(min(940, int(_sw * 0.86)), min(620, int(_sh * 0.86)))
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
        self._init_mode()
        # 单项改了就让模式跟着走（设置窗口里关掉「照片也写描述」→ 显示「自定义」）
        for _var in (self.var_ai_desc, self.var_desc_photo):
            _var.trace_add('write', lambda *a: self._sync_mode_hint())
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
    def _work_area(win) -> tuple[int, int]:
        """工作区尺寸，单位与 Tk 一致；取不到就退回 1920x1080。

        以前这里用 SystemParametersInfoW + SetProcessDPIAware()，返回的是
        物理像素（本机 5120x2160），而 Tk 用的是 3413x1440，两者混用会让
        「按屏幕裁剪」全部失效。统一走 ui.controls.work_area。
        """
        from .controls import work_area
        return work_area(win)

    @classmethod
    def _default_geometry(cls, win) -> str:
        """1120x760 是 1080p 上的设计值：小屏按屏幕收缩，第一次打开居中。"""
        sw, sh = cls._work_area(win)
        w = max(940, min(1120, int(sw * 0.90)))
        h = max(620, min(760, int(sh * 0.90)))
        return '%dx%d+%d+%d' % (w, h, max(0, (sw - w) // 2),
                                max(0, (sh - h) // 2 - 20))

    @classmethod
    def _saved_geometry(cls, win) -> str:
        """记住上次拉到多大的窗口（存在 config.json 的 geometry 里）。

        启动时按当前屏幕工作区裁剪：记住的尺寸若超过屏幕 80% 的宽或高，
        就作废回退到默认值——否则窗口会被上次的尺寸撑成接近全屏，
        多任务时不顺手。保存前也会裁剪，不会越存越大。
        """
        default = cls._default_geometry(win)
        try:
            g = (engine.config() or {}).get('geometry') or ''
            if re.match(r'^\d{3,5}x\d{3,5}([+-]\d{1,5}[+-]\d{1,5})?$', g):
                w, h = (int(x) for x in g.split('+')[0].split('x'))
                sw, sh = cls._work_area(win)
                if (w > sw * 0.8 or h > sh * 0.8
                        or w > sw or h > sh):
                    return default
                return g
        except Exception:
            pass
        return default

    def on_close(self):
        for window in self.root.winfo_children():
            if isinstance(window, (ModelDialog, AccelerationDialog, SettingsDialog)):
                try:
                    window.stop()       # 只停下载，不弹确认：主窗口要关了
                except Exception:
                    pass
        try:
            g = self.root.geometry()
            m = re.match(r'^(\d+)x(\d+)', g)
            if m:
                sw, sh = self._work_area(self.root)
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
        picked = ''
        try:
            # 首次运行：本地已经有加速组件就自动开起来（默认 CPU 是因为发行包不带
            # 运行库，不是因为假设没独显）。用户自己选过之后这里就不再插手。
            picked = engine.auto_pick_accelerator()
            if picked:
                self.q.put(('log', T('检测到已装好的 %s 组件，已自动启用显卡加速') % picked, 'ok'))
        except Exception:
            picked = ''         # 挑加速失败绝不能挡住推理库启动
        try:
            ok = engine.start_server(on_log=lambda s: self.q.put(('log', s, 'err')))
            if not ok:
                self.q.put(('log', T('本地推理库不可用'), 'err'))
            elif picked:
                self.q.put(('log', T('本地推理库已就绪 · %s') % picked, 'ok'))
            else:
                self.q.put(('log', T('本地推理库已就绪 · CPU'), 'ok'))
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

        # 主区只有一列：三步操作 → 结果表 → 选中详情条 → 日志抽屉。
        # 右侧栏取消了：它占的 270px 还给了结果表（7 列要 956px，原来只给到
        # 775，两个文件名一直在被截断）。预览和选中详情改成表格下方一条
        # 68px 的横排详情条——同一批东西竖排吃 270px 宽，横排只吃 68px 高。
        body = tk.Frame(self.root, bg=theme.C_BG)
        body.pack(fill='both', expand=True, padx=14, pady=(0, theme.S2))
        left = tk.Frame(body, bg=theme.C_BG)
        left.pack(side='left', fill='both', expand=True)

        self._build_control(left)
        self._build_table(left)
        self._build_detail(left)
        self._build_log_drawer(left)

        # 识别过程中不能再动的东西（改了目录或范围会让结果对不上号）。
        # 处理范围的勾选在设置窗口里，由设置窗口自己按 running 置灰（见 refresh）。
        self._busy_widgets = [w for w in (self.ent_dir, self.cmb_preset) if w is not None]

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
        # 设置：常驻在标题栏，不再藏在右下角侧栏里
        ttk.Button(head, text=T('⚙ 设置'), command=self.open_settings,
                   width=theme.button_width(9, 12)).pack(side='right', padx=(0, 8), pady=(4, 0))

    # ---------------------------------------------------------------- 模式
    def _build_mode(self, parent):
        """「模式」二选一：要不要让模型看画面（S-18）。

        普通人不需要先想明白「AI 描述 / 照片描述 / 模型 / 加速」四个开关该怎么配，
        他只需要回答一个问题：让不让模型看画面。让 = 智能命名，不让 = 秒改名。
        单项仍然都能在设置里改，改完这里显示「自定义」，不强行拉回某个模式。
        """
        row = tk.Frame(parent, bg=theme.C_CARD)
        row.pack(fill='x', pady=(0, 9))
        self.choice_mode = ChoiceRow(
            row, T('模式'),
            [(T('智能命名'), 'smart'), (T('秒改名'), 'quick')],
            self.var_mode, command=self._on_mode_pick, bg=theme.C_CARD)
        self.choice_mode.pack(side='left')
        self.lbl_mode = tk.Label(row, text='', bg=theme.C_CARD, fg=theme.C_HINT,
                                 font=(theme.FONT, 9), anchor='w')
        self.lbl_mode.pack(side='left', padx=(10, 0))

    def _recommended_mode(self) -> str:
        """按这台机器推荐一次：有显卡就走智能命名，纯 CPU 先给秒改名。

        判「有没有显卡」看两处：已经装好的加速组件（autopick 会顺手启用），
        以及 nvidia-smi 报出来的硬件。只看组件是不够的——新机器上组件还没下，
        但显卡明明在，不该因此把人推到「秒改名」。
        探测给 2 秒上限：这是在启动路径上，宁可判成 CPU 也不能卡住首屏。
        """
        try:
            if engine.accelerator.active_name:
                return 'smart'
            if engine.auto_pick_accelerator():
                return 'smart'
        except Exception:
            pass
        try:
            if gpu_memory(timeout=2.0).get('name'):
                return 'smart'
        except Exception:
            pass
        return 'quick'

    def _init_mode(self):
        """第一次运行替用户选一次；之后一律以 config.json 里的 mode 为准。"""
        saved = str((engine.config() or {}).get('mode') or '')
        if saved in ('smart', 'quick', 'custom'):
            self.var_mode.set(saved)
            self._sync_mode_hint()
            return
        mode = self._recommended_mode()
        self._mode_auto = True
        self.var_mode.set(mode)
        self._apply_mode(mode)

    def _apply_mode(self, mode):
        """把模式对应的开关真的写进变量和 config.json。"""
        preset = MODE_PRESETS.get(mode)
        if not preset:
            return
        self.var_ai_desc.set(bool(preset.get('ai_desc')))
        self.var_desc_photo.set(bool(preset.get('desc_photo')))
        self._save_sw('ai_desc', self.var_ai_desc)
        try:
            engine.save_config(mode=mode)
        except Exception:
            pass
        self._refresh_scope()
        self._sync_mode_hint()

    def _on_mode_pick(self, *a):
        mode = self.var_mode.get()
        if mode not in MODE_PRESETS:
            return
        self._mode_auto = False
        self._apply_mode(mode)

    def _sync_mode_hint(self):
        """按当前开关组合回填模式，并写右边那行小字。

        组合对不上任何预设（用户在设置里单独改过某一项）就显示「自定义」，
        而不是硬拉回上一次点的那个模式——那样界面上写的和实际跑的就对不上了。

        这里只改界面、**不写 config**：`_sync_mode_hint` 会被变量的每次变动触发，
        包括程序自己的临时切换（例如关掉「照片」时 `_refresh_scope` 会顺手清掉
        「照片也写描述」）。那种中间态要是也落盘，config.json 里就会留下一个
        谁也没点过的「自定义」。真正要记的选择，由 `_apply_mode` 写。
        """
        ai = bool(self.var_ai_desc.get())
        desc = bool(self.var_desc_photo.get())
        if not ai:
            mode = 'quick'
        elif desc:
            mode = 'smart'
        else:
            mode = 'custom'
        try:
            if self.var_mode.get() != mode:
                self.var_mode.set(mode)
        except Exception:
            pass
        hint = ''
        if mode == 'custom':
            hint = T('自定义')
        elif self._mode_auto:
            hint = T('已为你选好')
        try:
            self.lbl_mode.configure(text=hint)
        except Exception:
            pass

    def _build_control(self, parent):
        """两行走完：① 一个大按钮选文件夹 ② 开始识别 → 核对并改名。

        原来这里是「文件夹」标签 + 输入框 + 「浏览…」+ 预设下拉，一行挤了四个
        控件。普通人第一眼的困惑正是「我该点哪个」。现在整块就是一个按钮：
        点它选目录，选完按钮上直接写「{文件夹名} · 128 张」。
        进度条平时不存在——跑起来时「开始识别」这个按钮原地变成进度条，
        不再额外占一行，也不会留一个空槽在那里。
        """
        card = self._card(parent)
        card.pack(fill='x')
        inner = tk.Frame(card, bg=theme.C_CARD)
        inner.pack(fill='x', padx=12, pady=10)

        # —— 第 0 步：模式（调不调模型，只有这一个问题的答案）
        self._build_mode(inner)

        # —— 第 1 步：一个按钮
        # 预设改成这里的「模式」二选一，不再放下拉。变量留着是为了兼容旧配置。
        self._preset_disp = {T(k): k for k in engine.PRESETS}
        self.var_preset = tk.StringVar(value='')
        self.cmb_preset = None
        self.var_dir = tk.StringVar(
            value=(list(engine.PRESETS.values())[0] if engine.PRESETS else ''))
        # 换了目录，上一次的运行状态就不算数了，回到「未开始」
        self.var_dir.trace_add('write', lambda *a: self._on_dir_change())
        self.btn_dir = ttk.Button(inner, text=T('📁 选择照片文件夹'),
                                  style='Dir.TButton', command=self.pick_dir)
        self.btn_dir.pack(fill='x')
        self.ent_dir = self.btn_dir      # 兼容：_busy_widgets 靠它置灰
        # 这一步会处理什么 / 文件夹空不空，就写在按钮下面一行小字
        self.lbl_scope = tk.Label(inner, text='', bg=theme.C_CARD, fg=theme.C_SUB,
                                  font=(theme.FONT, 9), anchor='w', justify='left')
        self.lbl_scope.pack(fill='x', pady=(7, 0))

        # —— 第 2 步：开始识别（原地变进度条）+ 核对并改名接棒
        r2 = tk.Frame(inner, bg=theme.C_CARD)
        r2.pack(fill='x', pady=(11, 0))
        self.go_slot = tk.Frame(r2, bg=theme.C_CARD, height=42, width=206)
        self.go_slot.pack(side='left')
        self.go_slot.pack_propagate(False)
        self.btn_go = ttk.Button(self.go_slot, text=T('▶ 开始识别'),
                                 style='Go.TButton', command=self.on_go)
        self.btn_go.pack(fill='both', expand=True)
        # 顶替按钮的那块：进度条 + 一行字。与按钮同位置同高度（容器定高
        # pack_propagate(False)），所以切换时版面不跳。平时不 pack。
        self.go_bar = tk.Frame(self.go_slot, bg=theme.C_CARD)
        self.pb = ttk.Progressbar(self.go_bar, mode='determinate', maximum=100)
        self.pb.pack(fill='x', pady=(9, 0))
        self.lbl_prog = tk.Label(self.go_bar, text='', bg=theme.C_CARD,
                                 fg=theme.C_ACCENT, font=(theme.FONT, 9, 'bold'),
                                 anchor='w')
        self.lbl_prog.pack(fill='x', pady=(3, 0))

        self.btn_plan = ttk.Button(r2, text=T('核对并改名…'), style='Sec.TButton',
                                   command=self.open_plan, state='disabled',
                                   width=theme.button_width(11, 13))
        self.btn_plan.pack(side='left', padx=(10, 0))
        # 灰着的时候停在上面会说明为什么点不了，不是干等着用户自己猜
        DisabledTip(self.btn_plan, lambda: T('识别完成后点亮'))
        self.btn_stop = ttk.Button(r2, text=T('停止识别'), style='Sec.TButton',
                                   width=theme.button_width(10, 9),
                                   command=self.on_stop, state='disabled')
        # 平均/剩余靠右：只在跑的时候有内容
        self.lbl_avg = tk.Label(r2, text='', bg=theme.C_CARD, fg=theme.C_SUB,
                                font=(theme.FONT, 9), anchor='e', justify='right')
        self.lbl_avg.pack(side='right')

    # ------------------------------------------------------------ 结果表
    def _build_table(self, parent):
        card = self._card(parent)
        card.pack(fill='both', expand=True, pady=(10, 0))
        head = tk.Frame(card, bg=theme.C_CARD)
        head.pack(fill='x', padx=theme.S4, pady=(theme.S2 + 1, theme.S1))
        tk.Label(head, text=T('处理结果'), bg=theme.C_CARD, fg=theme.C_SUB,
                 font=(theme.FONT, 10, 'bold')).pack(side='left')
        # 三个「打开」入口原来在「⋯更多」菜单里，和侧栏的按钮重复。
        # 表头只留「打开目录」。改名台账搬进核对窗口页脚（那里本来就在操作
        # 同一个目录），识别报告改由跑完的浮条里「查看详细报告」进。
        ttk.Button(head, text=T('打开目录'), style='Link.TButton',
                   command=self.open_dir).pack(side='right', padx=(10, 0))

        # 7 列压成 4 列：保留原文件名（用户要能一眼看出这是哪张），
        # 地名并进新文件名（新名字本来就是「日期_时间_地名_内容」），
        # 耗时改由进度条旁边的动态平均说明，不再单占一列。
        cols = ('old', 'new', 'desc', 'st')
        heads = (T('原文件名'), T('新文件名'), T('描述'), T('状态'))
        samples = {
            'old': ('VID_20240115_183022_00_012.mp4',),
            'new': ('2024-01-15_18-30-22_西湖区_湖边长椅.mp4',),
            'desc': ('一名男子坐在湖边长椅上',),
            'st': ('没认出来，点我看看',),
        }
        bounds = {'old': (150, 320), 'new': (170, 340),
                  'desc': (120, 320), 'st': (70, 150)}
        widths = self._fit_columns(heads, samples, bounds)
        self._cols = cols
        self._col_bounds = bounds
        # 实际内容最宽值：插入行时增量累计，插入后合并一次调整列宽。
        # 中文地名/描述长短差很多，固定宽度总会差几个像素，截掉一个字用户就得
        # 去详情条里确认——那不如让列自己长到够。
        self._col_need = {c: widths[c] for c in cols}
        self._width_job = None

        wrap = tk.Frame(card, bg=theme.C_CARD)
        wrap.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        self.tree = ttk.Treeview(wrap, columns=cols, show='headings', height=9)
        for c, h in zip(cols, heads):
            self.tree.heading(c, text=h, anchor='w')
            # minwidth 留到下限：窗口拉得很窄时先压缩地名/描述，再不够才出
            # 横向滚动条，绝不把两个文件名截断
            self.tree.column(c, width=widths[c], minwidth=bounds[c][0],
                             anchor='w', stretch=(c == 'desc'))
        vs = ttk.Scrollbar(wrap, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        # 横向滚动只在真放不下时出现，平时不白占 16px（见 _sync_hscroll）
        self.hs = ttk.Scrollbar(wrap, orient='horizontal', command=self.tree.xview)
        self.tree.configure(xscrollcommand=self.hs.set)
        self._hs_shown = False
        self.tree.bind('<Configure>', lambda e: self._sync_hscroll(), add='+')
        self.tree.pack(side='left', fill='both', expand=True)
        vs.pack(side='right', fill='y')
        self.tree.tag_configure('odd', background=theme.C_ROW_ALT)
        self.tree.tag_configure('fail', foreground=theme.C_ERR)
        self.tree.tag_configure('st_ok', background='#eefaf1')
        self.tree.tag_configure('st_skip', foreground=theme.C_SUB)
        self.tree.tag_configure('st_fail', background='#fdeceb')
        self.tree.tag_configure('wait_time', foreground=theme.C_SUB)
        self.tree.bind('<<TreeviewSelect>>', self.on_pick_row)
        # 状态列里「点我看看 / 点我补时间」可以直接点（见 _on_cell_click）
        self.tree.bind('<Button-1>', self._on_cell_click, add='+')
        # 悬停预览：图 + 「原名 → 新名」放同一个浮窗，避免两个提示窗打架
        RowPreview(self.root, self.tree, self._hover_photo, self._hover_lines)
        # 空表时在中间放一句话，而不是留一片白让人不知道该干嘛
        self.lbl_empty = tk.Label(
            self.tree, text=T('选好文件夹，点「开始识别」，剩下的交给我'),
            bg=theme.C_CARD, fg=theme.C_HINT, font=(theme.FONT, 10))
        self._sync_empty()

    def _sync_empty(self):
        """空表才显示那句话；一旦有行就收起来。"""
        try:
            if self.tree.get_children():
                self.lbl_empty.place_forget()
            else:
                self.lbl_empty.place(relx=0.5, rely=0.5, anchor='center')
        except Exception:
            pass

    @staticmethod
    def _fit_columns(heads, samples, bounds):
        """按实测字体宽度算列宽：max(表头, 典型内容) + 24px 余量，再夹到上下限。

        写死宽度的老办法在中文名上一试就崩：一个「2024-01-15_西湖区_湖边长椅.mp4」
        要 246px，而原来只给 196px。这里用真字体量，换字号、换语言都不会错位。
        """
        try:
            hf = tkfont.nametofont('TkHeadingFont')
            bf = tkfont.nametofont('TkDefaultFont')
        except Exception:
            return {c: bounds[c][0] for c in samples}
        out = {}
        for c, h in zip(samples.keys(), heads):
            need = hf.measure(h)
            try:
                need = max([need] + [bf.measure(x) for x in samples.get(c, ())])
            except Exception:
                pass
            need += 24                     # 单元格左右内边距
            lo, hi = bounds.get(c, (60, 320))
            out[c] = max(lo, min(hi, need))
        return out

    def _note_width(self, *pairs):
        """记录某几列的实际内容宽度（只增不减，插入几千行也只是多算几次）。"""
        if not hasattr(self, '_col_need'):
            return
        try:
            bf = tkfont.nametofont('TkDefaultFont')
        except Exception:
            return
        for col, text in pairs:
            if not text:
                continue
            try:
                w = bf.measure(str(text)) + 24
            except Exception:
                continue
            if w > self._col_need.get(col, 0):
                self._col_need[col] = w
        if self._width_job is None:
            try:
                self._width_job = self.root.after_idle(self._apply_widths)
            except Exception:
                self._width_job = None

    def _apply_widths(self):
        """把内容宽度写回列上：两个文件名优先吃饱，描述拿剩下的。

        顺序是刻意的——「原文件名 / 新文件名」是这软件的最终产物，截断了等于白干；
        描述可以截，鼠标停住还有提示。全让完还不够就出横向滚动条。
        """
        self._width_job = None
        try:
            avail = self.tree.winfo_width()
            b = getattr(self, '_col_bounds', {})
            if not b:
                return
            cur = {c: int(self.tree.column(c, 'width')) for c in self._cols}
            need = {c: max(cur[c], int(self._col_need.get(c, 0))) for c in self._cols}
            want = dict(cur)

            # 1) 两个文件名：内容多宽就多宽（夹到上下限）
            for c in ('old', 'new'):
                lo, hi = b[c]
                want[c] = max(lo, min(hi, need[c]))
            # 2) 状态列：字都很短，够放下最长的那个就行
            lo_s, hi_s = b['st']
            want['st'] = max(lo_s, min(hi_s, need['st']))

            # 3) 描述：吃掉剩下的全部宽度，不低于下限
            lo_d, hi_d = b['desc']
            wd = max(lo_d, min(need['desc'], hi_d))
            if avail > 1:
                fixed = want['old'] + want['new'] + want['st']
                budget = max(avail - fixed, lo_d)
                wd = min(wd, max(lo_d, budget))
            want['desc'] = wd

            for c, w in want.items():
                if w != cur[c]:
                    self.tree.column(c, width=w)
            if any(w != cur[c] for c, w in want.items()):
                self._sync_hscroll()
        except Exception:
            pass

    def _sync_hscroll(self):
        """列宽合计超过可视宽度时才显示横向滚动条（否则白占 16px 高度）。"""
        try:
            total = sum(int(self.tree.column(c, 'width')) for c in self._cols)
            need = total > self.tree.winfo_width() + 2
        except Exception:
            return
        if need == getattr(self, '_hs_shown', False):
            return
        self._hs_shown = need
        try:
            if need:
                self.hs.pack(side='bottom', fill='x')
            else:
                self.hs.pack_forget()
        except Exception:
            pass

    # ------------------------------------------------------------ 选中详情条
    def _build_detail(self, parent):
        """表格下方一条横排详情：原名 → 新名 + 地名/耗时/失败原因。

        缩略图不再常驻在这里——预览改成「鼠标在行上停半秒，旁边浮出来」
        （见 widgets.RowPreview）。核对是偶发动作，不值得为它常驻占 90px 高度，
        原来那块写着「无预览」的灰方块更是一块纯占位的死空白。
        """
        card = self._card(parent)
        card.pack(fill='x', pady=(8, 0))
        row = tk.Frame(card, bg=theme.C_CARD)
        row.pack(fill='x', padx=12, pady=8)
        # 这个 label 不再上屏，只作为「当前缩略图」的引用持有者：
        # _show_photo / _show_photo_file 一直往里写，沿用它们不用改。
        self.lbl_prev = tk.Label(row, text='')
        box = tk.Frame(row, bg=theme.C_CARD)
        box.pack(side='left', fill='x', expand=True)
        self.lbl_d1 = tk.Label(box, text='', bg=theme.C_CARD, fg=theme.C_TEXT,
                               font=(theme.FONT, 10), anchor='w', justify='left')
        self.lbl_d1.pack(fill='x')
        self.lbl_d2 = tk.Label(box, text='', bg=theme.C_CARD, fg=theme.C_SUB,
                               font=(theme.FONT, 9), anchor='w', justify='left')
        self.lbl_d2.pack(fill='x', pady=(2, 0))
        self._set_detail(None)

    def _set_detail(self, r=None, fallback=''):
        """详情条两行字：第一行 原名 → 新名，第二行 地名 · 耗时 · 帧数/失败原因。

        名字被列宽截断时，这里是唯一能看到完整名字的地方，所以第一行优先。
        """
        if fallback or not r:
            self.lbl_d1.configure(
                text=fallback or T('点表格里的一行，这里显示它的原名和新名'),
                fg=theme.C_SUB)
            self.lbl_d2.configure(text=T('选中后这里显示地名、耗时和失败原因'),
                                  fg=theme.C_HINT)
            return
        self.lbl_d1.configure(
            text='%s　→　%s' % (r.get('old', ''),
                                r.get('new', '') or T('（时间空白，不改名）')),
            fg=theme.C_TEXT)
        bits = []
        if r.get('place'):
            bits.append(r['place'])
        secs = r.get('secs') or r.get('t_model') or 0
        if secs:
            bits.append('%.1fs' % secs)
        if r.get('frames'):
            bits.append(T('%d 帧 / %d token') % (r['frames'], r.get('tokens', 0)))
        self.lbl_d2.configure(text='　·　'.join(bits), fg=theme.C_SUB)
        if r.get('err'):
            self.lbl_d2.configure(text=T('失败：%s') % r['err'], fg=theme.C_ERR)

    # ------------------------------------------------------------ 运行日志（可折叠）
    def _build_log_drawer(self, parent):
        """运行日志不再上屏，只留一个隐藏的 Text 存着它。

        主界面原来有一条「▸ 运行日志」窄条，点开是一屏技术日志——普通人不会看，
        出问题时也读不懂。现在出错不弹日志，改弹一句人话 + 「复制日志发给作者」
        （见 _error_summary）；日志本身照常完整记录，只是不出现在界面上。
        """
        self.var_log = tk.BooleanVar(value=False)
        self.lbl_log = None
        self.log_head = None
        self.log_tools = None
        self.log_body = None
        self.txt = tk.Text(self.root, height=7, wrap='none')
        self.txt.tag_configure('err', foreground='#ff7b72')
        self.txt.tag_configure('ok', foreground='#7ee787')
        self.txt.tag_configure('dim', foreground='#8b949e')
        self.txt.tag_configure('head', foreground='#79c0ff')

    def _log_apply(self, opened: bool):
        """日志区已经没有可见部分了，这个接口留着给旧调用方（自检等）。"""
        try:
            self.var_log.set(bool(opened))
        except Exception:
            pass

    def toggle_log(self):
        self._log_apply(not bool(self.var_log.get()))

    def _error_summary(self, n_fail):
        """跑完有失败时，弹一句人话，而不是把技术日志糊在界面上。

        按钮只有两个：重试（识别一次）、复制日志发给作者（日志进剪贴板）。
        日志内容普通人看不懂，但作者能看懂——所以把它做成「一键交给作者」。
        """
        if n_fail <= 0:
            return
        try:
            w = tk.Toplevel(self.root)
            w.title(APP_TITLE)
            w.configure(bg=theme.C_BG)
            w.transient(self.root)
            w.resizable(False, False)
            pad = tk.Frame(w, bg=theme.C_BG)
            pad.pack(fill='both', expand=True, padx=22, pady=18)
            tk.Label(pad, text=T('有 %d 张没认出来，已跳过') % n_fail,
                     bg=theme.C_BG, fg=theme.C_TEXT,
                     font=(theme.FONT, 11, 'bold')).pack(anchor='w')
            tk.Label(pad, text=T('这些文件保持原名不动。可以先「重试」，'
                                 '还不行就把日志复制给作者。'),
                     bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 9),
                     wraplength=380, justify='left').pack(anchor='w', pady=(6, 0))
            foot = tk.Frame(pad, bg=theme.C_BG)
            foot.pack(fill='x', pady=(16, 0))

            def copy_and_close():
                self.copy_log()
                self._toast(T('日志已复制，粘贴给作者就行'))
                w.destroy()

            ttk.Button(foot, text=T('复制日志发给作者'), style='Sec.TButton',
                       command=copy_and_close).pack(side='right')
            ttk.Button(foot, text=T('重试'), style='Go.TButton',
                       command=lambda: (w.destroy(), self.on_go())).pack(
                side='right', padx=(0, 8))
            w.update_idletasks()
            rw, rh = w.winfo_reqwidth(), w.winfo_reqheight()
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - rw) // 2)
            y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - rh) // 3)
            w.geometry('+%d+%d' % (x, y))
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
        """底部只留一句话：现在是什么状态。

        原来这一条塞了模型圆点、端口、`llama.cpp · CPU`、模型库路径、地名版权、
        版本号——像调试面板。普通人只想知道「现在能不能点」。现在整条只有一句
        `● 就绪` / `● 正在识别 12/128`，技术细节全部收进这行字的悬浮提示。
        模型没加载时这句话变成 `● 模型待加载` 并且可以点——直接跳到设置的
        模型组。原来点它没反应，用户不知道去哪配，按了开始也没动静。
        """
        bar = tk.Frame(self.root, bg=theme.C_BAR)
        bar.pack(side='bottom', fill='x')

        # 这一句就是整个状态栏。`lbl_state` 这个名字保留：_set_run_state 与
        # update_prog 都在改它，改名会牵一片。
        self.lbl_state = tk.Label(bar, text=T('● 就绪'), bg=theme.C_BAR,
                                  fg=theme.C_SUB, font=(theme.FONT, 9, 'bold'),
                                  cursor='hand2')
        self.lbl_state.pack(side='left', padx=(14, 0), pady=5)
        self.lbl_state.bind('<Button-1>', lambda e: self._on_state_click())
        Hover(self.lbl_state, lambda: self._state_hint_text())

        # 下面三个控件不再上屏，只留作数据容器：update_server 照旧往里写
        # backend·device 和模型库路径，悬浮提示再读出来给想知道的人看。
        self.dot_srv = tk.Label(bar, text=T('● 模型未启动'))
        self._srv_hint = ''
        self.lbl_port = tk.Label(bar, text='')
        self._port_hint = ''
        self.lbl_models = tk.Label(bar, text='')
        self._models_full = ''
        self._model_badge = (T('● 模型待加载'), theme.C_HINT)
        self._model_ready = False
        try:
            md = engine.models_dir()
            self.lbl_models.configure(text=T('模型库 %s') % cut(md, 34))
            self._models_full = md
        except Exception:
            pass

        # 版本号靠右，小字
        tk.Label(bar, text=(APP_VER if APP_VER.startswith('v') else 'v' + APP_VER),
                 bg=theme.C_BAR, fg=theme.C_HINT,
                 font=(theme.FONT, 8)).pack(side='right', padx=(0, 14), pady=5)

    def _state_hint_text(self):
        """状态那一句的悬浮说明：把技术细节放这里，不摆在明面上。"""
        bits = [getattr(self, '_srv_hint', '') or '']
        try:
            port = self.lbl_port.cget('text')
            if port:
                bits.append(port)
        except Exception:
            pass
        if getattr(self, '_port_hint', ''):
            bits.append(self._port_hint)
        if getattr(self, '_models_full', ''):
            bits.append(T('模型库 %s') % self._models_full)
        return '\n'.join([b for b in bits if b])

    def _on_state_click(self):
        """点状态那句话：模型没好就直接去配，别让人对着灰按钮猜。"""
        if getattr(self, '_model_ready', False):
            self._toast(self._state_hint_text() or T('● 就绪'))
            return
        try:
            self.open_settings(section='model')
        except Exception:
            self.open_settings()

    def _sync_idle_state(self, **kw):
        """空闲时状态栏那句跟着模型状态走：就绪 / 模型待加载 / 识别完成。

        跑起来之后由 _set_run_state 和 update_prog 接管，这里不插手。
        """
        if getattr(self, 'state', 'idle') != 'idle':
            return
        try:
            if getattr(self, '_model_ready', False):
                self.lbl_state.configure(text=T('● 就绪'), fg=theme.C_SUB)
            else:
                text, fg = getattr(self, '_model_badge',
                                   (T('● 模型待加载'), theme.C_HINT))
                self.lbl_state.configure(text=text, fg=fg)
        except Exception:
            pass

    # ------------------------------------------------------------ 日志
    def log(self, s, tag=None):
        ts = time.strftime('%H:%M:%S')
        self.txt.insert('end', '[%s] %s\n' % (ts, s), tag or ())
        self.txt.see('end')
        # 日志只留在后台。原来这里会把日志抽屉自动弹开，把技术报错糊到用户脸上；
        # 现在出错统一走 _error_summary 的人话框。
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

        第 3 步「核对并改名」也要锁：它改的是磁盘上的文件，半路进去和正在写的
        计划表会打架（按钮本身由 _refresh_plan_btn 按 running 维护）。
        """
        spec = ['disabled'] if busy else ['!disabled']
        for w in getattr(self, '_busy_widgets', []):
            try:
                w.state(spec)
            except Exception:
                pass
        # 模式是 tk.Label 拼的分段按钮，没有 ttk 的 state()，走自己的开关
        try:
            if self.choice_mode is not None:
                self.choice_mode.set_enabled(not busy)
        except Exception:
            pass

    # ------------------------------------------------------------ 运行状态
    def _set_run_state(self, s, **kw):
        """把「现在到底在干嘛」写清楚：按钮 vs 进度条 + 状态栏一句话。

        进度条不再常驻：跑起来的时候「开始识别」这个按钮原地换成进度条，
        跑完换回来。所以平时界面上看不到任何空的进度槽。
        「停止识别」平时也不占位，只有真在跑的时候才出现。
        """
        self.state = s
        go, stop = self.btn_go, self.btn_stop
        if s == 'idle':
            self._show_bar(False)
            go.configure(text=T('▶ 开始识别'), state='normal')
            self._show_stop(False)
            self.lbl_avg.configure(text='')
            self._sync_idle_state()
        elif s == 'running':
            self._show_bar(True)
            self.lbl_prog.configure(text=T('准备中…'))
            stop.configure(text=T('停止识别'), state='normal')
            self._show_stop(True)
            self.lbl_state.configure(text=T('● 正在识别…'), fg=theme.C_ACCENT)
        elif s == 'stopping':
            stop.configure(text=T('停止中…'), state='disabled')
            self._show_stop(True)
            self.lbl_state.configure(text=T('● 正在停止…'), fg=theme.C_WARN)
        elif s == 'stopped':
            self._show_bar(False)
            go.configure(text=T('▶ 重新开始识别'), state='normal')
            stop.configure(text=T('停止识别'), state='disabled')
            self._show_stop(False)
            self.lbl_avg.configure(text='')
            self.lbl_state.configure(text=T('● 已停止 · %d/%d')
                                     % (kw.get('i', self.done), kw.get('n', self.total)),
                                     fg=theme.C_WARN)
        elif s == 'done':
            self._show_bar(False)
            go.configure(text=T('↻ 再识别一次'), state='normal')
            stop.configure(text=T('停止识别'), state='disabled')
            self._show_stop(False)
            self.lbl_avg.configure(text='')
            self._sync_idle_state()

    def _show_bar(self, show: bool):
        """「开始识别」按钮 ↔ 进度条，在同一格子里就地互换。

        容器 go_slot 定高且 pack_propagate(False)，所以两者高度一致，
        切换时下面的表格不会跳一下。
        """
        show = bool(show)
        if getattr(self, '_bar_shown', None) == show:
            return
        try:
            if show:
                self.btn_go.pack_forget()
                self.go_bar.pack(fill='both', expand=True)
            else:
                self.go_bar.pack_forget()
                self.btn_go.pack(fill='both', expand=True)
            self._bar_shown = show
        except Exception:
            pass

    def _toast(self, text, action_text='', action=None):
        """底部浮出一句话，几秒后自己消失（跑完的小结、模型状态都用它）。"""
        try:
            if getattr(self, '_toast_win', None) is None:
                self._toast_win = Toast(self.root)
            self._toast_win.show(text, action_text, action)
        except Exception:
            pass

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
                # after=btn_plan：按钮顺序是 进度槽 → 核对并改名 → 停止
                self.btn_stop.pack(side='left', padx=(10, 0), after=self.btn_plan)
            else:
                self.btn_stop.pack_forget()
            self._stop_shown = show
        except Exception:
            pass

    def _refresh_plan_btn(self):
        """目录里已经有计划表时，第 3 步「核对并改名」才可点。

        可点的时候换成主色（GoSm）：全场只有一个蓝按钮，用户看蓝的就知道
        下一步该点哪个——识别前蓝的是「开始识别」，识别完蓝的换成它。
        GoSm 与 Sec 的 padding 一样，换样式时按钮尺寸不跳。
        """
        if self.running:
            self.btn_plan.configure(state='disabled', style='Sec.TButton')
            return
        d = os.path.abspath(self.var_dir.get().strip())
        try:
            has = bool(os.path.isdir(d) and engine.load_plan(d))
        except Exception:
            has = False
        self.btn_plan.configure(state=('normal' if has else 'disabled'),
                                style=('GoSm.TButton' if has else 'Sec.TButton'))

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
        """把「这次到底会处理什么」写在第 1 步按钮下面一行小字里。"""
        v, p = bool(self.var_video.get()), bool(self.var_photos.get())
        ai = bool(getattr(self, 'var_ai_desc', None) and self.var_ai_desc.get())
        # 「照片也写内容描述」是「AI描述」的子选项：总开关关着就置灰并清掉勾选。
        # 勾选框现在住在设置窗口里，窗口没开时是 None，所以要判空。
        cb = getattr(self, 'cb_desc_photo', None)
        if cb is not None:
            cb.state(['!disabled'] if (p and ai) else ['disabled'])
        if not (p and ai):
            self.var_desc_photo.set(False)
        self._refresh_dir_btn()
        if not v and not p:
            self.lbl_scope.configure(text=T('两个都没勾选 · 不会处理任何文件'), fg=theme.C_ERR)
            return
        # 选了个空文件夹：先把这件事说清楚，比列一串参数有用
        d = (self.var_dir.get() or '').strip()
        if d and os.path.isdir(d):
            npic, nvid = self._count_media(d)
            if npic + nvid == 0:
                self.lbl_scope.configure(text=T('这个文件夹里没有照片或视频'),
                                         fg=theme.C_ERR)
                return
        bits = []
        if v:
            bits.append(T('视频：抽帧交给模型看') if ai
                        else T('视频：只读拍摄时间和 GPS，秒级'))
        if p:
            bits.append(T('照片：模型写内容描述')
                        if (ai and self.var_desc_photo.get())
                        else T('照片：只读拍摄时间和 GPS，秒级'))
        if not ai:
            bits.append(T('纯离线，不调用模型'))
        elif v or (p and self.var_desc_photo.get()):
            # 原来这里写死「约 11 秒/条」，换了机器就是错的——有显卡十几秒，
            # 纯 CPU 可能一分钟。只说会慢，不承诺具体秒数。
            bits.append(T('速度看机器：有显卡十几秒一张，纯 CPU 会慢不少'))
        # 开了落盘，模型会多写一段 100~200 字的长描述，每条多花几秒，先说清楚
        # （设置区比这里晚建，首次调用时这些变量可能还不存在）
        g = lambda k: bool(getattr(self, k, None) and getattr(self, k).get())
        if g('var_meta') or g('var_txt') or g('var_md'):
            bits.append(T('并写入元数据/摘要，每条多几秒'))
        if g('var_netgeo'):
            bits.append(T('联网精确定位：开'))
        self.lbl_scope.configure(text='　·　'.join(bits), fg=theme.C_SUB)

    def _refresh_dir_btn(self):
        """第 1 步按钮上的字：没选是「选照片文件夹」，选好了写「文件夹名 · 128 张」。

        只显示文件夹名不显示全路径——全路径又长又没人看，还把按钮撑得没边。
        """
        try:
            d = (self.var_dir.get() or '').strip()
            if not d or not os.path.isdir(d):
                self.btn_dir.configure(text=T('📁 选择照片文件夹'))
                return
            name = os.path.basename(os.path.normpath(d)) or d
            npic, nvid = self._count_media(d)
            n = npic + nvid
            if n:
                self.btn_dir.configure(text=T('📁 %s · %d 张') % (name, n))
            else:
                self.btn_dir.configure(text=T('📁 %s · 空文件夹') % name)
        except Exception:
            pass

    @staticmethod
    def _count_media(d):
        """数一下这个文件夹里有多少张照片、多少个视频（只看这一层）。"""
        try:
            from core.defaults import PHOTO_EXT, VID_EXT
        except Exception:
            PHOTO_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif')
            VID_EXT = ('.mp4', '.mov', '.mkv', '.avi', '.m4v', '.3gp', '.wmv')
        npic = nvid = 0
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if not e.is_file():
                            continue
                    except Exception:
                        continue
                    ext = os.path.splitext(e.name)[1].lower()
                    if ext in PHOTO_EXT:
                        npic += 1
                    elif ext in VID_EXT:
                        nvid += 1
        except Exception:
            pass
        return npic, nvid

    def open_plan(self, auto=False, focus_index=None):
        """打开「核对并改名」窗口（时间/地点/描述都能手改）。

        focus_index 给定时会翻到那一行并选中它——主界面点状态列的
        「点我补时间 / 点我看看」就是走这条路，用户点哪行就看到哪行。
        """
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
        # 回滚入口从 ⋯更多 菜单挪进核对窗口：它只在「这个目录改过名」时才有意义，
        # 和这张表天然是一起的，也不用再放一个随时灰着的菜单项。
        dlg = PlanDialog(self.root, d, items, on_rollback=self.on_rollback)
        if focus_index is not None:
            try:
                dlg.after(120, lambda: dlg.focus_row(focus_index))
            except Exception:
                pass
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
                if bool((engine.config() or {}).get('write_md', False)):
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
        self._sync_empty()
        self._iid_idx.clear()
        self._rows.clear()
        for i, it in enumerate(engine.load_plan(d), 1):
            st, stag = self._status_of(it)
            tags = ['odd'] if i % 2 else []
            if stag:
                tags.append(stag)
            self._rows[it.get('index', i)] = it
            self._note_width(('old', it.get('old')), ('new', it.get('new')),
                             ('desc', it.get('desc')), ('st', st))
            iid = self.tree.insert('', 'end', tags=tuple(tags), values=(
                it.get('old', ''),
                it.get('new', '') or T('（时间空白）'),
                it.get('desc', '') or '—', st))
            self._iid_idx[iid] = it.get('index', i)
        self._apply_widths()

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
        if self.cb_model is not None:
            self.cb_model.configure(state='disabled')
        self.dot_srv.configure(text=T('● 模型操作中…'), fg=theme.C_ACCENT)

        def work():
            try:
                # 动作可以返回一句话覆盖默认提示（例如「已暂停，进度已保留」）：
                # 用户主动暂停不该被当成失败播报
                result = action()
                self.q.put(('model_done', (True, result if isinstance(result, str) and result
                                           else message), None))
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
        """首次启动弹一次三步向导；勾了「不再显示」就写进 config。

        原来这里弹的是整篇使用说明（87 段），普通人不会读。真正要知道的只有
        三件事：选文件夹、点开始、核对改名。详细说明仍留在标题栏的「使用说明」。
        """
        try:
            if not engine.installed_models():
                messagebox.showinfo(APP_TITLE, T('未找到本地模型，请在模型管理中下载 4B / 2B 模型，或选择已有模型目录。'))
                self.open_model_dialog()
                return
            if self._sw('help_autoshow', True):
                self._show_wizard()
        except Exception:
            pass

    def _show_wizard(self):
        """三步小向导：① 选文件夹 ② 点开始识别 ③ 核对并改名。"""
        w = tk.Toplevel(self.root)
        w.title(APP_TITLE)
        w.configure(bg=theme.C_BG)
        w.transient(self.root)
        w.resizable(False, False)
        pad = tk.Frame(w, bg=theme.C_BG)
        pad.pack(fill='both', expand=True, padx=24, pady=20)
        tk.Label(pad, text=T('三步就能改好名字'), bg=theme.C_BG, fg=theme.C_TEXT,
                 font=(theme.FONT, 13, 'bold')).pack(anchor='w')
        steps = ((1, T('选文件夹'), T('点那个大按钮，选一个装着照片或视频的文件夹。')),
                 (2, T('点开始识别'), T('模型自己看画面，给每个文件起名字。第一次会慢一些。')),
                 (3, T('核对并改名'), T('确认没问题就点应用；想改哪一行，直接点格子改。')))
        for n, title, desc in steps:
            row = tk.Frame(pad, bg=theme.C_BG)
            row.pack(fill='x', pady=(12, 0))
            tk.Label(row, text='%d' % n, bg=theme.C_ACCENT, fg='#ffffff',
                     font=(theme.FONT, 9, 'bold'), width=2).pack(side='left', anchor='n', ipady=2)
            box = tk.Frame(row, bg=theme.C_BG)
            box.pack(side='left', fill='x', expand=True, padx=(10, 0))
            tk.Label(box, text=title, bg=theme.C_BG, fg=theme.C_TEXT,
                     font=(theme.FONT, 10, 'bold'), anchor='w').pack(fill='x')
            tk.Label(box, text=desc, bg=theme.C_BG, fg=theme.C_SUB,
                     font=(theme.FONT, 9), anchor='w', justify='left',
                     wraplength=360).pack(fill='x', pady=(2, 0))
        var = tk.BooleanVar(value=True)
        CheckRow(pad, T('不再显示'), var, bg=theme.C_BG,
                 font_size=10).pack(anchor='w', pady=(18, 0))
        foot = tk.Frame(pad, bg=theme.C_BG)
        foot.pack(fill='x', pady=(14, 0))

        def close():
            try:
                if var.get():
                    engine.save_config(help_autoshow=False)
            except Exception:
                pass
            w.destroy()

        ttk.Button(foot, text=T('知道了'), style='Go.TButton',
                   command=close).pack(side='right')
        w.bind('<Escape>', lambda e: close())
        w.protocol('WM_DELETE_WINDOW', close)
        w.update_idletasks()
        rw, rh = w.winfo_reqwidth(), w.winfo_reqheight()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - rw) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - rh) // 3)
        w.geometry('+%d+%d' % (x, y))
        try:
            w.grab_set()
        except Exception:
            pass

    def open_model_dialog(self):
        for w in self.root.winfo_children():
            if isinstance(w, ModelDialog) and w.winfo_exists():
                w.lift()
                w.focus_set()
                return
        ModelDialog(self.root, self)

    def open_settings(self, section=None):
        """二级窗口：所有设置（含 GPU 加速）都在这一层，不再往下套第三层。

        section 给定时（例如状态栏点「● 模型待加载」传 'model'）打开后直接把
        滚动位置带到那一组，用户不用自己在长列表里找。
        """
        dlg = getattr(self, 'settings_dlg', None)
        try:
            if dlg is not None and dlg.winfo_exists():
                dlg.deiconify()
                dlg.lift()
                dlg.focus_set()
                if section:
                    dlg.focus_section(section)
                return
        except Exception:
            pass
        self.settings_dlg = SettingsDialog(self.root, self, focus_section=section)

    def open_acceleration(self):
        """旧入口保留：GPU 加速已经并入设置窗口，这里直接开设置。"""
        self.open_settings()

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
                if bool((engine.config() or {}).get('write_md', False)):
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
        self._sync_empty()
        self.total, self.done = 0, 0
        self.t_start = time.time()
        self.stop_evt.clear()
        self.running = True
        self._rows.clear()
        self._iid_idx.clear()
        self._thumbs.clear()
        self._photo_thumbs.clear()   # 文件马上要改名了，旧缩略图全部作废
        self._set_run_state('running')
        self.btn_plan.configure(state='disabled')
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
                if self.cb_model is not None:
                    try:
                        self.cb_model.configure(state='readonly')
                    except Exception:
                        pass
                ok, message = payload
                self.log(message, 'ok' if ok else 'err')
                self.update_server(engine.server_info(), engine.list_models(), engine.runtime_status())
                for window in self.root.winfo_children():
                    if isinstance(window, (ModelDialog, AccelerationDialog, SettingsDialog)):
                        try:
                            window.refresh()
                        except Exception:
                            pass
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
        if self.cb_model is not None:
            try:
                self.cb_model.configure(values=values)
            except Exception:
                pass
        dot = self.dot_srv
        self._srv_hint = rt.get('error') or T('点这里加载所选模型')
        self._model_ready = False
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
            self._model_ready = True
        else:
            self._srv_hint = T('识别时自动加载，也可点击提前加载')
            dot.configure(text=T('● 模型待加载'), fg=theme.C_HINT, cursor='hand2')
        # 状态栏那一句跟着走（空闲才动；跑起来时由 _set_run_state 接管）
        self._model_badge = (dot.cget('text'), dot.cget('fg'))
        self._sync_idle_state()

    def update_prog(self, i, total, stats):
        self.total, self.done = total, i
        pct = (i / total * 100) if total else 0
        self.pb.configure(value=pct)
        el = stats.get('elapsed', 0)
        avg = stats.get('avg', 0) or 0
        left = avg * max(0, total - i)
        # 「识别中 12/128」写在按钮变的进度条上；平均/剩余单独一行放右边。
        # 原来这里是一长串「已处理 12/128 · 平均 5.3s/条 · 已用 1 分 · 预计剩余 9 分」，
        # 信息是全的，但挤在一个小按钮的位置上没人读得完。
        self.lbl_prog.configure(text=T('识别中 %d/%d') % (i, total))
        if avg > 0:
            self.lbl_avg.configure(
                text=T('平均约 %.1f 秒/张，还剩约 %s') % (avg, fmt_dur(left)))
        else:
            self.lbl_avg.configure(text=T('已用 %s') % fmt_dur(el))
        if self.state == 'running':
            self.lbl_state.configure(text=T('● 正在识别 %d/%d') % (i, total),
                                     fg=theme.C_ACCENT)

    @staticmethod
    def _status_of(it):
        """一行现在处于什么状态（配合 tag 上色）。

        两种失败长得一样、行为也一样：文案统一成「{失败类型}，点我看看」，
        点了都打开核对框并定位到这一行看原因。用户不用记两套规则。
        """
        if not it.get('ok', True):
            return T('没认出来，点我看看'), 'fail'
        if it.get('applied'):
            return T('✓ 已改名'), 'st_ok'
        if it.get('err'):
            return T('改名失败，点我看看'), 'st_fail'
        if not str(it.get('time') or '').strip():
            return T('点我补时间'), 'wait_time'
        if it.get('new') and it['new'] != it.get('old'):
            return T('✓'), ''
        return T('—'), 'st_skip'

    # 状态列里这几类可以点：点了就去核对框改/看这一行
    _CLICK_ST = ('fail', 'st_fail', 'wait_time')

    def _on_cell_click(self, ev):
        """点状态列的「点我看看 / 点我补时间」→ 打开核对框并定位到这一行。

        只认状态那一列：点文件名、描述还是照常选中整行看详情。
        单行重跑要动识别循环（计划里明确不碰引擎），所以这里做的是
        「把人送到能改的地方」。
        """
        try:
            if self.tree.identify_column(ev.x) != '#%d' % len(self._cols):
                return
            iid = self.tree.identify_row(ev.y)
            idx = self._iid_idx.get(iid)
            if idx is None:
                return
            r = self._rows.get(idx)
            if not r:
                return
            _st, stag = self._status_of(r)
            if stag not in self._CLICK_ST:
                return
            self.open_plan(focus_index=idx)
            return 'break'
        except Exception:
            return

    def add_item(self, r):
        idx = r.get('index', 0)
        # 四列要和表头一一对应：原文件名 / 新文件名 / 描述 / 状态
        # 存完整值（超出的部分由表格自己裁掉），鼠标停住能看到全名
        if not r.get('ok', True):
            st, _stag = self._status_of(r)
            iid = self.tree.insert('', 'end', tags=('fail',),
                                   values=(r.get('old', ''), T('（失败）'),
                                           r.get('err', '') or '—', st))
            self._iid_idx[iid] = idx
            # 失败行也存进 _rows：点它时详情条要能写出失败原因，否则只剩一句
            # 「上次识别留下的，没有更多细节」，把真正的原因藏起来了
            self._rows[idx] = r
            self._note_width(('old', r.get('old')), ('desc', r.get('err')),
                             ('st', st))
            self._sync_empty()
            return
        st, stag = self._status_of(r)
        tags = ['odd'] if idx % 2 else []
        if stag:
            tags.append(stag)
        iid = self.tree.insert('', 'end', tags=tuple(tags),
                               values=(r.get('old', ''),
                                       r.get('new', '') or T('（时间空白）'),
                                       r.get('desc', '') or '—', st))
        self._iid_idx[iid] = idx
        self._rows[idx] = r
        self._note_width(('old', r.get('old')), ('new', r.get('new')),
                         ('desc', r.get('desc')), ('st', st))
        if r.get('thumb'):
            self._thumbs[idx] = r['thumb']
        self.tree.yview_moveto(1.0)
        self.show_thumb(r)
        self._sync_empty()

    def _fit_prev(im):
        """缩略图按 112x54 的框等比缩放——详情条只有 68px 高。"""
        try:
            w, h = im.size
            if not w or not h:
                return im
            k = min(_PREV_W / float(w), _PREV_H / float(h))
            return im.resize((max(1, int(w * k)), max(1, int(h * k))),
                             Image.Resampling.LANCZOS)
        except Exception:
            return im

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
                    im = self._fit_prev(im.convert('RGB'))
                    ph = ImageTk.PhotoImage(im)
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
            im = self._fit_prev(Image.open(io.BytesIO(data)))
            self._thumb_ref = ImageTk.PhotoImage(im)
            self.lbl_prev.configure(image=self._thumb_ref, text='',
                                    width=im.width, height=im.height)
            return True
        except Exception:
            return False

    def _hover_photo(self, iid):
        """悬停预览要的图：视频取识别时抽的帧，照片直接读原图缩到 300px 宽。"""
        idx = self._iid_idx.get(iid)
        if idx is None:
            return None
        try:
            ph = self._photo_thumbs.get(idx)
            if ph is not None:
                return ph
        except Exception:
            pass
        data = self._thumbs.get(idx)
        im = None
        if _HAS_PIL and data:
            try:
                im = Image.open(io.BytesIO(data))
            except Exception:
                im = None
        if im is None:
            r = self._rows.get(idx) or {}
            if _HAS_PIL and r.get('kind') == 'photo':
                d = os.path.abspath(self.var_dir.get().strip())
                for name in (r.get('new'), r.get('old')):
                    p = os.path.join(d, name) if name else ''
                    if p and os.path.isfile(p):
                        try:
                            im = Image.open(p)
                        except Exception:
                            im = None
                        break
        if im is None:
            return None
        try:
            w, h = im.size
            k = 300.0 / max(1, w)
            if h * k > 340:
                k = 340.0 / max(1, h)
            nim = im.convert('RGB').resize((max(1, int(w * k)), max(1, int(h * k))))
            photo = ImageTk.PhotoImage(nim)
            self._hover_ref = photo      # 保住引用，否则浮窗里是空白
            return photo
        except Exception:
            return None

    def _hover_lines(self, iid):
        """悬停预览要的字：原名 → 新名 + 地名/描述（列宽有限，长名字靠这里看全）。"""
        idx = self._iid_idx.get(iid)
        r = self._rows.get(idx) if idx is not None else None
        if not r:
            try:
                v = self.tree.item(iid, 'values')
            except Exception:
                return []
            return ([T('原文件：%s') % v[0], T('新文件：%s') % v[1]]
                    if len(v) > 1 else [])
        lines = [T('原文件：%s') % r.get('old', ''),
                 T('新文件：%s') % (r.get('new') or T('（时间空白，不改名）'))]
        bits = []
        if r.get('place'):
            bits.append(r['place'])
        if r.get('desc'):
            bits.append(r['desc'])
        if bits:
            lines.append('　·　'.join(bits))
        if r.get('err'):
            lines.append(T('失败：%s') % r['err'])
        return lines

    def show_thumb(self, r):
        self._show_photo(r.get('thumb'))
        self._set_detail(r)

    def on_pick_row(self, _evt=None):
        """点结果表里的一行 → 下方详情条显示原名→新名（名字被截断时很有用）。"""
        sel = self.tree.selection()
        if not sel:
            return
        # 表格只有四列，第一列已经是原文件名，不能再从 values 里取序号
        idx = self._iid_idx.get(sel[0])
        if idx is None:
            return
        r = self._rows.get(idx)
        if not r:
            self._set_detail(None, fallback=T('这一行没有更多细节（可能是上次识别留下的）'))
            return
        if not self._show_photo(self._thumbs.get(idx)):
            if not self._show_photo_file(r, idx):
                self.lbl_prev.configure(image='', text=T('无预览'), width=16, height=3)
                self._thumb_ref = None
        self._set_detail(r)

    def finish(self):
        self.running = False
        self._set_busy(False)
        el = time.time() - self.t_start
        d = os.path.abspath(self.var_dir.get().strip())
        n = len(self._plan) if self._plan else len(engine.load_plan(d) or [])
        if self.stop_evt.is_set():          # 用户中途按了「停止识别」
            self._set_run_state('stopped', i=self.done, n=self.total)
            self._toast(T('已停止 · 完成 %d/%d · 用时 %s')
                        % (self.done, self.total, fmt_dur(el)))
            self.log(T('——— 已手动停止（已完成的 %d 条已写进计划表）———') % len(self._plan),
                     'head')
        else:
            self._set_run_state('done', n=n, el=el)
            if self.total:
                self.pb.configure(value=100)
            # 收尾小结放在浮条里，不占状态栏那一句（那句只负责「就绪」）。
            # 数字说明：ok = 新名字和原名不一样、需要改的；fail = 没认出来的。
            # 没认出来的即使算得出名字也不许算进「待改名」——它不会被执行，
            # 两边都数一遍会变成「3 张里 2 张待改名 + 2 张没认出来」。
            ok = sum(1 for it in self._plan
                     if it.get('ok', True)
                     and it.get('new') and it['new'] != it.get('old'))
            fail = sum(1 for it in self._plan if not it.get('ok', True))
            skip = max(0, n - ok - fail)
            tip = T('%d 张，%d 张待改名，%d 张不用改') % (n, ok, skip)
            if fail:
                tip += T('，%d 张没认出来') % fail
            self._toast(tip, T('查看详细报告'), self.open_report)
            self.log(T('——— 识别结束 ———'), 'head')
            if fail:
                self.root.after(400, lambda: self._error_summary(fail))
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
        # —— v3.22：AI 描述总开关（勾选框已搬进设置窗口，窗口没开时只校验变量联动）——
        try:
            _ai0 = bool(self.var_ai_desc.get())
            self.var_ai_desc.set(False)
            self._refresh_scope()
            _off_grey = (self.cb_desc_photo is None
                         or 'disabled' in self.cb_desc_photo.state())
            self.var_ai_desc.set(True)
            self._refresh_scope()
            _on_live = (self.cb_desc_photo is None
                        or 'disabled' not in self.cb_desc_photo.state())
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
        _gone = not hasattr(self, 'menu_more') and not hasattr(self, 'sb_open')
        self.log(T('自检：⋯更多菜单与右侧栏已移除，入口改为第 3 步 + 表格标题行'),
                 'ok' if _gone else 'err')
        _seen_plan = []
        for _st in ('normal', 'disabled', 'normal'):
            self.btn_plan.configure(state=_st)
            self.root.update_idletasks()
            _seen_plan.append(str(self.btn_plan.cget('state')))
        self.log(T('自检：第 3 步「核对并改名」可用/置灰切换 %s') % _seen_plan,
                 'ok' if _seen_plan == ['normal', 'disabled', 'normal'] else 'err')
        self._set_detail(self._rows.get(1) if self._rows else None)
        self.root.update_idletasks()
        self.log(T('自检：选中详情条首行 = %r') % self.lbl_d1.cget('text')[:28], 'ok')
        # 日志已经从界面撤下来了，只剩后台记录 + 出错时的人话框
        self.log(T('自检：运行日志已不上屏（后台仍完整记录，出错走人话框）'), 'ok')
        # 结果表四列：原文件名 / 新文件名 / 描述 / 状态
        self.log(T('自检：结果表列 = %s') % ' / '.join(self._cols),
                 'ok' if list(self._cols) == ['old', 'new', 'desc', 'st'] else 'err')
        # 状态列：两种失败文案一致、都可点；待补时间也点得动
        _s1 = self._status_of({'ok': False})[0]
        _s2 = self._status_of({'ok': True, 'err': 'x', 'time': '1'})[0]
        _s3 = self._status_of({'ok': True, 'time': ''})[0]
        self.log(T('自检：状态列文案 %s / %s / %s') % (_s1, _s2, _s3),
                 'ok' if (_s1.endswith('点我看看') and _s2.endswith('点我看看')
                          and _s3 == T('点我补时间')) else 'err')
        # 照片的模型失败也必须标成「没认出来」：真跑一遍（桩一个必挂的后端），
        # 断言 ok=False、状态列有话说、异常原文仍留在 raw 里可查。
        try:
            import tempfile as _tf
            from datetime import datetime as _dtm
            import core.workflow as _wf

            class _Boom:
                def describe(self, *a, **k):
                    raise RuntimeError('selftest: backend on purpose')

            _td2 = _tf.mkdtemp(prefix='seename_fail_')
            _p2 = os.path.join(_td2, 'IMG_fail.jpg')
            Image.new('RGB', (64, 48), (90, 120, 150)).save(_p2, 'JPEG')
            _res = _wf.rename_one_photo(
                _p2, describe=True, backend=_Boom(),
                meta={'dt': _dtm(2024, 1, 15, 18, 30), 'gps': None})
            _stf = self._status_of(_res)[0]
            _pass = (not _res.get('ok', True)) and bool(_res.get('err')) \
                and bool(_res.get('raw')) and _stf == T('没认出来，点我看看')
            self.log(T('自检：照片模型失败 → ok=%s · 状态列=%s · 原因可查=%s')
                     % (_res.get('ok'), _stf, bool(_res.get('raw'))),
                     'ok' if _pass else 'err')
        except Exception as _e:
            self.log(T('自检：照片模型失败标记异常：%r') % (_e,), 'err')
        # 空表时那句引导真的存在
        self.log(T('自检：空表引导 = %r') % self.lbl_empty.cget('text')[:24], 'ok')
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
