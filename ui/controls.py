# -*- coding: utf-8 -*-
"""Large, self-explanatory controls shared by the settings / model dialogs.

Why this module exists
----------------------
On Windows the stock ttk checkbox draws a 13px indicator and the combobox draws
a faint arrow; users who do not already know the convention cannot tell what is
clickable or whether an option is on. Everything here keeps one visual language:

* :class:`CheckRow`   — a 22px box that turns solid blue with a white tick, the
  whole row (box + text) is the hit target, and hovering highlights it.
* :class:`ChoiceRow`  — segmented buttons instead of a dropdown when there are
  only a handful of choices, so all options stay visible.
* :class:`ScrollArea` — a scrollable body, so a dialog can never cut content off.
* :func:`fit_to_content` — size a dialog to its content, clamp it to the work
  area and centre it over its parent (no more "drag the corner to see the rest").
* :func:`gpu_memory` — one cheap probe used to advise Vulkan vs CUDA.
"""
from __future__ import annotations

import subprocess
import tkinter as tk

from strings import T

from . import theme

CRE = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


# --------------------------------------------------------------- 窗口尺寸
# 设计基准：常规 1920x1080 显示器（去掉任务栏约 1040）。所有窗口的尺寸都按
# 这个基准写死成"设计值"，再按实际屏幕等比收缩——小屏不溢出，超大屏不会
# 拉成一整面墙。
BASE_SCREEN = (1920, 1040)


def work_area(win) -> tuple[int, int]:
    """工作区尺寸，单位与 Tk 的 geometry/winfo 一致（DPI 缩放后的逻辑像素）。

    两个坑，都在这里避掉：

    1. **不能用 SystemParametersInfoW(SPI_GETWORKAREA)**。进程一旦
       ``SetProcessDPIAware()``，它按物理像素作答：本机 3413x1440 的屏幕会
       报成 5120x2160，而 Tk 的 geometry 用的是 3413x1440。混用后
       「限制在屏幕 92%」形同虚设，居中坐标也算到屏幕外——窗口贴着屏幕
       右下角冒出来就是这么来的。
    2. **不能在这里 SetProcessDPIAware()**。窗口已经按 96dpi 建好了，此时
       再宣布"本进程自己处理缩放"，Windows 就不再做位图拉伸，界面直接变糊。
    """
    try:
        sw = int(win.winfo_screenwidth())
        sh = int(win.winfo_screenheight())
    except Exception:
        return BASE_SCREEN
    if sw < 320 or sh < 240:          # 还没拿到真实屏幕
        return BASE_SCREEN
    # 任务栏高度 Tk 拿不到，按缩放比例折算 48dp
    bar = 48
    try:
        scale = float(win.tk.call('tk', 'scaling')) / (96 / 72.0)
        bar = int(48 * max(1.0, scale))
    except Exception:
        pass
    return (sw, max(480, sh - bar))


def screen_scale(win) -> float:
    """相对 1920x1080 基准的比例，只缩不放（大屏保持窗口感，不铺满）。"""
    sw, sh = work_area(win)
    return min(1.0, sw / float(BASE_SCREEN[0]), sh / float(BASE_SCREEN[1]))


def fit_window(win, design_w, design_h, min_w=None, min_h=None,
               ratio=0.92, center=True):
    """按设计尺寸开窗，再按屏幕收缩、居中于父窗口。

    ``design_w/h`` 按 1920x1080 写（常规显示器能完整放下的尺寸）；小屏
    （1366x768、1280x800）按比例缩到屏幕 ``ratio`` 以内，下限也随之下调，
    否则 1366x768 上 760 高的窗口会顶出屏幕。
    """
    try:
        win.update_idletasks()
        sw, sh = work_area(win)
        cap_w, cap_h = int(sw * ratio), int(sh * ratio)
        floor_w = min(int(min_w if min_w else design_w * 0.72), cap_w)
        floor_h = min(int(min_h if min_h else design_h * 0.72), cap_h)
        s = screen_scale(win)
        w = min(max(int(design_w * s), floor_w), cap_w)
        h = min(max(int(design_h * s), floor_h), cap_h)
        if center:
            x, y = _center_over(win, w, h)
            win.geometry('%dx%d+%d+%d' % (w, h, x, y))
        else:
            win.geometry('%dx%d' % (w, h))
        try:
            win.minsize(floor_w, floor_h)
        except Exception:
            pass
        return (w, h)
    except Exception:
        return (int(design_w), int(design_h))


def fit_to_content(win, design_w=760, design_h=620, min_w=520, min_h=320,
                   max_ratio=0.92, center=True, pad_x=0, pad_y=0):
    """按内容测量尺寸，落在「设计尺寸」与「屏幕 92%」之间。

    旧版把尺寸写死，提示文字一长、字体一换，控件就被推到可视区外，用户得
    手工拖边框才能看全。这里先量 ``winfo_reqwidth/reqheight``，再以设计值
    为起点、屏幕为上限：

    * 内容比设计值大 → 放大到内容（但仍不超过屏幕）；
    * 内容比设计值小 → 保持设计值，避免窗口缩成一条；
    * 带 ScrollArea 的对话框内容再长也不怕：ScrollArea 自身的 reqheight 是
      固定的小值，超出的部分本来就走滚动条。
    """
    try:
        win.update_idletasks()
        sw, sh = work_area(win)
        cap_w, cap_h = int(sw * max_ratio), int(sh * max_ratio)
        floor_w = min(int(min_w), cap_w)
        floor_h = min(int(min_h), cap_h)
        s = screen_scale(win)
        w = max(int(design_w * s), floor_w, win.winfo_reqwidth() + pad_x)
        h = max(int(design_h * s), floor_h, win.winfo_reqheight() + pad_y)
        w = max(min(w, cap_w), floor_w)
        h = max(min(h, cap_h), floor_h)
        if center:
            x, y = _center_over(win, w, h)
            win.geometry('%dx%d+%d+%d' % (w, h, x, y))
        else:
            win.geometry('%dx%d' % (w, h))
        try:
            win.minsize(floor_w, floor_h)
        except Exception:
            pass
        return (w, h)
    except Exception:
        return (int(design_w), int(design_h))


def _center_over(win, w, h) -> tuple[int, int]:
    sw, sh = work_area(win)
    try:
        master = win.master
        mx, my = master.winfo_rootx(), master.winfo_rooty()
        mw, mh = master.winfo_width(), master.winfo_height()
        if mw <= 1 or mh <= 1:            # 主窗口还没映射出来
            raise ValueError
    except Exception:
        return ((sw - w) // 2, max(0, (sh - h) // 2 - 20))
    # 比父窗口更宽/更高时，相对父窗口居中等于是贴边（x 会被 clamp 到 0），
    # 这种窗口改成相对整个屏幕居中。
    x = mx + (mw - w) // 2 if w <= mw else (sw - w) // 2
    y = my + (mh - h) // 3 if h <= mh else max(0, (sh - h) // 2)
    x = max(0, min(x, sw - w))
    y = max(0, min(y, sh - h))
    return (x, y)


def center_xy(win, w: int, h: int) -> tuple:
    """给「指定尺寸」算一个和 fit_to_content 一致的居中坐标。

    单独拆出来是给那些「先 withdraw 搭界面、算完尺寸再显示」的窗口用：
    窗口还没上屏时 ``winfo_x/y/width`` 全是 1 或 0，只能自己算坐标。
    """
    return _center_over(win, w, h)


# --------------------------------------------------------------- 滚动容器
class ScrollArea(tk.Frame):
    """A vertically scrollable body; use ``.inner`` as the parent of content."""

    def __init__(self, master, bg=theme.C_BG, width=520):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0,
                                width=width, height=200)
        bar = tk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>',
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))

    def bind_wheel(self, top=None):
        """Recursively bind the wheel (Tk does not propagate it to parents)."""

        def _wheel(e):
            try:
                self.canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units')
            except Exception:
                pass

        def _rec(w):
            try:
                w.bind('<MouseWheel>', _wheel)
                for c in w.winfo_children():
                    _rec(c)
            except Exception:
                pass
        _rec(top or self.inner)


# --------------------------------------------------------------- 勾选行
class CheckRow(tk.Frame):
    """Whole-row checkbox: 22px box + tick, big hit area, hover highlight."""

    def __init__(self, master, text, variable, command=None,
                 bg=theme.C_CARD, wrap=380, font_size=11, hint=''):
        super().__init__(master, bg=bg)
        self.var = variable
        self.command = command
        self._bg = bg
        self._enabled = True
        self.box = tk.Canvas(self, width=22, height=22, bg=bg,
                             highlightthickness=0, cursor='hand2')
        self.box.pack(side='left', padx=(0, theme.S2))
        self.lab = tk.Label(self, text=text, bg=bg, fg=theme.C_TEXT,
                            font=(theme.FONT, font_size), anchor='w', justify='left',
                            wraplength=wrap, cursor='hand2')
        self.lab.pack(side='left')
        self.hint = None
        if hint:
            self.hint = tk.Label(self, text=hint, bg=bg, fg=theme.C_SUB,
                                 font=(theme.FONT, 9), anchor='w', justify='left',
                                 wraplength=wrap)
            self.hint.pack(side='left', padx=(theme.S2, 0))
        for w in (self, self.box, self.lab):
            w.bind('<Button-1>', self._toggle)
            w.bind('<Enter>', self._hover_in)
            w.bind('<Leave>', self._hover_out)
        # 变量是全局的（设置窗口关掉后主窗口还在写它），所以窗口销毁时必须解绑，
        # 否则下次改设置会调到已经销毁的控件上，控制台刷一串 TclError。
        self._trace = None
        try:
            self._trace = variable.trace_add('write', lambda *a: self._draw())
            self.bind('<Destroy>', self._drop_trace)
        except Exception:
            pass
        self._draw()

    def _drop_trace(self, event=None):
        if event is not None and getattr(event, 'widget', None) is not self:
            return          # 子控件销毁也会冒泡上来，只处理自己
        if not self._trace:
            return
        try:
            self.var.trace_remove('write', self._trace)
        except Exception:
            pass
        self._trace = None

    # 状态
    def _on(self) -> bool:
        try:
            return bool(self.var.get())
        except Exception:
            return False

    def _toggle(self, _e=None):
        if not self._enabled:
            return
        try:
            self.var.set(not self._on())
        except Exception:
            return
        if self.command:
            self.command()

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        fg = theme.C_TEXT if enabled else theme.C_HINT
        cur = 'hand2' if enabled else 'arrow'
        self.lab.configure(fg=fg, cursor=cur)
        self.box.configure(cursor=cur)
        self._draw()

    def state(self, spec=None):
        """ttk 兼容层：老代码统一调 widget.state(['disabled']) / (['!disabled'])。"""
        if spec is None:
            return () if self._enabled else ('disabled',)
        try:
            first = spec[0] if isinstance(spec, (list, tuple)) and spec else spec
            text = str(first)
            if text == 'disabled':
                self.set_enabled(False)
            elif text == '!disabled':
                self.set_enabled(True)
        except Exception:
            pass
        return ()

    # 绘制
    def _draw(self):
        on = self._on()
        bg = self._bg if self._enabled else theme.C_DIS_BG
        self.configure(bg=bg)
        self.box.configure(bg=bg)
        self.lab.configure(bg=bg)
        if self.hint is not None:
            self.hint.configure(bg=bg)
        try:
            self.box.delete('all')
            if on and self._enabled:
                self.box.create_rectangle(2, 2, 20, 20, fill=theme.C_ACCENT,
                                          outline=theme.C_ACCENT_D, width=1)
                self.box.create_line(6, 11, 10, 16, 17, 6, fill='#ffffff',
                                     width=2.6, capstyle='round', joinstyle='round')
            elif on:
                self.box.create_rectangle(2, 2, 20, 20, fill=theme.C_LINE_D,
                                          outline=theme.C_LINE_D, width=1)
                self.box.create_line(6, 11, 10, 16, 17, 6, fill='#ffffff',
                                     width=2.6, capstyle='round', joinstyle='round')
            else:
                self.box.create_rectangle(2, 2, 20, 20, fill=theme.C_CARD,
                                          outline=theme.C_LINE_D, width=2)
        except Exception:
            pass

    def _hover_in(self, _e=None):
        if self._enabled and not self._on():
            self._tint(theme.C_BTN_H)

    def _hover_out(self, _e=None):
        if self._enabled:
            self._tint(self._bg)

    def _tint(self, color):
        try:
            self.configure(bg=color)
            self.box.configure(bg=color)
            self.lab.configure(bg=color)
            if self.hint is not None:
                self.hint.configure(bg=color)
        except Exception:
            pass


# --------------------------------------------------------------- 手机式开关
class SwitchRow(CheckRow):
    """二选一用手机式开关：能拨的轨道，而不是一个小勾。

    勾选框只有 22px 一个小方块，开没开要凑近看；换成 42×22 的轨道 + 圆点，
    开的时候圆点滑到右边、轨道变主色，隔着一米也看得出状态。
    行为、禁用、悬停高亮、trace 解绑全部复用 CheckRow，不重复实现。
    """

    def __init__(self, master, text, variable, command=None, **kw):
        super().__init__(master, text, variable, command=command, **kw)
        try:
            self.box.configure(width=42)
        except Exception:
            pass
        self._draw()

    def _draw(self):
        on = self._on()
        bg = self._bg if self._enabled else theme.C_DIS_BG
        try:
            self.configure(bg=bg)
            self.box.configure(bg=bg)
            self.lab.configure(bg=bg)
            if self.hint is not None:
                self.hint.configure(bg=bg)
            self.box.delete('all')
            if not self._enabled:
                track, knob = theme.C_LINE_D, '#f2f4f7'
            else:
                track = theme.C_ACCENT if on else theme.C_SW_OFF
                knob = theme.C_SW_KNOB
            # 轨道：两个半圆 + 中间矩形拼成一颗胶囊
            self.box.create_oval(1, 5, 15, 19, fill=track, outline=track)
            self.box.create_oval(27, 5, 41, 19, fill=track, outline=track)
            self.box.create_rectangle(8, 5, 34, 19, fill=track, outline=track)
            cx = 32 if on else 10
            self.box.create_oval(cx - 8, 4, cx + 8, 20, fill=knob,
                                 outline=theme.C_LINE_D, width=1)
        except Exception:
            pass


# --------------------------------------------------------------- 分段选择
class ChoiceRow(tk.Frame):
    """Label + segmented buttons — every option visible, nothing hidden in a menu."""

    def __init__(self, master, label, options, variable, command=None,
                 bg=theme.C_CARD, label_width=None, wrap=340, font_size=10):
        super().__init__(master, bg=bg)
        self.var = variable
        self.command = command
        self._bg = bg
        self._enabled = True
        self._buttons: list[tuple[tk.Label, object]] = []
        self.lab = tk.Label(self, text=label, bg=bg, fg=theme.C_TEXT,
                            font=(theme.FONT, font_size), anchor='w', justify='left',
                            wraplength=wrap)
        if label_width:
            self.lab.configure(width=label_width)
        self.lab.pack(side='left', padx=(0, theme.S2))
        seg = tk.Frame(self, bg=bg)
        seg.pack(side='left')
        for text, value in options:
            b = tk.Label(seg, text=text, font=(theme.FONT, font_size),
                         padx=14, pady=6, cursor='hand2', bd=0,
                         highlightthickness=1, highlightbackground=theme.C_LINE_D)
            b.pack(side='left', padx=(0, theme.S1))
            b._value = value            # 供 _hover 判断当前是否选中
            b.bind('<Button-1>', lambda e, v=value: self._pick(v))
            b.bind('<Enter>', lambda e, w=b: self._hover(w, True))
            b.bind('<Leave>', lambda e, w=b: self._hover(w, False))
            self._buttons.append((b, value))
        self._trace = None
        try:
            self._trace = variable.trace_add('write', lambda *a: self._paint())
            self.bind('<Destroy>', self._drop_trace)
        except Exception:
            pass
        self._paint()

    def _drop_trace(self, event=None):
        if event is not None and getattr(event, 'widget', None) is not self:
            return
        if not self._trace:
            return
        try:
            self.var.trace_remove('write', self._trace)
        except Exception:
            pass
        self._trace = None

    def _pick(self, value):
        if not self._enabled:
            return
        try:
            self.var.set(value)
        except Exception:
            return
        if self.command:
            self.command()

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self.lab.configure(fg=theme.C_TEXT if enabled else theme.C_HINT)
        for b, _v in self._buttons:
            b.configure(cursor='hand2' if enabled else 'arrow')
        self._paint()

    def _selected(self):
        try:
            return self.var.get()
        except Exception:
            return None

    def _cur(self) -> object:
        return self._selected()

    def _paint(self):
        now = self._cur()
        for b, value in self._buttons:
            on = (value == now)
            if on and self._enabled:
                b.configure(bg=theme.C_ACCENT, fg='#ffffff',
                            highlightbackground=theme.C_ACCENT_D)
            elif on:
                b.configure(bg=theme.C_LINE_D, fg='#ffffff',
                            highlightbackground=theme.C_LINE_D)
            else:
                b.configure(bg=theme.C_CARD,
                            fg=theme.C_TEXT if self._enabled else theme.C_HINT,
                            highlightbackground=theme.C_LINE_D)

    def _hover(self, widget, inside):
        if not self._enabled:
            return
        on = (getattr(widget, '_value', None) == self._cur())
        if inside and not on:
            widget.configure(bg=theme.C_BTN_H)
        elif not inside and not on:
            widget.configure(bg=theme.C_CARD)


# --------------------------------------------------------------- 显卡探测
_GPU_CACHE: dict = {}
_GPU_TS = 0.0
_GPU_TTL = 20.0     # nvidia-smi 最长会卡 8 秒，缓存一会儿；界面每 120ms 刷一次不能真去问


def gpu_memory(ttl=_GPU_TTL, timeout=8) -> dict:
    """Best-effort VRAM probe (nvidia-smi). 名称/总量缓存更久，空闲显存带 TTL。

    ``timeout`` 单独可调：界面轮询用默认 8 秒（驱动打嗝也不能让界面卡死），
    首次启动判断「这台机器值不值得走智能命名」时传 2 秒——那是在启动路径上，
    宁可判错成 CPU，也不能让人对着空窗口等 8 秒。
    """
    global _GPU_TS
    import time
    now = time.time()
    info = dict(_GPU_CACHE)
    if info and now - _GPU_TS < ttl:
        return info
    try:
        p = subprocess.run(
            ['nvidia-smi', '--query-gpu=name,memory.total,memory.free',
             '--format=csv,noheader,nounits'],
            capture_output=True, creationflags=CRE, timeout=timeout)
        line = (p.stdout or b'').decode('utf-8', 'replace').strip().splitlines()
        if line:
            parts = [x.strip() for x in line[0].split(',')]
            if len(parts) >= 3:
                info = {'name': parts[0],
                        'total_mb': int(float(parts[1])),
                        'free_mb': int(float(parts[2]))}
                _GPU_CACHE.update(info)
                _GPU_TS = now
    except Exception:
        pass
    return info


def gpu_advice(recommend_mb=4500) -> str:
    """One-line verdict shown in the acceleration panel."""
    g = gpu_memory()
    if not g.get('name'):
        return T('未检测到 NVIDIA 显卡 · 建议用 Vulkan（兼容 Intel / AMD）或 CPU')
    free = g.get('free_mb', 0) / 1024.0
    total = g.get('total_mb', 0) / 1024.0
    head = T('%s · 可用显存 %.1f / %.1f GB') % (g['name'], free, total)
    if g.get('free_mb', 0) >= recommend_mb:
        return head + T(' · CUDA 可用')
    if g.get('free_mb', 0) >= 3000:
        return head + T(' · 偏紧，4B 模型建议用 Vulkan（或先关掉占显存的程序）')
    return head + T(' · 不足，建议用 Vulkan')
