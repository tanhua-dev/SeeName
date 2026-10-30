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
def work_area(win) -> tuple[int, int]:
    """Screen work area (taskbar excluded); falls back to the screen size."""
    try:
        import ctypes
        import ctypes.wintypes as wt
        ctypes.windll.user32.SetProcessDPIAware()
        rc = wt.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0):
            return (max(640, rc.right - rc.left), max(480, rc.bottom - rc.top))
    except Exception:
        pass
    try:
        return (win.winfo_screenwidth(), win.winfo_screenheight())
    except Exception:
        return (1920, 1080)


def fit_to_content(win, min_w=520, min_h=320, max_ratio=0.92, center=True,
                   pad_x=0, pad_y=0):
    """Resize ``win`` to what its content actually asks for, then clamp + centre.

    Dialogs used to hard-code a geometry, so a longer hint text or a taller
    font pushed controls outside the visible area and users had to drag the
    border. Measuring ``winfo_reqwidth/reqheight`` removes that class of bug.
    """
    try:
        win.update_idletasks()
        w = max(int(min_w), win.winfo_reqwidth() + pad_x)
        h = max(int(min_h), win.winfo_reqheight() + pad_y)
        sw, sh = work_area(win)
        w = min(w, int(sw * max_ratio))
        h = min(h, int(sh * max_ratio))
        if center:
            x, y = _center_over(win, w, h)
            win.geometry('%dx%d+%d+%d' % (w, h, x, y))
        else:
            win.geometry('%dx%d' % (w, h))
        return (w, h)
    except Exception:
        return (min_w, min_h)


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
    x = mx + (mw - w) // 2
    y = my + (mh - h) // 3
    x = max(0, min(x, sw - w))
    y = max(0, min(y, sh - h))
    return (x, y)


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
        try:
            variable.trace_add('write', lambda *a: self._draw())
        except Exception:
            pass
        self._draw()

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
        try:
            variable.trace_add('write', lambda *a: self._paint())
        except Exception:
            pass
        self._paint()

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


def gpu_memory(ttl=_GPU_TTL) -> dict:
    """Best-effort VRAM probe (nvidia-smi). 名称/总量缓存更久，空闲显存带 TTL。"""
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
            capture_output=True, creationflags=CRE, timeout=8)
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
