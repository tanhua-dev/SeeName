# -*- coding: utf-8 -*-
"""显影 SeeName · 视频改名助手 · 图形界面（tkinter，零第三方 UI 依赖）。
作者：tanhua

自带推理运行时（runtime\\ollama）+ 自带模型库（models）：
用户不需要另外安装 Ollama，只要在本软件里下载一次模型包即可。

用本地视觉模型 qwen3-vl:4b-instruct 看视频抽帧，把手机拍的视频和照片
改成一眼能看懂的名字，例如：
  视20241220成都新场镇_林间散步.mp4
  图20250130南充周口.jpg
地名来自文件自带的 GPS：国内用内置县/区点位（3300 多个，离线反查到县级并带市名），
国外用内置全球城市库（精度到城市）；没有 GPS 就不标地名。描述不超过 9 个字（照片默认不写，秒级处理完）。
流程是两段式的：先「开始识别」——只出一张能改的计划表，文件名一个都不动；
再在「核对并改名」里改时间 / 地点 / 描述、点「应用改名」才真的落盘，
落盘后弹结果窗口（成功 / 未改动 / 失败各多少，失败列出原因），主界面表格有「状态」列。

界面分区（v3.16 起）——一眼只看到一个主要动作：
  标题栏（商标 / 产品名 / 副标题 / 作者）
   → 目录行 → 处理范围行 → 操作行（只有「▶ 开始识别」，运行时才多出一个「停止识别」）
   → 进度条 → 处理结果表 → 底部状态栏（●模型状态 ｜ 端口 ｜ 模型库 ｜ ⋯更多）
  右侧是可收起的侧栏：上半「模型看到的画面」，下半「⚙ 设置」。
次要动作全部收起来：核对改名 / 回滚 / 打开报告都收进底部「⋯更多」菜单，
不是每次都要动的参数（抽帧数、模型…）收进右侧「⚙ 设置」。

用法：双击「启动SeeName.bat」，或 python main.py
自检：python main.py --selftest   （构建全部控件后立即销毁，不开窗口）
"""
from __future__ import annotations

import io
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, simpledialog, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine  # noqa: E402
import strings  # noqa: E402
from strings import T  # noqa: E402


def BW(zh_w, en_w=None):
    """ttk 按钮宽度按「0」字符数计：英文比中文长得多，固定宽度会截断。
    中文用原值，英文默认 +6（个别按钮可显式给 en_w）。"""
    return zh_w if strings.lang() == 'zh' else (en_w or zh_w + 6)

try:
    from PIL import Image, ImageOps, ImageTk
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

APP_TITLE = '显影 SeeName'
# 副标题固定双语展示，不随界面语言切换
APP_SUB = '看见，即命名。/ See it. Name it.'
# tanhua 只作为作者/开发者署名保留，不再指代产品
APP_AUTHOR = '作者：tanhua' if strings.lang() == 'zh' else 'By tanhua'
APP_VER = 'v1.0'

# 侧栏宽度由预览图的固定宽度决定（缩略图统一缩到 _PREV_W，不会忽宽忽窄）
_PREV_W = 280


def _asset(name: str) -> str:
    """界面资源（logo 等）路径，兼容 PyInstaller 打包模式（含 macOS .app）。"""
    for r in engine._bundle_roots():
        p = os.path.join(r, 'assets', name)
        if os.path.exists(p):
            return p
    return ''


def _open_path(p: str):
    """在系统文件管理器里打开文件/文件夹：Windows startfile，macOS open，
    Linux xdg-open。都失败就安静返回（不算错误）。"""
    try:
        if os.name == 'nt':
            os.startfile(p)          # noqa: S606 - 仅本机用户主动点击时调用
        elif sys.platform == 'darwin':
            subprocess.run(['open', p], timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(['xdg-open', p], timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

# ---- 配色（浅色，克制的蓝灰） ----
# 视觉规范 v1：颜色对比度按 WCAG 2.1 相对亮度公式实算（白底，AA 要求 ≥4.5:1）
C_BG = '#f4f5f7'        # 画布
C_CARD = '#ffffff'      # 卡片 / 表格底
C_LINE = '#e3e6ea'      # 1px 描边、分隔线
C_TEXT = '#1f2329'      # 主文本          15.40:1
C_SUB = '#5f6672'       # 次要文本         5.78:1  （原 #7a828e 仅 3.88）
C_HINT = '#8a9099'      # 占位 / 禁用      3.60:1  （禁用态不要求 4.5）
C_ACCENT = '#1668dc'    # 主色             4.86:1（白字）
C_ACCENT_D = '#0f4fa8'  # 悬浮 hover       白字 7.77:1
C_ACCENT_P = '#0c3f88'  # 按下 pressed     白字 10.09:1
C_ACCENT_F = '#0a3675'  # 键盘聚焦描边
C_ACCENT_L = '#e8f1fd'  # 选中行 / 高亮底

# ---- 交互状态（视觉规范 v1 · 组件规格卡：默认/悬浮/按下/聚焦/禁用/加载中）----
# ⚠ clam 主题自带的描边是暖灰 #9e9a91、按钮底 #dcdad5，和这套冷灰蓝界面不搭，
#   下面把 TButton/TEntry/Combobox/Heading 的描边与高光全部换成冷灰系。
C_LINE_D = '#c2cad4'    # 控件描边（比分隔线 #e3e6ea 深一档，输入框才看得出边界）
C_BTN_H = '#eef2f7'     # 次级按钮 悬浮：与白底差 1.12 倍
C_BTN_P = '#dbe3ef'     # 次级按钮 按下：与白底差 1.29 倍
C_DIS_BG = '#eef0f3'    # 禁用底
C_DIS_FG = '#6f7681'    # 禁用字  4.01:1（禁用态不强制 4.5，但尽量看清）
C_BUSY_BG = '#cddff7'   # 主按钮「加载中」底
C_BUSY_FG = '#0f4fa8'   # 主按钮「加载中」字  5.73:1
                        # （原来禁用态是浅灰底+白字，只有 1.31:1——运行中那行
                        #   「识别中 12/289」根本看不清，等于没有加载状态）
C_OK = '#0f7b33'        # 成功文字         5.38:1  （原 #17a34a 仅 3.30）
C_WARN = '#a15c00'      # 警告文字         5.19:1  （原 #d98400 仅 2.90）
C_ERR = '#d92d20'       # 错误文字         4.83:1
C_ROW_ALT = '#eaeff7'   # 表格斑马纹：与白底差 1.16 倍（原 #fafbfc 仅 1.04，等于没有）
C_LOG_BG = '#1e2430'    # 日志区深色底（文字用下面独立的亮色体系）
C_LOG_FG = '#c7ccd6'    #                  9.66:1
C_BAR = '#eceff4'       # 底部状态栏：比画布深半档，看得出是一条独立带
C_BAR_S = '#c8cfd9'     # 状态栏里的竖分隔线

# 8pt 间距栅格：把原来 4/6/7/9/10/12/14 七种值收敛成五档
S1, S2, S3, S4, S5 = 4, 8, 12, 16, 24

FONT = 'Microsoft YaHei UI' if os.name == 'nt' else 'PingFang SC'


class Tip:
    """鼠标在表格里停住一下，弹出这一行的完整文件名（列宽有限，长名字会被裁掉）。"""

    def __init__(self, master, tree, delay=550):
        self.master = master
        self.tree = tree
        self.delay = delay
        self.win = None
        self.job = None
        tree.bind('<Motion>', self._move)
        tree.bind('<Leave>', lambda e: self.hide())
        tree.bind('<ButtonPress>', lambda e: self.hide())

    def hide(self):
        if self.job:
            try:
                self.tree.after_cancel(self.job)
            except Exception:
                pass
            self.job = None
        if self.win:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None

    def _move(self, ev):
        self.hide()
        iid = self.tree.identify_row(ev.y)
        self.job = self.tree.after(self.delay,
                                   lambda: self._show(ev.x_root, ev.y_root, iid))

    def _show(self, x, y, iid):
        try:
            if not iid or not self.tree.exists(iid):
                return
            v = self.tree.item(iid, 'values')
            if len(v) < 3:
                return
            lines = [T('原文件：%s') % v[1], T('新文件：%s') % v[2]]
            if len(v) > 4 and v[3] not in ('', '—'):
                lines.append(T('地名：%s\u3000描述：%s') % (v[3], v[4]))
            if len(v) > 6 and v[6]:
                lines.append(T('状态：%s') % v[6])
            w = tk.Toplevel(self.master)
            w.wm_overrideredirect(True)
            w.wm_geometry('+%d+%d' % (x + 14, y + 18))
            tk.Label(w, text='\n'.join(lines), justify='left', bg='#fffbe6', fg=C_TEXT,
                     font=(FONT, 9), relief='solid', borderwidth=1,
                     wraplength=560).pack(padx=6, pady=5)
            self.win = w
        except Exception:
            pass


class Hover:
    """鼠标悬停显示完整文字。

    状态栏的模型库路径会被截断（窗口就这么宽），完整路径靠悬停看；
    模型状态圆点也用它显示详情（服务虽在跑但模型还没下载等）。
    """

    def __init__(self, widget, get_text, delay=350):
        self.w = widget
        self.get = get_text
        self.delay = delay
        self.job = None
        self.win = None
        widget.bind('<Enter>', self._in)
        widget.bind('<Leave>', self._out)
        widget.bind('<ButtonPress>', self._out)

    def _in(self, _e=None):
        self._cancel()
        self.job = self.w.after(self.delay, self._show)

    def _cancel(self):
        if self.job:
            try:
                self.w.after_cancel(self.job)
            except Exception:
                pass
            self.job = None

    def _out(self, _e=None):
        self._cancel()
        if self.win:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None

    def _show(self):
        try:
            txt = (self.get() or '').strip()
            if not txt:
                return
            self.win = w = tk.Toplevel(self.w)
            w.wm_overrideredirect(True)
            px, py = self.w.winfo_pointerxy()
            tk.Label(w, text=txt, justify='left', bg='#fffbe6', fg=C_TEXT,
                     font=(FONT, 9), relief='solid', borderwidth=1,
                     wraplength=620).pack(padx=6, pady=5)
            w.update_idletasks()
            # 靠右界线下方：越界就翻到左边，免得 tooltip 被屏幕边裁掉
            x = min(px + 14, max(0, self.w.winfo_screenwidth() - w.winfo_width() - 8))
            y = min(py + 18, max(0, self.w.winfo_screenheight() - w.winfo_height() - 8))
            w.wm_geometry('+%d+%d' % (x, y))
        except Exception:
            pass


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_evt = threading.Event()
        self.running = False
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
        root.configure(bg=C_BG)
        self._icons = []                # 防止图标 PhotoImage 被 GC
        p = _asset('logo_round.png')
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
        """窗口一开就把自带推理运行时拉起来（用户不需要装 Ollama）。"""
        threading.Thread(target=self._autostart_bg, daemon=True).start()

    def _autostart_bg(self):
        try:
            rs = engine.runtime_status()
            self.q.put(('log', T('推理运行时：%s（%s）')
                        % (rs['exe'] or T('未找到'), rs['label']), 'dim'))
            self.q.put(('log', T('模型库：%s') % rs['models_dir'], 'dim'))
            if rs['running']:
                self.q.put(('log', T('推理服务已在线'), 'ok'))
            elif not rs['found']:
                self.q.put(('log', T('未找到自带运行时，请确认软件目录里的 runtime 文件夹完整'), 'err'))
            else:
                ok = engine.start_server(on_log=lambda s: self.q.put(('log', s, 'dim')))
                self.q.put(('log', T('推理服务已就绪') if ok else T('推理服务启动失败'),
                            'ok' if ok else 'err'))
        except Exception:
            self.q.put(('log', traceback.format_exc(), 'err'))
        self.q.put(('srv', None, None))

    # ------------------------------------------------------------ 样式
    def _setup_style(self):
        global FONT
        try:
            fams = set(self.root.tk.call('font', 'families'))
            for cand in ('Microsoft YaHei UI', 'Microsoft YaHei', 'SimHei',
                         'Segoe UI', 'PingFang SC', 'Songti SC',
                         'Helvetica Neue', 'Helvetica'):
                if cand in fams:
                    FONT = cand
                    break
        except Exception:
            pass
        st = ttk.Style()
        try:
            st.theme_use('clam')
        except Exception:
            pass
        st.configure('.', background=C_BG, foreground=C_TEXT, font=(FONT, 10))
        st.configure('Card.TFrame', background=C_CARD)
        st.configure('Card.TLabel', background=C_CARD, foreground=C_TEXT)
        st.configure('Sub.TLabel', background=C_CARD, foreground=C_SUB, font=(FONT, 9))
        st.configure('Head.TLabel', background=C_CARD, foreground=C_TEXT, font=(FONT, 10, 'bold'))
        st.configure('Title.TLabel', background=C_BG, foreground=C_TEXT, font=(FONT, 14, 'bold'))
        st.configure('Ver.TLabel', background=C_BG, foreground=C_SUB, font=(FONT, 9))
        # ---- 按钮：默认 / 悬浮 / 按下 / 聚焦 / 禁用 / 加载中 ----
        # 默认改白底 + 冷灰描边（原来用 clam 自带的暖灰 #dcdad5/#9e9a91）
        for bs in ('TButton', 'Sec.TButton'):
            st.configure(bs, padding=(12, 8), font=(FONT, 10),
                         background=C_CARD, bordercolor=C_LINE_D,
                         lightcolor=C_CARD, darkcolor='#cfd6e0')
            st.map(bs,
                   background=[('disabled', C_DIS_BG), ('pressed', C_BTN_P),
                               ('active', C_BTN_H)],
                   foreground=[('disabled', C_DIS_FG)],
                   bordercolor=[('disabled', C_LINE_D), ('focus', C_ACCENT),
                                ('active', C_ACCENT_D)])
        # 主按钮：禁用态就是「加载中」——识别过程中按钮上写着「识别中 12/289」，
        # 灰掉会看不见，所以禁用改浅蓝底 + 深蓝字（5.73:1）
        st.configure('Go.TButton', padding=(28, 10), font=(FONT, 11, 'bold'),
                     background=C_ACCENT, foreground='#ffffff',
                     bordercolor=C_ACCENT_D, lightcolor='#4a8ae8',
                     darkcolor=C_ACCENT_D)
        st.map('Go.TButton',
               background=[('disabled', C_BUSY_BG), ('pressed', C_ACCENT_P),
                           ('active', C_ACCENT_D)],
               foreground=[('disabled', C_BUSY_FG)],
               bordercolor=[('disabled', '#a8c2ec'), ('focus', C_ACCENT_F),
                            ('pressed', C_ACCENT_P), ('active', C_ACCENT_D)])

        # ---- 输入框 / 下拉框：默认 / 悬浮 / 聚焦 / 禁用 ----
        for es in ('TEntry', 'TSpinbox'):
            st.configure(es, padding=5, fieldbackground=C_CARD,
                         bordercolor=C_LINE_D, lightcolor=C_CARD,
                         darkcolor='#cfd6e0', insertcolor=C_TEXT)
            st.map(es,
                   bordercolor=[('disabled', C_LINE_D), ('focus', C_ACCENT)],
                   fieldbackground=[('disabled', C_DIS_BG)],
                   foreground=[('disabled', C_DIS_FG)])
        st.configure('TCombobox', padding=5, fieldbackground=C_CARD,
                     bordercolor=C_LINE_D, lightcolor=C_CARD,
                     darkcolor='#cfd6e0', arrowcolor=C_SUB)
        st.map('TCombobox',
               bordercolor=[('disabled', C_LINE_D), ('focus', C_ACCENT),
                            ('active', C_ACCENT_D)],
               fieldbackground=[('readonly', C_CARD), ('disabled', C_DIS_BG)],
               foreground=[('disabled', C_DIS_FG)])

        # ---- 勾选 / 单选：描边统一，选中块的勾改主色 ----
        for cs in ('TCheckbutton', 'TRadiobutton', 'Scope.TCheckbutton'):
            st.configure(cs, background=C_CARD, foreground=C_TEXT,
                         indicatorcolor=C_ACCENT, indicatorbackground=C_CARD,
                         bordercolor=C_CARD)
            st.map(cs,
                   background=[('active', C_CARD), ('disabled', C_CARD)],
                   foreground=[('disabled', C_DIS_FG)],
                   indicatorcolor=[('disabled', C_LINE_D)])
        # 勾选框：加大一号，让「处理范围」一眼可见可点
        st.configure('Scope.TCheckbutton', font=(FONT, 11))

        # ---- 表格 ----
        # 行高 24→28：9pt（12px）中文上下各留 8px，不再挤
        st.configure('Treeview', rowheight=28, background=C_CARD,
                     fieldbackground=C_CARD, font=(FONT, 9), borderwidth=0,
                     bordercolor=C_LINE)
        st.configure('Treeview.Heading', font=(FONT, 9, 'bold'),
                     background=C_BG, bordercolor=C_LINE,
                     lightcolor=C_CARD, darkcolor=C_LINE)
        st.map('Treeview.Heading', background=[('active', '#e4eaf3')])
        # 选中行改成浅蓝底 + 深色字：系统默认的深蓝白字在浅色界面里很跳
        st.map('Treeview',
               background=[('selected', C_ACCENT_L)],
               foreground=[('selected', C_TEXT)])

        # ---- 进度条 / 分组框 / 滚动条：去掉 clam 的暖灰残留 ----
        st.configure('TProgressbar', thickness=8, background=C_ACCENT,
                     troughcolor=C_LINE, bordercolor=C_LINE,
                     lightcolor=C_ACCENT, darkcolor=C_ACCENT)
        st.configure('TLabelframe', background=C_CARD, bordercolor=C_LINE,
                     lightcolor=C_CARD, darkcolor=C_LINE)
        st.configure('TLabelframe.Label', background=C_CARD, foreground=C_SUB,
                     font=(FONT, 10, 'bold'))
        st.configure('TScrollbar', background=C_BG, bordercolor=C_BG,
                     troughcolor=C_BG, arrowcolor=C_SUB,
                     lightcolor=C_BG, darkcolor='#d5d9de')
        st.map('TScrollbar', background=[('active', C_LINE_D)])

        # ---- 底部状态栏：默认底更深，竖分隔线要跟着换色 ----
        st.configure('Bar.TFrame', background=C_BAR)
        st.configure('Bar.TLabel', background=C_BAR, foreground=C_SUB, font=(FONT, 9))
        st.configure('Bar.TSeparator', background=C_BAR_S)

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
                 bg=C_BG, fg=C_SUB, font=(FONT, 8)).pack(side='bottom', fill='x',
                                                         padx=14, pady=(0, 4))

        # 主区：左列（控制 + 结果 + 日志抽屉），右侧栏（预览 + 设置）。
        # 侧栏**先**入队：窗口再窄它也保得住自己的宽度，被裁的只会是结果表。
        body = tk.Frame(self.root, bg=C_BG)
        body.pack(fill='both', expand=True, padx=14, pady=(0, S2))
        left = tk.Frame(body, bg=C_BG)
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
        f = tk.Frame(parent, bg=C_CARD, highlightbackground=C_LINE,
                     highlightthickness=1)
        if title:
            tk.Label(f, text=title, bg=C_CARD, fg=C_SUB,
                     font=(FONT, 10, 'bold')).pack(anchor='w', padx=S4, pady=(S2 + 1, 0))
        return f

    def _build_head(self):
        """标题栏：商标 + 产品名 + 副标题/作者。

        原来这行右边还挂着「自带运行时 · 端口 · 模型库」和模型服务状态，太像调试信息，
        v3.16 起统一挪到底部状态栏。
        """
        head = tk.Frame(self.root, bg=C_BG)
        head.pack(fill='x', padx=14, pady=(12, 6))
        p = _asset('logo_head.png')
        if p:
            try:
                lg = tk.PhotoImage(file=p)
                self._icons.append(lg)
                tk.Label(head, image=lg, bg=C_BG).pack(side='left', padx=(0, 9))
            except Exception:
                pass
        tf = tk.Frame(head, bg=C_BG)
        tf.pack(side='left')
        tk.Label(tf, text=APP_TITLE, bg=C_BG, fg=C_TEXT,
                 font=(FONT, 15, 'bold')).pack(anchor='w')
        tk.Label(tf, text='%s　%s' % (APP_SUB, APP_AUTHOR), bg=C_BG,
                 fg=C_SUB, font=(FONT, 9)).pack(anchor='w')
        # 使用说明：标题栏右侧常驻，第一次启动还会自己弹一次（见 _maybe_help）
        ttk.Button(head, text=T('❓ 使用说明'), command=self.open_help,
                   width=BW(12, 16)).pack(side='right', pady=(4, 0))

    def _build_control(self, parent):
        card = self._card(parent)
        card.pack(fill='x')
        inner = tk.Frame(card, bg=C_CARD)
        inner.pack(fill='x', padx=12, pady=10)

        # —— 第一行：目录（右边的「浏览… / 预设」先从右缘占位，窄窗口也不会被挤出去）
        r1 = tk.Frame(inner, bg=C_CARD)
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
        tk.Label(r1, text=T('目录'), bg=C_CARD, fg=C_TEXT,
                 font=(FONT, 10)).pack(side='left')
        self.var_dir = tk.StringVar(
            value=(list(engine.PRESETS.values())[0] if engine.PRESETS else ''))
        # 换了目录，上一次的运行状态就不算数了，回到「未开始」
        self.var_dir.trace_add('write', lambda *a: self._on_dir_change())
        self.ent_dir = ttk.Entry(r1, textvariable=self.var_dir, font=(FONT, 10))
        self.ent_dir.pack(side='left', fill='x', expand=True, padx=8)

        # —— 第二行：处理范围（视频 / 照片 各自独立勾选）
        r2 = tk.Frame(inner, bg=C_CARD)
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
        r3 = tk.Frame(inner, bg=C_CARD)
        r3.pack(fill='x', pady=(12, 0))
        self.btn_go = ttk.Button(r3, text=T('▶ 开始识别'), style='Go.TButton',
                                 command=self.on_go)
        self.btn_go.pack(side='left')
        self.btn_stop = ttk.Button(r3, text=T('停止识别'), style='Sec.TButton',
                                   width=BW(10, 9), command=self.on_stop,
                                   state='disabled')
        self.btn_stop.pack_forget()

        # —— 第四行：进度（左：细进度；右：本轮状态灯）
        r4 = tk.Frame(inner, bg=C_CARD)
        r4.pack(fill='x', pady=(12, 0))
        self.pb = ttk.Progressbar(r4, mode='determinate', maximum=100)
        self.pb.pack(fill='x')
        r4b = tk.Frame(r4, bg=C_CARD)
        r4b.pack(fill='x', pady=(4, 0))
        self.lbl_prog = tk.Label(r4b, text=T('待处理'), bg=C_CARD, fg=C_SUB,
                                 font=(FONT, 9), anchor='w')
        self.lbl_prog.pack(side='left')
        # 状态灯：说清现在是「没开始 / 正在跑 / 已停 / 跑完了」，
        # 光看按钮的禁用状态分不出来（v3.4 反馈）
        self.lbl_state = tk.Label(r4b, text=T('● 未开始'), bg=C_CARD, fg=C_SUB,
                                  font=(FONT, 10, 'bold'), anchor='e')
        self.lbl_state.pack(side='right')

    # ------------------------------------------------------------ 结果表
    def _build_table(self, parent):
        card = self._card(parent)
        card.pack(fill='both', expand=True, pady=(10, 0))
        tk.Label(card, text=T('处理结果'), bg=C_CARD, fg=C_SUB,
                 font=(FONT, 10, 'bold')).pack(anchor='w', padx=S4, pady=(S2 + 1, S1))

        cols = ('no', 'old', 'new', 'place', 'desc', 'sec', 'st')
        heads = ('#', T('原文件名'), T('新文件名'), T('地名'), T('描述'), T('耗时'), T('状态'))
        # 文件名列固定宽（名字是等宽可预估的），窗口拉宽时多余空间全给地名/描述
        widths = (36, 172, 196, 116, 108, 46, 56)
        wrap = tk.Frame(card, bg=C_CARD)
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
        self.tree.tag_configure('odd', background=C_ROW_ALT)
        self.tree.tag_configure('fail', foreground=C_ERR)
        self.tree.tag_configure('st_ok', background='#eefaf1')
        self.tree.tag_configure('st_skip', foreground=C_SUB)
        self.tree.tag_configure('st_fail', background='#fdeceb')
        self.tree.bind('<<TreeviewSelect>>', self.on_pick_row)
        Tip(self.root, self.tree)

    # ------------------------------------------------------------ 运行日志（可折叠）
    def _build_log_drawer(self, parent):
        """日志默认收起成一条窄条，出错时自动弹开（见 log()）。"""
        self.var_log = tk.BooleanVar(value=self._sw('logopen', False))
        card = self._card(parent)
        card.pack(fill='x', pady=(10, 0))
        self.log_head = tk.Frame(card, bg=C_CARD, cursor='hand2')
        self.log_head.pack(fill='x', padx=12, pady=(8, 6))
        self.lbl_log = tk.Label(self.log_head, text='', bg=C_CARD, fg=C_SUB,
                                font=(FONT, 10, 'bold'), cursor='hand2')
        self.lbl_log.pack(side='left')
        self.log_tools = tk.Frame(self.log_head, bg=C_CARD)
        ttk.Button(self.log_tools, text=T('清空'), width=BW(6, 8),
                   command=lambda: self.txt.delete('1.0', 'end')).pack(side='right')
        ttk.Button(self.log_tools, text=T('复制全部'), width=BW(8, 10),
                   command=self.copy_log).pack(side='right', padx=6)
        for w in (self.log_head, self.lbl_log):
            w.bind('<Button-1>', lambda e: self.toggle_log())

        self.log_body = tk.Frame(card, bg=C_CARD)
        wrap = tk.Frame(self.log_body, bg=C_CARD)
        wrap.pack(fill='both', expand=True, padx=12, pady=(0, 10))
        self.txt = tk.Text(wrap, height=7, bg=C_LOG_BG, fg=C_LOG_FG,
                           insertbackground=C_LOG_FG, font=('Consolas', 9),
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
        sb = tk.Frame(parent, bg=C_BG)
        sb.pack(side='right', fill='y', padx=(10, 0))

        # —— 收起态：一条窄边 + 一个 ⚙ 按钮
        self.sb_closed = tk.Frame(sb, bg=C_CARD)
        b = ttk.Button(self.sb_closed, text='⚙', width=3,
                       command=lambda: self._side_apply(True))
        b.pack(padx=4, pady=(4, 2))
        Hover(b, lambda: T('展开侧栏（预览 + 设置）'))
        tk.Label(self.sb_closed, text=T('设置'), bg=C_CARD, fg=C_SUB,
                 font=(FONT, 8)).pack(pady=(0, 6))

        # —— 展开态：整条侧栏共用一个滚动容器（预览在上、设置在下，
        #    以后设置再加也不会把预览挤出窗口）
        self.sb_open = tk.Frame(sb, bg=C_BG)
        outer, inner, cv = self._scroller(self.sb_open, bg=C_BG)
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
    def _scroller(parent, bg=C_CARD, width=316):
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
        head = tk.Frame(card, bg=C_CARD)
        head.pack(fill='x', padx=12, pady=(9, 0))
        tk.Label(head, text=T('模型看到的画面'), bg=C_CARD, fg=C_SUB,
                 font=(FONT, 10, 'bold')).pack(side='left')
        b = ttk.Button(head, text='»', width=3,
                       command=lambda: self._side_apply(False))
        b.pack(side='right')
        Hover(b, lambda: T('收起侧栏'))
        self.lbl_prev = tk.Label(card, text=T('届时显示抽帧缩略图'), bg='#eef1f5',
                                 fg=C_SUB, font=(FONT, 9), width=34, height=9)
        self.lbl_prev.pack(padx=12, pady=(6, 6), fill='x')
        self.lbl_prev_info = tk.Label(card, text='', bg=C_CARD, fg=C_TEXT,
                                      font=(FONT, 9), wraplength=320, justify='left')
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
        head = tk.Frame(card, bg=C_CARD)
        head.pack(fill='x', padx=S4, pady=(S2 + 1, S1))
        tk.Label(head, text=T('⚙ 设置'), bg=C_CARD, fg=C_SUB,
                 font=(FONT, 10, 'bold')).pack(side='left')

        body = tk.Frame(card, bg=C_CARD)
        body.pack(fill='x', padx=S4, pady=(0, 10))

        # ① 抽帧数
        self.var_frames = tk.StringVar(value=T('自动'))
        self.cmb_frames = ttk.Combobox(self._set_row(body, T('抽帧数')),
                                       textvariable=self.var_frames,
                                       width=BW(7, 9), state='readonly',
                                       values=[T('自动'), '2', '4', '6', '8'])
        self.cmb_frames.pack(side='right')

        # ② 最多处理个数
        r = self._set_row(body, T('最多处理个数'))
        tk.Label(r, text=T('个（0=全部）'), bg=C_CARD, fg=C_HINT,
                 font=(FONT, 8)).pack(side='right')
        self.var_limit = tk.StringVar(value='0')
        self.sp_limit = ttk.Spinbox(r, from_=0, to=100000,
                                    textvariable=self.var_limit, width=BW(6, 7))
        self.sp_limit.pack(side='right', padx=(0, 5))

        # ③ 重新处理已改过名的
        self.var_all = tk.BooleanVar(value=False)
        self.cb_all = ttk.Checkbutton(body, text=T('重新处理已改过名的'),
                                      variable=self.var_all)
        self.cb_all.pack(fill='x', anchor='w', pady=(0, 7))

        # ④ 模型（下拉 + 模型管理）
        self.var_model = tk.StringVar(value=engine.MODEL)
        self.cb_model = ttk.Combobox(self._set_row(body, T('模型')),
                                     textvariable=self.var_model,
                                     width=BW(20, 21), state='readonly',
                                     values=[engine.MODEL])
        self.cb_model.pack(side='right')
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
            body, bg=C_CARD, fg=C_SUB, font=(FONT, 8), anchor='w', justify='left',
            wraplength=250,
            text=T('开启后将在有网时调用在线地图接口获取更精确地址，会产生网络请求'))
        self.lbl_netgeo.pack(fill='x', pady=(1, 7))

        r = self._set_row(body, T('高德 API Key'))
        self.var_amap = tk.StringVar(
            value=str((engine.config() or {}).get('amap_key') or ''))
        self.ent_amap = ttk.Entry(r, textvariable=self.var_amap, width=BW(17, 19))
        self.ent_amap.pack(side='right')
        self.var_amap.trace_add('write', lambda *a: self._save_amap())
        self.lbl_amap = tk.Label(
            body, bg=C_CARD, fg=C_SUB, font=(FONT, 8), anchor='w', justify='left',
            wraplength=250,
            text=T('国内坐标用它查街道级地址，去 lbs.amap.com 免费申请；不填就一直走离线库'))
        self.lbl_amap.pack(fill='x', pady=(1, 7))

        self.var_geolv = tk.StringVar(
            value=T('到街道') if (engine.config() or {}).get('geo_level') == 'street'
            else T('到区县'))
        self.cmb_geolv = ttk.Combobox(
            self._set_row(body, T('地址粒度')), textvariable=self.var_geolv,
            width=BW(8, 10), state='readonly',
            values=[T('到区县'), T('到街道')])
        self.cmb_geolv.pack(side='right')
        self.cmb_geolv.bind('<<ComboboxSelected>>', self._on_geolv)

        # ⑦ 描述落盘（三个独立开关，默认全开）
        ttk.Separator(body, orient='horizontal').pack(fill='x', pady=(2, 8))
        self.var_meta = tk.BooleanVar(value=self._sw('write_meta', True))
        ttk.Checkbutton(body, text=T('写入文件元数据'),
                        variable=self.var_meta,
                        command=lambda: self._save_sw('write_meta', self.var_meta)
                        ).pack(fill='x', anchor='w')
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
        tk.Label(body, bg=C_CARD, fg=C_SUB, font=(FONT, 8),
                 anchor='w', justify='left', wraplength=250,
                 text=T('元数据写进文件本身（照片 EXIF/XMP、视频容器标签），'
                        '拷贝到任何地方都带着走；三个开关各管一件事，关掉就不生成。')
                 ).pack(fill='x', pady=(1, 7))

        # ⑧ 语言
        self.var_lang = tk.StringVar(
            value=T('中文') if strings.lang() == 'zh' else 'English')
        self.cmb_lang = ttk.Combobox(self._set_row(body, T('语言')),
                                     textvariable=self.var_lang,
                                     width=BW(8, 10), state='readonly',
                                     values=[T('中文'), 'English'])
        self.cmb_lang.pack(side='right')
        self.cmb_lang.bind('<<ComboboxSelected>>', self._on_lang_pick)

        # 处理范围行的耗时说明原来挤在勾选框右边，v3.18 起放到设置区底部当脚注：
        # 状态栏已经塞了模型圆点/端口/模型库路径，底部更挤，而这里本来就有空。
        # 文字会跟着勾选变化（_refresh_scope），所以用 wraplength 允许折行。
        self.lbl_scope = tk.Label(body, text='', bg=C_CARD, fg=C_SUB,
                                  font=(FONT, 8), anchor='w', justify='left',
                                  wraplength=250)
        self.lbl_scope.pack(fill='x', pady=(4, 0))

        # ↑↑ 以后要加新开关，就往这行上面接着塞一行（放不下会自动出滚动条）

    # ------------------------------------------------------------ 新增开关的落盘
    def _save_sw(self, key, var):
        """布尔开关：勾选/取消都立刻写进 config.json（下次打开还是这个状态）。"""
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
        r = tk.Frame(parent, bg=C_CARD)
        r.pack(fill='x', pady=(0, 7))
        tk.Label(r, text=label, bg=C_CARD, fg=C_TEXT,
                 font=(FONT, 10)).pack(side='left')
        return r

    # ------------------------------------------------------------ 底部状态栏
    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=C_BAR)
        bar.pack(side='bottom', fill='x')

        # ● 模型状态：点它 = 启动模型服务（原来头顶那颗「启动模型服务」按钮）
        self.dot_srv = tk.Label(bar, text=T('● 模型未启动'), bg=C_BAR, fg=C_HINT,
                                font=(FONT, 9), cursor='hand2')
        self.dot_srv.pack(side='left', padx=(14, 0), pady=5)
        self.dot_srv.bind('<Button-1>', lambda e: self.on_start_srv())
        self._srv_hint = T('点这里启动模型服务')
        Hover(self.dot_srv, lambda: self._srv_hint)

        ttk.Separator(bar, orient='vertical',
                      style='Bar.TSeparator').pack(side='left', fill='y',
                                                   padx=10, pady=4)
        self.lbl_port = tk.Label(bar, text='', bg=C_BAR, fg=C_SUB, font=(FONT, 9))
        self.lbl_port.pack(side='left', pady=5)
        self._port_hint = ''
        Hover(self.lbl_port, lambda: self._port_hint)

        ttk.Separator(bar, orient='vertical',
                      style='Bar.TSeparator').pack(side='left', fill='y',
                                                   padx=10, pady=4)
        self.lbl_models = tk.Label(bar, text='', bg=C_BAR, fg=C_SUB,
                                   font=(FONT, 9))
        self.lbl_models.pack(side='left', pady=5)
        self._models_full = ''
        Hover(self.lbl_models, lambda: self._models_full)
        # 先按本地配置摆上端口和模型库路径（服务探测是后台线程，晚几秒才回来）
        try:
            self.lbl_port.configure(text=T('端口 %d') % engine.server_port())
            md = engine.models_dir()
            self.lbl_models.configure(text=T('模型库 %s') % cut(md, 34))
            self._models_full = md
        except Exception:
            pass

        # ⋯ 更多：核对改名 / 回滚 / 打开目录 / 打开报告 / 打开台账 / 模型管理 / 日志
        self.btn_more = tk.Menubutton(bar, text=T('⋯ 更多'), relief='flat',
                                      bg=C_BAR, fg=C_SUB, font=(FONT, 9),
                                      padx=10, pady=2, direction='above',
                                      activebackground=C_BTN_H,
                                      activeforeground=C_TEXT)
        self.menu_more = tk.Menu(self.btn_more, tearoff=0, bg=C_CARD, fg=C_TEXT,
                                 activebackground=C_ACCENT_L,
                                 activeforeground=C_TEXT, borderwidth=1,
                                 relief='solid', font=(FONT, 9))
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
            self.lbl_state.configure(text=T('● 未开始'), fg=C_SUB)
        elif s == 'running':
            go.configure(text=T('识别中…'), state='disabled')
            self._show_stop(True)
            stop.configure(text=T('停止识别'), state='normal')
            self.lbl_state.configure(text=T('● 识别中…'), fg=C_ACCENT)
        elif s == 'stopping':
            go.configure(text=T('正在停止…'), state='disabled')
            self._show_stop(True)
            stop.configure(text=T('停止中…'), state='disabled')
            self.lbl_state.configure(text=T('● 正在停止…'), fg=C_WARN)
        elif s == 'stopped':
            go.configure(text=T('▶ 重新开始识别'), state='normal')
            stop.configure(text=T('停止识别'), state='disabled')
            self._show_stop(False)
            self.lbl_state.configure(text=T('● 已手动停止 · 完成 %d/%d')
                                     % (kw.get('i', self.done), kw.get('n', self.total)),
                                     fg=C_WARN)
        elif s == 'done':
            go.configure(text=T('↻ 再识别一次'), state='normal')
            stop.configure(text=T('停止识别'), state='disabled')
            self._show_stop(False)
            tail = (T(' · 用时 %s') % fmt_dur(kw['el'])) if kw.get('el') else ''
            self.lbl_state.configure(text=T('● 识别完成 · %d 条%s')
                                     % (kw.get('n', len(self._plan)), tail), fg=C_OK)

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
            self.lbl_scope.configure(text=T('两个都没勾选 · 不会处理任何文件'), fg=C_ERR)
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
        self.lbl_scope.configure(text='　·　'.join(bits), fg=C_SUB)

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
            _open_path(d)
        else:
            messagebox.showwarning(APP_TITLE, T('目录不存在'))

    def open_report(self):
        """识别阶段写的对照报告（改名结果不写在这里，改名看台账）。"""
        sp = engine.state_paths(self.var_dir.get())
        p = sp['report']
        if os.path.exists(p):
            _open_path(p)
        else:
            _open_path(sp['dir'])

    def open_ledger(self):
        """改名台账：新文件名 → 手机原始名，「回滚本目录改名」就是按它还原的。"""
        sp = engine.state_paths(self.var_dir.get())
        p = sp['ledger']
        if os.path.exists(p):
            _open_path(p)
        else:
            messagebox.showinfo(APP_TITLE, T('这个目录还没有改名记录。\n' + '台账在「核对并改名」里点过「应用改名」之后才会生成。'))

    def on_start_srv(self):
        """启动模型服务。入口在底部状态栏那个 ●圆点（点它就是本方法）。"""
        try:
            self.dot_srv.configure(text=T('● 模型启动中…'), fg=C_ACCENT,
                                   cursor='arrow')
        except Exception:
            pass
        self.log(T('正在启动 Ollama 模型服务…'), 'head')
        threading.Thread(target=self._start_srv_bg, daemon=True).start()

    def _start_srv_bg(self):
        ok = engine.start_server(on_log=lambda s: self.q.put(('log', s, None)))
        if ok:
            self.q.put(('log', T('模型服务已就绪，正在预热 %s…') % self.var_model.get(), 'ok'))
            wok, _err = engine.warmup(self.var_model.get().strip() or engine.MODEL)
            self.q.put(('log', T('模型已加载进显存') if wok
                        else T('模型预热失败（不影响使用，识别时会自动重试）'),
                        'ok' if wok else 'dim'))
        else:
            self.q.put(('log', T('模型服务启动失败'), 'err'))
        self.q.put(('srv', None, None))

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
        if self.running:
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
                    T('视觉模型「%s」还没有下载到本机。\n\n' + '现在打开「模型管理」下载它吗？\n（模型文件约 3–8 GB，只下载一次）') % model):
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
        models = engine.list_models() if info.get('up') else []
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
        """刷新底部状态栏：●模型状态 ｜ 端口 ｜ 模型库（完整路径靠悬停看）。"""
        self._models = models or []
        if rt:
            self.lbl_port.configure(text=T('端口 %d') % rt['port'])
            self._port_hint = T('%s\n%s') % (rt['label'], rt['url'])
            self.lbl_models.configure(text=T('模型库 %s') % cut(rt['models_dir'], 34))
            self._models_full = rt['models_dir']
        if models:
            cur = self.cb_model.get()
            vals = list(models)
            if engine.MODEL not in vals:
                vals.insert(0, engine.MODEL)
            self.cb_model.configure(values=vals)
            if cur not in vals:
                self.cb_model.set(engine.MODEL if engine.MODEL in vals else vals[0])
        dot = self.dot_srv
        if not info.get('up'):
            # 灰点 + 点它就能拉起服务（原来头顶那颗「启动模型服务」按钮）
            dot.configure(text=T('● 模型未启动'), fg=C_HINT, cursor='hand2')
            self._srv_hint = T('点这里启动模型服务')
            return
        cur = self.var_model.get().strip()
        # 服务在跑 = 绿点；但模型还没准备好时橙点提示，细节写在悬停里
        if not self._models:
            self._srv_hint = T('服务在线 · 还没下载任何模型（可在「⋯更多 → 模型管理…」下载）')
            dot.configure(text=T('● 模型运行中 · 待下载'), fg=C_WARN, cursor='arrow')
            return
        if cur and not engine.model_exists(cur, {m: 0 for m in self._models}):
            self._srv_hint = T('%s 未下载 · 在「⋯更多 → 模型管理…」下载') % cur
            dot.configure(text=T('● 模型运行中 · 待下载'), fg=C_WARN, cursor='arrow')
            return
        ms = info.get('models') or []
        if ms:
            m = ms[0]
            gb = m['vram'] / 1073741824.0
            tot = m['size'] / 1073741824.0
            allin = T('（全部在显存）') if m['size'] and abs(m['size'] - m['vram']) < 1e7 else \
                    T('（%.1fG/%.1fG 部分在内存）') % (gb, tot)
            self._srv_hint = T('已就绪 · %s · %.1fG %s') % (m['name'], tot, allin)
        else:
            self._srv_hint = T('服务在线 · 模型待命（点「开始识别」时自动加载，无需手动）')
        dot.configure(text=T('● 模型运行中'), fg=C_OK, cursor='arrow')

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
            self.lbl_state.configure(text=T('● 识别中 %d/%d') % (i, total), fg=C_ACCENT)

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
        hp = _help_path()
        hd = HelpDialog(self.root, self)
        self.root.update_idletasks()
        self.log(T('自检：使用说明窗口构建正常（%d 段 · %s）')
                 % (hd.n_seg, os.path.basename(hp) if hp else T('（缺文件）')),
                 'ok' if hd.n_seg > 20 and hp else 'warn')
        hd.on_close()
        pd = PlanDialog(self.root, os.path.dirname(os.path.abspath(__file__)), [
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
        rd = RenameResultDialog(self.root, os.path.dirname(os.path.abspath(__file__)), {
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
        st = '● 模型未启动'
        self.dot_srv.configure(text=st, fg=C_HINT)
        self.dot_srv.configure(text=T('● 模型运行中'), fg=C_OK)
        self.update_server({'up': True, 'models': []}, [], engine.runtime_status())
        self.update_server({'up': False, 'models': []}, [], engine.runtime_status())
        self.root.update_idletasks()
        self.log(T('自检：底部状态栏（●模型状态 / 端口 / 模型库）切换正常'), 'ok')
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
        self.log('ffmpeg = %s' % engine.FF(), 'dim')
        rs = engine.runtime_status()
        self.log(T('推理运行时 = %s（%s）') % (rs['exe'] or T('未找到'), rs['label']), 'dim')
        self.log(T('模型库 = %s') % engine.models_dir(), 'dim')
        self.log(T('预设目录：%s') % engine.PRESETS, 'dim')


STATUS_ZH = {
    'pulling manifest': T('获取清单'),
    'pulling': T('下载中'),
    'downloading': T('下载中'),
    'verifying sha256 digest': T('校验完整性'),
    'writing manifest': T('写入清单'),
    'removing any unused layers': T('清理旧层'),
    'using existing layer': T('已存在，复用'),
    'success': T('完成'),
}


class PlanDialog(tk.Toplevel):
    """核对并改名：把识别结果摆成一张能直接改的表（时间 / 地点 / 描述）。

    设计取向（对应反馈）：
      * 软件先一口气跑完，绝不中途弹窗打断；跑完才打开这里让用户改。
      * 时间、地名、描述都能直接改（GPS 有时不准，有些文件读不到时间）。
      * 时间留空的行不会被改名，原文件保持不动。
      * 同一场景的相邻文件地名都一样 → 格子后面有 ↓，把值填到下面连续空白
        （跨页有效），不用一格一格抄，也不需要批量改横条。
      * 关窗自动存草稿（不改名）；真正动文件只有「应用改名」一条路。
      * 一次可能是几千条（照片目录随随便便六千张），所以**分页显示**：
        数据全在内存里，但界面上只建当前页那几十行的控件，翻页飞快。
    """

    PAGE_SIZES = (50, 100, 300)

    def __init__(self, master, directory, items):
        super().__init__(master)
        self.directory = directory
        self.items = [dict(x) for x in items]
        # rows 是全部数据（dict，不是控件）；frame 只在当前页存在
        self.rows: list[dict] = []
        self.page = 0
        self.page_size = 100
        self._suspend = False    # 批量改值时先挂起预览，改完只重算当前页
        self.applied = None     # (done, failed, skipped)
        self.detail = None      # 改名明细：谁改成了什么、谁失败、谁被跳过
        self.title(T('核对并改名 · %s') % APP_TITLE)
        self.geometry('1200x700')
        self.minsize(900, 460)
        self.configure(bg=C_BG)
        self.transient(master)
        self.grab_set()
        self.protocol('WM_DELETE_WINDOW', self.on_cancel)
        self._build()
        self.bind('<Escape>', lambda *a: self.on_cancel())

    # ---------------------------------------------------------------- 构建
    def _build(self):
        head = tk.Frame(self, bg=C_BG)
        head.pack(fill='x', padx=14, pady=(12, 2))
        tk.Label(head, text=T('核对并改名'), bg=C_BG, fg=C_TEXT,
                 font=(FONT, 14, 'bold')).pack(side='left')
        tk.Label(head, text=T('时间、地名、描述直接改；时间留空不改名；' + '格子后面 ↓ 把值填到下面连续空白（同一场景填一次就够）'),
                 bg=C_BG, fg=C_SUB, font=(FONT, 9)).pack(side='left', padx=(10, 0),
                                                         pady=(4, 0))
        self.lbl_stat = tk.Label(head, text='', bg=C_BG, fg=C_SUB, font=(FONT, 9))
        self.lbl_stat.pack(side='right', pady=(4, 0))

        # 表头
        hd = tk.Frame(self, bg=C_BG)
        hd.pack(fill='x', padx=14)
        for txt, w in (('#', 4), (T('原文件名'), 32), (T('拍摄时间'), 15), (T('地名'), 15),
                       (T('描述'), 12), (T('改完的新文件名'), 30)):
            tk.Label(hd, text=txt, width=w, anchor='w', bg=C_BG, fg=C_SUB,
                     font=(FONT, 9, 'bold')).pack(side='left', padx=(0, 4))

        wrap = tk.Frame(self, bg=C_CARD, highlightbackground=C_LINE, highlightthickness=1)
        wrap.pack(fill='both', expand=True, padx=14, pady=(0, 0))
        self.canvas = tk.Canvas(wrap, bg=C_CARD, highlightthickness=0)
        vs = ttk.Scrollbar(wrap, orient='vertical', command=self.canvas.yview)
        hs = ttk.Scrollbar(wrap, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side='right', fill='y')
        hs.pack(side='bottom', fill='x')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.rows_box = tk.Frame(self.canvas, bg=C_CARD)
        self.canvas.create_window((0, 0), window=self.rows_box, anchor='nw')
        self.rows_box.bind(
            '<Configure>',
            lambda *a: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind_all('<MouseWheel>', self._on_wheel)

        self._build_pagebar()
        for i, it in enumerate(self.items, 1):
            self._make_row(it, it.get('index') or i)
        self._apply_filter()

        foot = tk.Frame(self, bg=C_BG)
        foot.pack(fill='x', padx=14, pady=12)
        ttk.Button(foot, text=T('查找替换…'), style='Sec.TButton', width=BW(12, 18),
                   command=self.open_replace).pack(side='left')
        tk.Label(foot, text=T('改名后想还原：主界面点「回滚本目录改名」。'),
                 bg=C_BG, fg=C_SUB, font=(FONT, 9)).pack(side='left', padx=(12, 0))
        ttk.Button(foot, text=T('应用改名'), style='Go.TButton',
                   command=self.on_apply).pack(side='right')
        ttk.Button(foot, text=T('暂不改名，关闭'), style='Sec.TButton', width=BW(14, 18),
                   command=self.on_cancel).pack(side='right', padx=(0, 10))

    def open_replace(self):
        """散落在不相邻行里的同一个错名，用查找替换一次换掉。"""
        dlg = ReplaceDialog(self, self.rows)
        self.wait_window(dlg)
        if dlg.n_done:
            for row in self.rows:
                if row['frame'] is not None:
                    self._preview(row)
            self._stat()
            messagebox.showinfo(APP_TITLE, T('已替换 %d 行。') % dlg.n_done, parent=self)

    def _make_row(self, it, idx):
        """只建数据、不建控件：几千条时，控件留给 _mount 按页创建。"""
        vs = {'time': tk.StringVar(value=it.get('time', '')),
              'place': tk.StringVar(value=it.get('place', '')),
              'desc': tk.StringVar(value=it.get('desc', ''))}
        row = {'it': it, 'idx': idx, 'vs': vs, 'frame': None,
               # 记下「用户开始改之前」那一格的值：↓ 靠它判断该替换哪些同名格
               # （点位库用一个行政中心代表整片区域，相邻几条往往同错，得成段改）
               'old_at_focus': {'place': it.get('place', ''),
                                'desc': it.get('desc', '')},
               'new': tk.StringVar(value=it.get('new', '') or T('（时间空白，不改名）'))}
        for v in vs.values():
            v.trace_add('write', lambda *a, rw=row: self._on_edit(rw))
        # 这里**不**算预览：几千条时白白多算几千次文件名，等 _mount 显示时再算
        self.rows.append(row)

    def _mount(self, row):
        """把这一行真正建成控件（只有落在当前页的行才会被建成）。"""
        if row['frame'] is not None:
            return
        it, vs = row['it'], row['vs']
        f = tk.Frame(self.rows_box, bg=C_CARD)
        tk.Label(f, text=str(row['idx']), width=4, anchor='e', bg=C_CARD, fg=C_SUB,
                 font=(FONT, 9)).pack(side='left', padx=(0, 4))
        kind = '照' if it.get('kind') == 'photo' else '影'
        tag = '' if it.get('ok', True) else T('（失败）')
        tk.Label(f, text='%s %s%s' % (kind, cut(it.get('old', ''), 28), tag),
                 width=32, anchor='w', bg=C_CARD,
                 fg=(C_TEXT if it.get('ok', True) else C_ERR),
                 font=(FONT, 9)).pack(side='left', padx=(0, 4))
        ttk.Entry(f, textvariable=vs['time'], width=15, font=(FONT, 9)).pack(
            side='left', padx=(0, 4))
        e_place = ttk.Entry(f, textvariable=vs['place'], width=15, font=(FONT, 9))
        e_place.pack(side='left', padx=(0, 0))
        e_place.bind('<FocusIn>', lambda *a: self._mark_old(row, 'place'))
        self._fill_btn(f, row, 'place')
        e_desc = ttk.Entry(f, textvariable=vs['desc'], width=12, font=(FONT, 9))
        e_desc.pack(side='left', padx=(4, 0))
        e_desc.bind('<FocusIn>', lambda *a: self._mark_old(row, 'desc'))
        self._fill_btn(f, row, 'desc')
        tk.Label(f, textvariable=row['new'], width=30, anchor='w', bg=C_CARD,
                 fg=C_ACCENT, font=(FONT, 9)).pack(side='left')
        row['frame'] = f
        self._preview(row)               # 显示前先走一遍「时间空白=不改名」的判断

    def _fill_btn(self, parent, row, field):
        """格子后面的小 ↓：把这一格的值填到下面**连续的空白格**（跨页有效）。

        同一场景的视频/照片地名都一样，填一次就够，不用一格一格抄。
        """
        # 静止时给一块浅灰蓝底（白底上才能看出它是可点的），悬浮变主色蓝底白字
        b = tk.Button(parent, text='↓', width=2, bd=0, relief='flat', cursor='hand2',
                      bg='#eef2f7', fg=C_ACCENT, activebackground=C_ACCENT_D,
                      activeforeground='#ffffff', font=(FONT, 10, 'bold'),
                      command=lambda: self._fill_down(row, field))
        b.pack(side='left', padx=(0, 4))
        label = T('地名') if field == 'place' else T('描述')
        self._attach_tip(
            b, T('① 把这一格的%s填到下面连续的空白格（遇到有值就停，跨页有效）\n' + '② 改过这一格后点它：把上下连续的同名格一起换成新值\n' + '   （点位库用一个行政中心代表整片区域，边界上常常一整段都判错）') % label)

    def _attach_tip(self, widget, text):
        """鼠标停在 ↓ 上弹一行说明（和主界面 Tip 同款样式）。"""
        win = [None]
        job = [None]

        def show():
            job[0] = None
            if win[0] or not widget.winfo_exists():
                return
            x = widget.winfo_rootx() + 14
            y = widget.winfo_rooty() + 22
            w = tk.Toplevel(self)
            w.wm_overrideredirect(True)
            w.wm_geometry('+%d+%d' % (x, y))
            tk.Label(w, text=text, justify='left', bg='#fffbe6', fg=C_TEXT,
                     font=(FONT, 9), relief='solid', borderwidth=1).pack(padx=6, pady=5)
            win[0] = w

        def hide(_e=None):
            if job[0]:
                try:
                    self.after_cancel(job[0])
                except Exception:
                    pass
                job[0] = None
            if win[0]:
                try:
                    win[0].destroy()
                except Exception:
                    pass
                win[0] = None

        widget.bind('<Enter>', lambda e: job.__setitem__(0, self.after(500, show)))
        widget.bind('<Leave>', hide)
        widget.bind('<ButtonPress>', hide)

    def _unmount(self, row):
        if row['frame'] is None:
            return
        try:
            row['frame'].destroy()
        except Exception:
            pass
        row['frame'] = None

    def _mark_old(self, row, field):
        """开始改这一格时，先把它原来的值记下来（↓ 的「同名替换」要用）。"""
        row['old_at_focus'][field] = row['vs'][field].get()

    def _fill_down(self, row, field):
        """↓ 的两种用法，一次点完：

        1. 向下填空白（同场景的地名/描述只写一个就够了）；
        2. **改过这一格后**点它 → 把上下**连续的同名格**一起换成新值。
           点位库用一个行政中心代表整片区域，附近几条常常一起判错（比如在县城
           边缘被判到邻县），这种时候改一格就能修一整段。
        """
        val = row['vs'][field].get().strip()
        if not val:
            messagebox.showinfo(APP_TITLE, T('这一格是空的，先填上内容再向下填。'), parent=self)
            return
        if field == 'desc':
            val = val[:9]                # 描述最多 9 个字，保持和识别一致
        src = (row['old_at_focus'].get(field) or '').strip()
        same_run = bool(src) and src != val        # 改过值 → 顺手把同名格整段替换
        at = self.rows.index(row)
        i, filled = at + 1, 0
        lo = hi = at
        self._suspend = True             # 批量改值时挂起预览，改完只重算看得见的
        try:
            while i < len(self.rows):    # 往下：填空白 + 替换同名
                cur = self.rows[i]['vs'][field].get().strip()
                if cur == '' or (same_run and cur == src):
                    self.rows[i]['vs'][field].set(val)
                    filled += 1
                    hi = i
                    i += 1
                else:
                    break
            j = at - 1
            while j >= 0 and same_run:   # 往上：只替换同名，不填空白
                if self.rows[j]['vs'][field].get().strip() == src:
                    self.rows[j]['vs'][field].set(val)
                    filled += 1
                    lo = j
                    j -= 1
                else:
                    break
        finally:
            self._suspend = False
        for r in self.rows[lo:hi + 1]:
            if r['frame'] is not None:
                self._preview(r)
        self._stat()
        if not filled:
            messagebox.showinfo(
                APP_TITLE,
                T('没有可填的空白，上下相邻格的值也不一样。\n' + '（想改不相邻的同名格，用左下角「查找替换…」）'), parent=self)

    def _filtered(self):
        """当前筛选条件下的行。"""
        out = []
        for row in self.rows:
            it, vs = row['it'], row['vs']
            if self.var_only_bad.get() and it.get('ok', True) and vs['time'].get().strip():
                continue
            out.append(row)
        return out

    def _render_page(self):
        """只把当前页的行建成控件：6400 条也不会卡。"""
        for row in self.rows:
            self._unmount(row)
        vis = self._filtered()
        n_page = max(1, -(-len(vis) // self.page_size))
        self.page = max(0, min(self.page, n_page - 1))
        lo = self.page * self.page_size
        for row in vis[lo:lo + self.page_size]:
            self._mount(row)
            row['frame'].pack(fill='x', padx=8, pady=3)
        try:
            self.rows_box.update_idletasks()
            self.canvas.configure(scrollregion=self.canvas.bbox('all') or (0, 0, 1, 1))
            self.canvas.yview_moveto(0.0)
        except Exception:
            pass
        self._page_info(len(vis), n_page, lo)
        self._stat()

    def _build_pagebar(self):
        bar = tk.Frame(self, bg=C_BG)
        bar.pack(fill='x', padx=14, pady=(6, 0))
        self.btn_prev = ttk.Button(bar, text=T('← 上一页'), style='Sec.TButton', width=BW(11, 13),
                                   command=lambda: self._turn(-1))
        self.btn_prev.pack(side='left')
        self.btn_next = ttk.Button(bar, text=T('下一页 →'), style='Sec.TButton', width=BW(11, 13),
                                   command=lambda: self._turn(1))
        self.btn_next.pack(side='left', padx=(8, 0))
        self.lbl_page = tk.Label(bar, text='', bg=C_BG, fg=C_TEXT, font=(FONT, 9))
        self.lbl_page.pack(side='left', padx=(12, 0))
        tk.Label(bar, text=T('每页'), bg=C_BG, fg=C_SUB, font=(FONT, 9)).pack(
            side='left', padx=(16, 2))
        cb = ttk.Combobox(bar, width=5, state='readonly',
                          values=[str(x) for x in self.PAGE_SIZES])
        cb.set(str(self.page_size))
        cb.pack(side='left')
        cb.bind('<<ComboboxSelected>>',
                lambda *a: (setattr(self, 'page_size', int(cb.get())),
                            self._render_page()))
        # 小开关收在页脚：找空白行用（默认关，不打扰）
        self.var_only_bad = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text=T('只看空白'), variable=self.var_only_bad,
                        command=self._apply_filter).pack(side='right')

    def _turn(self, d):
        self.page += d
        self._render_page()

    def _page_info(self, n_vis, n_page, lo):
        hi = min(lo + self.page_size, n_vis)
        scope = (T('只看空白/失败，共 %d 条') % n_vis) if self.var_only_bad.get() \
            else (T('全部 %d 条') % len(self.rows))
        self.lbl_page.configure(
            text=T('第 %d / %d 页\u3000·\u3000显示第 %d–%d 条\u3000·\u3000%s')
                 % (self.page + 1, n_page, (lo + 1) if n_vis else 0, hi, scope))
        self.btn_prev.state(['!disabled' if self.page > 0 else 'disabled'])
        self.btn_next.state(['!disabled' if self.page < n_page - 1 else 'disabled'])

    # ---------------------------------------------------------------- 交互
    def _preview(self, row):
        if self._suspend:
            return
        it, vs, v_new = row['it'], row['vs'], row['new']
        t = vs['time'].get().strip()
        dt = engine.parse_manual_time(t) if t else None
        nm = engine.name_from_item({
            'old': it.get('old', ''),
            'time': dt.strftime('%Y%m%d_%H%M') if dt else '',
            'place': vs['place'].get().strip(),
            'desc': vs['desc'].get().strip()})
        if not t:
            v_new.set(T('（时间空白，不改名）'))
        elif dt is None:
            v_new.set(T('（时间看不懂）'))
        else:
            v_new.set(nm or T('（空）'))

    def _on_edit(self, row):
        """用户手改了某一行：只更新这一行的预览和顶部统计。"""
        if self._suspend:
            return
        self._preview(row)
        self._stat()

    def _stat(self):
        ready = blank = 0
        for row in self.rows:
            if row['vs']['time'].get().strip() and row['it'].get('ok', True):
                ready += 1
            else:
                blank += 1
        self.lbl_stat.configure(
            text=T('共 %d 项 · 将改名 %d · 跳过（时间空白/失败） %d')
                 % (len(self.rows), ready, blank))

    def _on_wheel(self, ev):
        """滚轮只在本窗口内生效。

        绑定在 "all" 上是为了让焦点落在输入框里时也能滚；但必须判断事件
        发生在哪个窗口，否则会把主窗口的结果表和日志一起带着滚。
        """
        try:
            if not self.winfo_exists():
                return
            w = self.winfo_containing(ev.x_root, ev.y_root)
            if w is None:
                return
            tl = w.winfo_toplevel()
            if tl is not self and str(tl) != str(self):
                return
            self.canvas.yview_scroll(int(-ev.delta / 120), 'units')
        except Exception:
            pass

    def _apply_filter(self):
        self.page = 0
        self._render_page()

    # ---------------------------------------------------------------- 动作
    def on_apply(self):
        out, bad = [], []
        for row in self.rows:
            it, vs = row['it'], row['vs']
            t = vs['time'].get().strip()
            if t:
                dt = engine.parse_manual_time(t)
                if dt is None:
                    bad.append((it.get('old', ''), t))
                    continue
                t = dt.strftime('%Y%m%d_%H%M')
            it2 = dict(it)
            it2['time'] = t
            it2['place'] = vs['place'].get().strip()
            it2['desc'] = vs['desc'].get().strip()
            it2['new'] = engine.name_from_item(it2)
            out.append(it2)
        if bad:
            fn, s = bad[0]
            messagebox.showerror(
                APP_TITLE,
                T('有 %d 行的时间写得不认识，请改成 20251011_1430 这种写法。\n\n%s\n  「%s」')
                % (len(bad), cut(fn, 40), s), parent=self)
            return
        ready = [x for x in out if x['time'] and x.get('ok', True) and
                 x.get('new') and x['new'] != x['old']]
        if not ready:
            messagebox.showwarning(APP_TITLE, T('没有任何一行可以改名（时间都是空的）。'),
                                   parent=self)
            return
        if not messagebox.askyesno(
                APP_TITLE,
                T('将按表格里的内容改名 %d 个文件（其余 %d 个时间空白或无需改动的保持原名）。' + '\n\n改名后可以在主界面点「回滚本目录改名」还原。\n确定继续？')
                % (len(ready), len(out) - len(ready)), parent=self):
            return
        try:
            done, failed, skipped = engine.apply_plan(
                self.directory, out, on_log=lambda s: None)
        except Exception:
            messagebox.showerror(APP_TITLE, traceback.format_exc(limit=3), parent=self)
            return
        # apply_plan 执行完会在每行上留下 applied / err，据此分出「成功 / 失败 / 没动」
        ok_n, bad_n, skip_n = [], [], []
        for x in out:
            old = x.get('old', '')
            if x.get('applied'):
                ok_n.append((old, x.get('new', '')))
            elif x.get('err'):
                bad_n.append((old, x.get('new', ''), x.get('err', '')))
            else:
                skip_n.append((old, x.get('new', '')))
        self.detail = {'ok': ok_n, 'fail': bad_n, 'skip': skip_n,
                       'counts': (done, failed, skipped), 'dir': self.directory}
        self.applied = (done, failed, skipped)
        self._close()

    def on_cancel(self):
        self.applied = None
        self._close()

    def _save_draft(self):
        """把改过的时间/地名/描述存回计划表（**不改名**），关窗后不丢。

        下次打开「核对并改名」还是你上次改的样子；真正动文件名仍要「应用改名」。
        已改过名的行保持实际文件名，不重算。
        """
        try:
            for row in self.rows:
                it, vs = row['it'], row['vs']
                t = vs['time'].get().strip()
                dt = engine.parse_manual_time(t) if t else None
                it['time'] = dt.strftime('%Y%m%d_%H%M') if dt else t
                it['place'] = vs['place'].get().strip()
                it['desc'] = vs['desc'].get().strip()
                if not it.get('applied'):
                    it['new'] = engine.name_from_item(it) if dt else ''
            engine.save_plan(self.directory, self.items)
        except Exception:
            pass

    def _close(self):
        if self.applied is None:        # 应用改名后计划表已由 apply_plan 落盘，别覆盖
            self._save_draft()
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class ReplaceDialog(tk.Toplevel):
    """全表查找替换：把某个错的地名（或描述）一次性换成对的。

    典型场景：手机定位飘了，把「玉林西路」判成了「肖家河」，十几行全错，
    但这些行不一定挨着 —— ↓ 只能改连续的一段，散落的就用这个。
    """

    def __init__(self, master, rows):
        super().__init__(master)
        self.rows = rows
        self.n_done = 0
        self.title(T('查找替换 · %s') % APP_TITLE)
        self.geometry('520x300')
        self.minsize(460, 260)
        self.configure(bg=C_BG)
        self.transient(master)
        self.grab_set()
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.bind('<Escape>', lambda *a: self.on_close())

        tk.Label(self, text=T('查找替换'), bg=C_BG, fg=C_TEXT,
                 font=(FONT, 14, 'bold')).pack(anchor='w', padx=14, pady=(12, 2))
        tk.Label(self, text=T('把表里所有「完全相同」的这一项换成新值（不分相邻，全表生效）'),
                 bg=C_BG, fg=C_SUB, font=(FONT, 9)).pack(anchor='w', padx=14)

        box = self._card()
        self.var_field = tk.StringVar(value='place')
        r0 = tk.Frame(box, bg=C_CARD)
        r0.pack(fill='x', pady=(0, 8))
        tk.Label(r0, text=T('改哪一列'), bg=C_CARD, fg=C_TEXT, font=(FONT, 10),
                 width=8, anchor='w').pack(side='left')
        ttk.Radiobutton(r0, text=T('地名'), value='place', variable=self.var_field,
                        command=lambda: (self._fill_chips(), self._recount())).pack(
            side='left')
        ttk.Radiobutton(r0, text=T('描述'), value='desc', variable=self.var_field,
                        command=lambda: (self._fill_chips(), self._recount())).pack(
            side='left', padx=(16, 0))

        self.var_find = tk.StringVar()
        self.var_to = tk.StringVar()
        for label, var in ((T('查找'), self.var_find), (T('替换为'), self.var_to)):
            r = tk.Frame(box, bg=C_CARD)
            r.pack(fill='x', pady=3)
            tk.Label(r, text=label, bg=C_CARD, fg=C_TEXT, font=(FONT, 10),
                     width=8, anchor='w').pack(side='left')
            e = ttk.Entry(r, textvariable=var, font=(FONT, 10))
            e.pack(side='left', fill='x', expand=True)
            var.trace_add('write', lambda *a: self._recount())
        # 已有的值列出来，点一下就填进查找框，省得手打错别字
        self.lbl_hit = tk.Label(box, text='', bg=C_CARD, fg=C_SUB, font=(FONT, 9),
                                anchor='w', wraplength=450, justify='left')
        self.lbl_hit.pack(fill='x', pady=(8, 2))
        self._build_values(box)

        foot = tk.Frame(self, bg=C_BG)
        foot.pack(fill='x', padx=14, pady=10)
        ttk.Button(foot, text=T('替换全部'), style='Go.TButton',
                   command=self.on_go).pack(side='right')
        ttk.Button(foot, text=T('取消'), style='Sec.TButton', width=BW(10, 10),
                   command=self.on_close).pack(side='right', padx=(0, 10))
        self._recount()

    def _card(self):
        f = tk.Frame(self, bg=C_CARD, highlightbackground=C_LINE, highlightthickness=1)
        f.pack(fill='both', expand=True, padx=14, pady=(8, 0))
        inner = tk.Frame(f, bg=C_CARD)
        inner.pack(fill='both', expand=True, padx=12, pady=10)
        return inner

    def _build_values(self, box):
        """把表里出现过的值列成小按钮，点一下填进查找框（免得手打错别字）。"""
        tk.Label(box, text=T('表里现有的值（点一下填入查找框）：'), bg=C_CARD, fg=C_SUB,
                 font=(FONT, 9)).pack(anchor='w', pady=(4, 2))
        self.chips = tk.Frame(box, bg=C_CARD)
        self.chips.pack(fill='x')
        self._fill_chips()

    def _fill_chips(self):
        for w in self.chips.winfo_children():
            w.destroy()
        field = self.var_field.get()
        vals = []
        for row in self.rows:
            v = row['vs'][field].get().strip()
            if v and v not in vals:
                vals.append(v)
        for v in vals[:40]:
            tk.Button(self.chips, text=v, bd=1, relief='solid', bg='#eef2f7',
                      fg=C_TEXT, activebackground=C_ACCENT_D,
                      activeforeground='#ffffff', font=(FONT, 9),
                      cursor='hand2', command=lambda s=v: self.var_find.set(s)).pack(
                side='left', padx=(0, 6), pady=2)

    def _recount(self):
        f = self.var_find.get().strip()
        n = sum(1 for r in self.rows
                if r['vs'][self.var_field.get()].get().strip() == f) if f else 0
        to = self.var_to.get().strip()
        if not f:
            self.lbl_hit.configure(text=T('先填要查找的内容（或从下面点一个现有的值）'), fg=C_SUB)
        elif not to:
            self.lbl_hit.configure(text=T('命中 %d 行。还要填「替换为」的内容。') % n, fg=C_WARN)
        else:
            self.lbl_hit.configure(text=T('命中 %d 行：「%s」→「%s」') % (n, f, to), fg=C_OK)

    def on_go(self):
        f, to = self.var_find.get().strip(), self.var_to.get().strip()
        if not f:
            messagebox.showinfo(APP_TITLE, T('先填要查找的内容。'), parent=self)
            return
        if not to:
            messagebox.showinfo(APP_TITLE, T('先填「替换为」的内容。'), parent=self)
            return
        hits = [r for r in self.rows if r['vs'][self.var_field.get()].get().strip() == f]
        if not hits:
            messagebox.showinfo(APP_TITLE, T('没有找到「%s」。') % f, parent=self)
            return
        if not messagebox.askyesno(
                APP_TITLE, T('把 %d 行的「%s」改成「%s」？\n\n' + '（只改表格内容，文件名要等点「应用改名」才动）')
                           % (len(hits), f, to), parent=self):
            return
        for r in hits:
            r['vs'][self.var_field.get()].set(to)
        self.n_done = len(hits)
        self.on_close()

    def on_close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class RenameResultDialog(tk.Toplevel):
    """改名结果：成功 / 跳过 / 失败各多少，失败的列出文件名和原因。

    之前只有底部日志里一行字，滚过去就找不到了（v3.4 反馈「改名后如何确认」）。
    """

    MAX_LINES = 300          # 清单最多列这么多行，再多只显示计数

    def __init__(self, master, directory, detail):
        super().__init__(master)
        self.directory = directory
        self.detail = detail or {}
        ok = self.detail.get('ok') or []
        bad = self.detail.get('fail') or []
        skip = self.detail.get('skip') or []
        done, failed, skipped = (self.detail.get('counts')
                                 or (len(ok), len(bad), len(skip)))
        self.title(T('改名结果 · %s') % APP_TITLE)
        self.geometry('760x480')
        self.minsize(560, 340)
        self.configure(bg=C_BG)
        self.transient(master)
        self.grab_set()
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.bind('<Escape>', lambda *a: self.on_close())

        head = tk.Frame(self, bg=C_BG)
        head.pack(fill='x', padx=14, pady=(14, 2))
        tk.Label(head, text=T('改名完成'), bg=C_BG, fg=C_TEXT,
                 font=(FONT, 15, 'bold')).pack(side='left')
        tk.Label(head, text=cut(directory, 60), bg=C_BG, fg=C_SUB,
                 font=(FONT, 9)).pack(side='left', padx=(10, 0), pady=(6, 0))

        nums = tk.Frame(self, bg=C_BG)
        nums.pack(fill='x', padx=14, pady=(8, 6))
        for txt, val, col in ((T('已改名'), done, C_OK), (T('未改动'), skipped, C_SUB),
                              (T('失败'), failed, C_ERR if failed else C_SUB)):
            f = tk.Frame(nums, bg=C_CARD, highlightbackground=C_LINE,
                         highlightthickness=1)
            f.pack(side='left', padx=(0, 10), ipadx=14, ipady=6)
            tk.Label(f, text=str(val), bg=C_CARD, fg=col,
                     font=(FONT, 18, 'bold')).pack()
            tk.Label(f, text=txt, bg=C_CARD, fg=C_SUB, font=(FONT, 9)).pack()

        wrap = tk.Frame(self, bg=C_BG)
        wrap.pack(fill='both', expand=True, padx=14, pady=(0, 6))
        self.txt = tk.Text(wrap, height=12, bg=C_CARD, fg=C_TEXT, relief='flat',
                           font=('Consolas', 9), wrap='none')
        vs = ttk.Scrollbar(wrap, orient='vertical', command=self.txt.yview)
        hs = ttk.Scrollbar(wrap, orient='horizontal', command=self.txt.xview)
        self.txt.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.txt.grid(row=0, column=0, sticky='nsew')
        vs.grid(row=0, column=1, sticky='ns')
        hs.grid(row=1, column=0, sticky='ew')
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.txt.tag_configure('h', foreground=C_TEXT, font=(FONT, 10, 'bold'))
        self.txt.tag_configure('ok', foreground=C_OK)
        self.txt.tag_configure('bad', foreground=C_ERR)
        self.txt.tag_configure('dim', foreground=C_SUB)
        self._fill(ok, bad, skip)
        self.txt.configure(state='disabled')

        foot = tk.Frame(self, bg=C_BG)
        foot.pack(fill='x', padx=14, pady=(0, 12))
        ttk.Button(foot, text=T('打开该目录'), command=self.open_dir).pack(side='left')
        ttk.Button(foot, text=T('打开改名台账'), command=self.open_ledger).pack(
            side='left', padx=(8, 0))
        ttk.Button(foot, text=T('复制清单'), command=self.copy_all).pack(
            side='left', padx=(8, 0))
        ttk.Button(foot, text=T('关闭'), command=self.on_close).pack(side='right')
        self.txt.see('1.0')

    def _fill(self, ok, bad, skip):
        w = self.txt
        for title, rows, tag in ((T('已改名 %d 个') % len(ok), ok, 'ok'),
                                 (T('改名失败 %d 个（保持原名，改掉后可再试）') % len(bad),
                                  bad, 'bad'),
                                 (T('未改动 %d 个（时间空白、或新名和原名一样）') % len(skip),
                                  skip, 'dim')):
            if not rows:
                continue
            w.insert('end', '\n%s\n' % title, ('h',))
            for r in rows[:self.MAX_LINES]:
                if len(r) >= 3:        # 失败：旧 → 新：原因
                    w.insert('end', '  %s\n     → %s　%s\n' % (r[0], r[1] or T('（空）'), r[2]),
                             (tag,))
                else:
                    w.insert('end', '  %s\n     → %s\n' % (r[0], r[1] or T('（未改名）')),
                             (tag,))
            if len(rows) > self.MAX_LINES:
                w.insert('end', T('  …另有 %d 条未列出\n') % (len(rows) - self.MAX_LINES),
                         ('dim',))
        if not (ok or bad or skip):
            w.insert('end', T('\n没有可改名的条目。'), ('dim',))

    def copy_all(self):
        self.clipboard_clear()
        self.clipboard_append(self.txt.get('1.0', 'end'))

    def open_dir(self):
        if os.path.isdir(self.directory):
            _open_path(self.directory)

    def open_ledger(self):
        p = engine.state_paths(self.directory)['ledger']
        if os.path.exists(p):
            _open_path(p)

    def on_close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def _help_path() -> str:
    """使用说明（Markdown）在哪：优先 exe 自带的（_MEIPASS），再找软件目录。

    放在软件目录里那份是给用户看的/可改的；打包时两份都带。
    """
    name = 'help_zh.md' if strings.lang() == 'zh' else 'help_en.md'
    for r in engine._bundle_roots():
        p = os.path.join(r, name)
        if os.path.exists(p):
            return p
    return ''


def _md_inline(s: str):
    """把一行里的 **粗体** 和 `行内代码` 拆成 [(文本, tag), ...]。

    粗体里还可能套着 `代码`（英文版「**`▶ Start recognition`**」这种写法），
    所以粗体的内容要递归再拆一遍：内层的代码保持 mono，其余归入粗体。
    """
    out, buf, i = [], '', 0
    while i < len(s):
        if s.startswith('**', i):
            j = s.find('**', i + 2)
            if j > i + 1:
                if buf:
                    out.append((buf, ''))
                buf = ''
                for t, tg in _md_inline(s[i + 2:j]):
                    out.append((t, tg if tg == 'm' else 'b'))
                i = j + 2
                continue
        if s[i] == '`':
            j = s.find('`', i + 1)
            if j > i:
                if buf:
                    out.append((buf, ''))
                buf = ''
                out.append((s[i + 1:j], 'm'))
                i = j + 1
                continue
        buf += s[i]
        i += 1
    if buf:
        out.append((buf, ''))
    return out


def _md_render(txt: tk.Text, md: str) -> int:
    """极简 Markdown 渲染：只认帮助文档用到的那几种语法。

    没引第三方库（tkhtmlview 之类要拖一堆依赖、许可也杂），自己按行处理就够：
    # 标题 / - 列表 / 1. 有序列表 / > 引用 / ``` 代码块 / **粗体** / `代码` / --- 分隔线。
    返回渲染出的段落数（自检用）。
    """
    base = (FONT, 10)
    cfg = txt.tag_configure
    cfg('h1', font=(FONT, 16, 'bold'), spacing1=16, spacing3=6, foreground=C_TEXT)
    cfg('h2', font=(FONT, 13, 'bold'), spacing1=18, spacing3=4, foreground=C_TEXT)
    cfg('h3', font=(FONT, 11, 'bold'), spacing1=12, spacing3=2, foreground=C_TEXT)
    cfg('p', font=base, spacing1=3, spacing3=3, foreground=C_TEXT)
    cfg('li1', font=base, spacing1=3, lmargin1=18, lmargin2=30, foreground=C_TEXT)
    cfg('li2', font=base, spacing1=3, lmargin1=38, lmargin2=50, foreground=C_TEXT)
    cfg('quote', font=base, spacing1=8, spacing3=8, lmargin1=16, lmargin2=16,
        foreground=C_SUB, background=C_ACCENT_L)
    cfg('code', font=('Consolas', 9), spacing1=2, spacing3=2, lmargin1=18,
        lmargin2=18, background='#f2f4f7', foreground=C_TEXT)
    cfg('b', font=(FONT, 10, 'bold'), foreground=C_TEXT)
    cfg('m', font=('Consolas', 9), foreground=C_ACCENT_D)
    cfg('rule', font=base, foreground=C_LINE)

    def put(seg, tag):
        for t, tg in seg:
            txt.insert('end', t, (tag, tg) if tg else tag)
        txt.insert('end', '\n')

    n, i, in_code = 0, 0, False
    for ln in md.replace('\r\n', '\n').split('\n'):
        if ln.strip().startswith('```'):
            in_code = not in_code
            if not in_code:
                txt.insert('end', '\n')
            continue
        if in_code:
            txt.insert('end', ln + '\n', 'code')
            continue
        st = ln.strip()
        if not st:
            continue
        if set(st) == {'-'} and len(st) >= 3:
            txt.insert('end', '─' * 58 + '\n', 'rule')
            continue
        ind = len(ln) - len(ln.lstrip(' '))
        if st.startswith('#'):
            lv = min(len(st) - len(st.lstrip('#')), 3)
            txt.insert('end', st.lstrip('#').strip() + '\n', 'h%d' % lv)
            n += 1
        elif st.startswith('> '):
            put(_md_inline(st[2:]), 'quote')
            n += 1
        elif st[:2] in ('- ', '* '):
            put(_md_inline(st[2:]), 'li2' if ind >= 2 else 'li1')
            n += 1
        elif re.match(r'^\d+[\.、]\s+', st):
            put(_md_inline(st), 'li1')
            n += 1
        else:
            put(_md_inline(st), 'p')
            n += 1
    txt.configure(state='disabled')
    return n


class HelpDialog(tk.Toplevel):
    """使用说明：Markdown 文档渲染成可滚动的富文本。

    首次启动自动弹一次；勾了「下次启动不再自动显示」就写进 config.json，
    之后只通过标题栏的「❓ 使用说明」按钮打开。
    """

    def __init__(self, master, app=None):
        super().__init__(master)
        self.app = app
        self.title('%s　·　%s' % (T('使用说明'), APP_TITLE))
        self.configure(bg=C_BG)
        self.transient(master)
        self.geometry('880x680')
        self.minsize(620, 440)

        head = tk.Frame(self, bg=C_BG)
        head.pack(fill='x', padx=16, pady=(12, 6))
        tk.Label(head, text=T('使用说明'), bg=C_BG, fg=C_TEXT,
                 font=(FONT, 14, 'bold')).pack(side='left')
        tk.Label(head, text='%s　%s' % (APP_TITLE, APP_VER), bg=C_BG, fg=C_SUB,
                 font=(FONT, 9)).pack(side='left', padx=(10, 0), pady=(5, 0))

        wrap = tk.Frame(self, bg=C_CARD, highlightbackground=C_LINE,
                        highlightthickness=1)
        wrap.pack(fill='both', expand=True, padx=14)
        txt = tk.Text(wrap, wrap='word', bg=C_CARD, fg=C_TEXT, bd=0,
                      highlightthickness=0, padx=18, pady=14,
                      cursor='arrow', relief='flat')
        vs = ttk.Scrollbar(wrap, orient='vertical', command=txt.yview)
        txt.configure(yscrollcommand=vs.set)
        vs.pack(side='right', fill='y')
        txt.pack(side='left', fill='both', expand=True)
        self.txt = txt

        md = ''
        p = _help_path()
        if p:
            try:
                md = io.open(p, encoding='utf-8').read()
            except Exception:
                md = ''
        if not md.strip():
            md = '# %s\n\n%s' % (APP_TITLE, T('帮助文件缺失：请确认软件目录下有 help_zh.md / help_en.md'))
        self.n_seg = _md_render(txt, md.replace('%VER%', APP_VER))

        def _wheel(e):
            try:
                txt.yview_scroll(int(-1 * (e.delta / 120)), 'units')
            except Exception:
                pass
        txt.bind('<MouseWheel>', _wheel)

        foot = tk.Frame(self, bg=C_BG)
        foot.pack(fill='x', padx=16, pady=12)
        self.var_noauto = tk.BooleanVar(
            value=not bool((engine.config() or {}).get('help_autoshow', True)))
        ttk.Checkbutton(foot, text=T('下次启动不再自动显示'),
                        variable=self.var_noauto).pack(side='left')
        ttk.Button(foot, text=T('关闭'), style='Go.TButton', width=BW(10, 12),
                   command=self.on_close).pack(side='right')

        self.geometry('+%d+%d' % (master.winfo_rootx() + 90,
                                  max(40, master.winfo_rooty() + 40)))
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.bind('<Escape>', lambda e: self.on_close())
        self.focus_set()

    def on_close(self):
        try:
            engine.save_config(help_autoshow=not bool(self.var_noauto.get()))
        except Exception:
            pass
        self.destroy()


class AboutDialog(tk.Toplevel):
    """关于：版本 / 许可 / 隐私。

    隐私那一段跟着「联网精确定位」开关变：关着是纯离线（文件不上传），
    开着要如实说明「只发经纬度、不发文件」。
    """

    def __init__(self, master, app=None):
        super().__init__(master)
        self.title(T('关于'))
        self.configure(bg=C_BG)
        self.resizable(False, False)
        self.transient(master)
        frm = tk.Frame(self, bg=C_CARD, padx=18, pady=16)
        frm.pack(fill='both', padx=10, pady=10)
        tk.Label(frm, text=APP_TITLE, bg=C_CARD, fg=C_TEXT,
                 font=(FONT, 14, 'bold')).pack(anchor='w')
        tk.Label(frm, text='%s　%s　%s' % (APP_SUB, APP_AUTHOR, APP_VER),
                 bg=C_CARD, fg=C_SUB, font=(FONT, 9)).pack(anchor='w', pady=(2, 10))

        tk.Label(frm, text=T('隐私'), bg=C_CARD, fg=C_TEXT,
                 font=(FONT, 10, 'bold')).pack(anchor='w')
        on = False
        try:
            on = bool(app and app.var_netgeo.get())
        except Exception:
            on = bool((engine.config() or {}).get('net_geo'))
        lines = [T('识别与改名全程在你的电脑上完成，照片和视频不会离开本机，识别结果也不上传。')]
        if on:
            lines.append(T('联网精确定位开启时，仅经纬度坐标会发送给地图服务商用于解析地址，不上传文件本身。'))
        else:
            lines.append(T('联网精确定位当前是关闭的：软件不发任何网络请求，地址全部来自内置离线库。'))
        for s in lines:
            tk.Label(frm, text='· ' + s, bg=C_CARD, fg=C_SUB, font=(FONT, 9),
                     anchor='w', justify='left', wraplength=430
                     ).pack(anchor='w', pady=(3, 0))

        tk.Label(frm, text=T('数据与许可'), bg=C_CARD, fg=C_TEXT,
                 font=(FONT, 10, 'bold')).pack(anchor='w', pady=(12, 0))
        for s in (T('地名数据 © OpenStreetMap 贡献者（ODbL）· 全球城市 GeoNames（CC-BY 4.0）'),
                  T('联网地址：国内 高德开放平台（lbs.amap.com）· 国外 Nominatim / OpenStreetMap')):
            tk.Label(frm, text='· ' + s, bg=C_CARD, fg=C_SUB, font=(FONT, 9),
                     anchor='w', justify='left', wraplength=430
                     ).pack(anchor='w', pady=(3, 0))

        ttk.Button(frm, text=T('关闭'), width=BW(10, 12),
                   command=self.destroy).pack(anchor='e', pady=(14, 0))
        self.geometry('+%d+%d' % (master.winfo_rootx() + 160,
                                  master.winfo_rooty() + 120))
        self.grab_set()
        self.focus_set()
        self.bind('<Escape>', lambda e: self.destroy())


class ModelDialog(tk.Toplevel):
    """模型管理：模型文件不随软件打包，需要时在这里单独下载。"""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.q: queue.Queue = queue.Queue()
        self.downloading: str | None = None
        self.stop_evt = threading.Event()
        self.rows: dict = {}
        self._last = (0.0, 0)
        self._speed = 0.0
        self._inst: dict = {}

        self.title(T('模型管理 · %s') % APP_TITLE)
        self.geometry('790x590')
        self.minsize(700, 470)
        self.configure(bg=C_BG)
        self.transient(master)
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self._build()
        self.refresh()
        self._pump()

    # ---------------------------------------------------------- 布局
    def _build(self):
        head = tk.Frame(self, bg=C_BG)
        head.pack(fill='x', padx=14, pady=(12, 6))
        tk.Label(head, text=T('视觉识别模型（模型包）'), bg=C_BG, fg=C_TEXT,
                 font=(FONT, 13, 'bold')).pack(anchor='w')
        tk.Label(head, text=T('模型文件不随软件打包，按需在这里单独下载；下载后保存在本软件自带的模型库，' + '换台机器把整个文件夹拷过去即可继续用。'),
                 bg=C_BG, fg=C_SUB, font=(FONT, 9), justify='left').pack(anchor='w', pady=(3, 0))
        self.lbl_stat = tk.Label(head, text=T('正在读取…'), bg=C_BG, fg=C_SUB, font=(FONT, 9),
                                 justify='left')
        self.lbl_stat.pack(anchor='w', pady=(5, 0))

        wrap = tk.Frame(self, bg=C_BG)
        wrap.pack(fill='both', expand=True, padx=14)
        self.canvas = tk.Canvas(wrap, bg=C_CARD, highlightthickness=1,
                                highlightbackground=C_LINE)
        vs = ttk.Scrollbar(wrap, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vs.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        vs.pack(side='right', fill='y')
        self.inner = tk.Frame(self.canvas, bg=C_CARD)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>',
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind_all('<MouseWheel>', self._on_wheel)

        foot = tk.Frame(self, bg=C_BG)
        foot.pack(fill='x', padx=14, pady=12)
        self.pb = ttk.Progressbar(foot, mode='determinate', maximum=100)
        self.pb.pack(fill='x')
        self.lbl_prog = tk.Label(foot, text=T('就绪'), bg=C_BG, fg=C_SUB,
                                 font=(FONT, 9), anchor='w')
        self.lbl_prog.pack(fill='x', pady=(4, 8))
        bar = tk.Frame(foot, bg=C_BG)
        bar.pack(fill='x')
        ttk.Button(bar, text=T('刷新'), command=self.refresh).pack(side='left')
        ttk.Button(bar, text=T('打开模型库'), command=self.open_models_dir).pack(
            side='left', padx=8)
        ttk.Button(bar, text=T('模型库位置…'), command=self.change_models_dir).pack(
            side='left')
        ttk.Button(bar, text=T('用本机已有模型库'), command=self.use_system_lib).pack(
            side='left', padx=8)
        ttk.Button(bar, text=T('下载源…'), command=self.set_source).pack(side='left')
        ttk.Button(bar, text=T('关闭'), command=self.on_close).pack(side='right')

    def _on_wheel(self, ev):
        """同上：滚轮只在本窗口内生效，别把主窗口的表格和日志一起滚了。"""
        try:
            if not self.winfo_exists():
                return
            w = self.winfo_containing(ev.x_root, ev.y_root)
            if w is None:
                return
            tl = w.winfo_toplevel()
            if tl is not self and str(tl) != str(self):
                return
            self.canvas.yview_scroll(int(-ev.delta / 120), 'units')
        except Exception:
            pass

    # ---------------------------------------------------------- 列表刷新
    def refresh(self):
        self._inst = engine.installed_models()
        total = sum(self._inst.values())
        src = engine.registry() or T('Ollama 官方源')
        if self._inst:
            self.lbl_stat.configure(
                text=T('已下载 %d 个模型，共 %s\u3000·\u3000模型库 %s\u3000·\u3000下载源 %s')
                     % (len(self._inst), human_size(total), engine.models_dir(), src))
        else:
            self.lbl_stat.configure(
                text=T('本机还没下载任何模型\u3000·\u3000模型库 %s\u3000·\u3000下载源 %s')
                     % (engine.models_dir(), src))

        for w in self.inner.winfo_children():
            w.destroy()
        self.rows.clear()

        names = {m['name'] for m in engine.MODEL_CATALOG}
        items = list(engine.MODEL_CATALOG)
        for n in sorted(self._inst):
            if n not in names:
                items.append({'name': n, 'size': human_size(self._inst[n]),
                              'tag': T('本机已有'), 'note': T('本机已安装的其它模型。')})
        for m in items:
            self._add_row(m, self._inst)
        self._apply_dl_state()

    def _add_row(self, m, inst):
        ok = engine.model_exists(m['name'], inst)
        row = tk.Frame(self.inner, bg=C_CARD)
        row.pack(fill='x', padx=10, pady=6)

        left = tk.Frame(row, bg=C_CARD)
        left.pack(side='left', fill='x', expand=True)
        top = tk.Frame(left, bg=C_CARD)
        top.pack(fill='x', anchor='w')
        tk.Label(top, text='✓' if ok else '↓', bg=C_CARD,
                 fg=C_OK if ok else C_SUB, font=(FONT, 11, 'bold')).pack(side='left')
        tk.Label(top, text=m['name'], bg=C_CARD, fg=C_TEXT,
                 font=(FONT, 10, 'bold')).pack(side='left', padx=(6, 0))
        if m.get('tag'):
            tk.Label(top, text=' %s ' % m['tag'], bg='#e6f1fb', fg='#185fa5',
                     font=(FONT, 8)).pack(side='left', padx=6)
        tk.Label(top, text=m['size'], bg=C_CARD, fg=C_SUB,
                 font=(FONT, 9)).pack(side='left', padx=8)
        tk.Label(left, text=m['note'], bg=C_CARD, fg=C_SUB, font=(FONT, 9),
                 wraplength=470, justify='left').pack(anchor='w', pady=(2, 0))

        btn = ttk.Button(row, width=8,
                         text=T('使用') if ok else T('下载'),
                         command=(lambda n=m['name']: self.use(n)) if ok
                         else (lambda n=m['name']: self.download(n)))
        btn.pack(side='right', padx=8)
        self.rows[m['name']] = {'btn': btn, 'installed': ok}

    def _apply_dl_state(self):
        for name, r in self.rows.items():
            if self.downloading == name:
                r['btn'].configure(text=T('取消'), state='normal', command=self.cancel_dl)
            elif self.downloading:
                r['btn'].configure(state='disabled')
            else:
                r['btn'].configure(state='normal')

    # ---------------------------------------------------------- 动作
    def use(self, name):
        self.app.var_model.set(name)
        try:
            self.app.cb_model.set(name)
        except Exception:
            pass
        self.app.log(T('已选用模型：%s') % name, 'ok')
        self.destroy()

    def open_models_dir(self):
        d = engine.models_dir()
        try:
            if not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            _open_path(d)
        except Exception as e:
            messagebox.showwarning(T('模型管理'), T('无法打开目录：%r') % e, parent=self)

    def change_models_dir(self):
        """把模型库换到别的盘（比如模型包很大、想放到空间充足的盘）。"""
        cur = engine.models_dir()
        d = filedialog.askdirectory(title=T('选择模型库目录（模型包会下载到这里）'),
                                    initialdir=cur if os.path.isdir(cur) else 'D:\\',
                                    parent=self)
        if not d:
            return
        d = os.path.normpath(d)
        if os.path.abspath(d) == os.path.abspath(cur):
            return
        if not messagebox.askyesno(
                T('模型管理'),
                T('把模型库切换到：\n%s\n\n切换后会重启自带推理服务。' + '原目录里的模型不会自动搬过去（需要时手动复制即可）。\n\n继续？') % d,
                parent=self):
            return
        self.lbl_prog.configure(text=T('正在切换模型库并重启服务…'))
        threading.Thread(target=self._switch_dir_bg, args=(d,), daemon=True).start()

    def _switch_dir_bg(self, d):
        ok = engine.set_models_dir(d, on_log=lambda s: self.app.log(s, 'dim'))
        self.q.put(('dir', (d, ok)))

    def _on_dir(self, d, ok):
        self.lbl_prog.configure(text=(T('模型库已切换到 %s') % d) if ok else T('切换失败'))
        self.app.log(T('模型库 → %s') % d, 'ok' if ok else 'err')
        self.refresh()

    def use_system_lib(self):
        """一键复用本机已安装的 Ollama 模型库（免去重新下载模型包）。"""
        cur = engine.models_dir()
        d = engine.system_ollama_models()
        if not d:
            messagebox.showinfo(
                T('模型管理'),
                T('没在本机找到已安装的 Ollama 模型库。\n\n' + '可以点「模型库位置…」手动指定一个目录，或直接在列表里下载模型。'),
                parent=self)
            return
        if os.path.abspath(d) == os.path.abspath(cur):
            messagebox.showinfo(T('模型管理'), T('当前已经在使用这个模型库了。'), parent=self)
            return
        n = engine.local_models_in(d)
        if not messagebox.askyesno(
                T('模型管理'),
                T('检测到本机已安装的 Ollama 模型库：\n%s\n（约 %d 个模型，可直接复用）\n\n' + '把模型库切换到这里？（会重启推理服务；原目录里的文件不会被动）') % (d, n),
                parent=self):
            return
        self.lbl_prog.configure(text=T('正在切换模型库并重启服务…'))
        threading.Thread(target=self._switch_dir_bg, args=(d,), daemon=True).start()

    def set_source(self):
        """设置模型下载源（默认 Ollama 官方源）。"""
        cur = engine.registry()
        v = simpledialog.askstring(
            T('模型下载源'),
            T('留空 = Ollama 官方源（registry.ollama.ai）。\n' + '如需国内镜像，填写镜像域名，例如 mirror.example.com\n' + '（之后下载的模型会从该源拉取）'),
            initialvalue=cur, parent=self)
        if v is None:
            return
        v = engine.set_registry(v)
        self.app.log(T('模型下载源 → %s') % (v or T('Ollama 官方源')), 'ok')
        self.refresh()

    def download(self, name):
        if self.downloading:
            messagebox.showinfo(T('模型管理'), T('已有下载任务在进行中。'), parent=self)
            return
        if not engine.port_open(1.0):
            self.lbl_prog.configure(text=T('正在启动模型服务…'))
            if not engine.start_server(on_log=lambda s: self.app.log(s, 'dim')):
                messagebox.showerror(T('模型管理'),
                                     T('推理服务不可用，无法下载。\n' + '请确认软件目录里的 runtime\\ollama 是否完整。'), parent=self)
                self.lbl_prog.configure(text=T('模型服务不可用'))
                return
        self.downloading = name
        self.stop_evt.clear()
        self._last = (time.time(), 0)
        self._speed = 0.0
        self.pb.configure(value=0)
        self.lbl_prog.configure(text=T('准备下载 %s …') % name)
        self.app.log(T('开始下载模型 %s …') % name, 'head')
        self._apply_dl_state()
        threading.Thread(target=self._dl_bg, args=(name,), daemon=True).start()

    def cancel_dl(self):
        if self.downloading:
            self.stop_evt.set()
            self.lbl_prog.configure(text=T('正在取消…'))

    def _dl_bg(self, name):
        try:
            def cb(st, done, total):
                self.q.put(('prog', (st, done, total)))
            r = engine.pull_model(name, on_progress=cb,
                                  should_stop=self.stop_evt.is_set)
            self.q.put(('done', (name, r)))
        except Exception as e:
            self.q.put(('err', (name, str(e))))

    # ---------------------------------------------------------- 进度
    def _on_prog(self, st, done, total):
        zh = STATUS_ZH.get(st, st or T('下载中'))
        now = time.time()
        lt, ld = self._last
        if lt and done > ld and now > lt:
            inst = (done - ld) / (now - lt)
            self._speed = inst if self._speed <= 0 else (self._speed * 0.6 + inst * 0.4)
        self._last = (now, done)
        if total:
            pct = done * 100.0 / total
            self.pb.configure(value=pct)
            eta = ''
            if self._speed > 0:
                eta = T('\u3000剩余约 %s') % fmt_dur((total - done) / self._speed)
            self.lbl_prog.configure(
                text='%s　%.0f%%　%s / %s　%s/s%s'
                     % (zh, pct, human_size(done), human_size(total),
                        human_size(self._speed), eta))
        else:
            self.pb.configure(value=0)
            self.lbl_prog.configure(text='%s …' % zh)

    def _on_done(self, name, r):
        self.downloading = None
        self.pb.configure(value=100 if r == 'success' else 0)
        if r == 'success':
            self.lbl_prog.configure(text=T('%s 下载完成，可直接开始处理') % name)
            self.app.log(T('模型 %s 下载完成') % name, 'ok')
            self.app.var_model.set(name)
            try:
                self.app.cb_model.set(name)
            except Exception:
                pass
        else:
            self.lbl_prog.configure(text=T('已取消下载'))
            self.app.log(T('模型 %s 下载已取消') % name, 'dim')
        self.refresh()

    def _on_err(self, name, msg):
        self.downloading = None
        self.pb.configure(value=0)
        short = msg if len(msg) <= 160 else msg[:159] + '…'
        self.lbl_prog.configure(text=T('下载失败：%s') % short)
        self.app.log(T('模型 %s 下载失败：%s') % (name, short), 'err')
        messagebox.showerror(T('模型管理'), T('下载失败：\n%s') % short, parent=self)
        self.refresh()

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == 'prog':
                    self._on_prog(*payload)
                elif kind == 'done':
                    self._on_done(*payload)
                elif kind == 'err':
                    self._on_err(*payload)
                elif kind == 'dir':
                    self._on_dir(*payload)
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            if self.winfo_exists():
                self.after(150, self._pump)
        except Exception:
            pass

    def on_close(self):
        if self.downloading:
            if not messagebox.askyesno(T('模型管理'), T('正在下载，确定关闭并停止下载吗？'),
                                       parent=self):
                return
            self.stop_evt.set()
        try:
            self.canvas.unbind_all('<MouseWheel>')
        except Exception:
            pass
        self.destroy()


def human_size(n) -> str:
    n = float(n or 0)
    if n < 1024:
        return '%.0f B' % n
    for u in ('KB', 'MB', 'GB', 'TB'):
        n /= 1024.0
        if n < 1024 or u == 'TB':
            return '%.2f %s' % (n, u)
    return '%.2f TB' % n


def _try(fn):
    try:
        return fn()
    except Exception:
        return None


def cut(s: str, n: int) -> str:
    s = s or ''
    return s if len(s) <= n else s[:n - 1] + '…'


def fmt_dur(sec: float) -> str:
    sec = int(max(0, sec))
    if sec < 60:
        return T('%d 秒') % sec
    m, s = divmod(sec, 60)
    if m < 60:
        return T('%d 分 %d 秒') % (m, s)
    h, m = divmod(m, 60)
    return T('%d 时 %d 分') % (h, m)


def clean_stale_mei(max_age_min: float = 15.0) -> tuple[int, int]:
    """清扫 PyInstaller onefile 遗留的 _MEI* 解压目录。

    单文件 exe 每次启动会把解释器解到 %TEMP%\\_MEI随机号。v3.16 起退出时
    用 os._exit 绕过引导器的删目录代码（那会在 DLL 被占用时弹
    「Failed to remove temporary directory」警告框），所以每次运行都会
    留下解压目录，靠本函数在下次启动时兜底清扫（删掉能删的部分）。

    只删同时满足的：① 名字以 _MEI 开头 ② 是目录
                    ③ 最后修改时间在 max_age_min 分钟之前（避免误删
                      正在运行的其他实例）
                    ④ 不是本次运行正在用的目录（sys._MEIPASS）
    删不掉的（仍被占用）直接跳过，不影响主流程。
    返回 (已删除, 删不掉)。
    """
    import shutil
    import tempfile

    tmp = tempfile.gettempdir()
    cur = os.path.abspath(getattr(sys, '_MEIPASS', '') or '')
    now = time.time()
    gone = kept = 0
    try:
        names = [n for n in os.listdir(tmp) if n.upper().startswith('_MEI')]
    except OSError:
        return (0, 0)
    for n in names:
        p = os.path.join(tmp, n)
        try:
            if not os.path.isdir(p) or os.path.abspath(p) == cur:
                continue
            if now - os.path.getmtime(p) < max_age_min * 60.0:
                continue
            shutil.rmtree(p, ignore_errors=True)
            if os.path.exists(p):
                kept += 1
            else:
                gone += 1
        except OSError:
            kept += 1
    return (gone, kept)


def enable_dpi():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    enable_dpi()
    # 后台清扫上次运行遗留的 _MEI* 解压目录（onefile 退出时删不掉的垃圾）
    def _sweep():
        try:
            gone, kept = clean_stale_mei()
            if gone or kept:
                sys.stderr.write('[mei] cleaned=%d skipped=%d\n' % (gone, kept))
        except Exception:
            pass
    threading.Thread(target=_sweep, daemon=True).start()
    root = tk.Tk()
    selftest = '--selftest' in sys.argv
    if selftest:
        root.withdraw()
    app = App(root)
    if selftest:
        app.selftest()
        root.update_idletasks()
        root.destroy()
        try:
            print('SELFTEST OK', flush=True)
        except Exception:
            pass
        return
    try:
        root.mainloop()
    finally:
        # —— 退出收尾（顺序很重要）——
        # 1) 收掉自带服务进程树，释放显存
        engine.shutdown_server()
        # 2) 给后台线程（识别 worker、自动启动等）一点时间自然收尾
        _t0 = time.time()
        for _t in threading.enumerate():
            if _t is threading.main_thread():
                continue
            _t.join(timeout=max(0.1, min(2.0, 4.0 - (time.time() - _t0))))
        # 3) 主动清掉本次解压目录里未被占用的文件（被映射的 DLL 删不掉没关系）
        _mei = getattr(sys, '_MEIPASS', '')
        if _mei:
            shutil.rmtree(_mei, ignore_errors=True)
        # 4) 直接终止进程，不再回到引导器的删目录代码 —— 那段代码遇到还
        #    被占用的 VCRUNTIME140.dll 时会弹「Failed to remove temporary
        #    directory」警告框（就是那个反复出现的弹窗）。残留交给下次
        #    启动时的 clean_stale_mei 清扫。
        os._exit(0)


if __name__ == '__main__':
    main()
