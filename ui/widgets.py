# -*- coding: utf-8 -*-
"""Tooltip widgets shared by the main window and dialogs."""
from __future__ import annotations

import tkinter as tk

from strings import T
from . import theme


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
            tk.Label(w, text='\n'.join(lines), justify='left', bg='#fffbe6', fg=theme.C_TEXT,
                     font=(theme.FONT, 9), relief='solid', borderwidth=1,
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
            tk.Label(w, text=txt, justify='left', bg='#fffbe6', fg=theme.C_TEXT,
                     font=(theme.FONT, 9), relief='solid', borderwidth=1,
                     wraplength=620).pack(padx=6, pady=5)
            w.update_idletasks()
            # 靠右界线下方：越界就翻到左边，免得 tooltip 被屏幕边裁掉
            x = min(px + 14, max(0, self.w.winfo_screenwidth() - w.winfo_width() - 8))
            y = min(py + 18, max(0, self.w.winfo_screenheight() - w.winfo_height() - 8))
            w.wm_geometry('+%d+%d' % (x, y))
        except Exception:
            pass


class DisabledTip(Hover):
    """按钮禁用时，鼠标停上去说明为什么点不了。

    置灰的按钮不给理由，用户只会反复点、反复没反应。「先选择文件夹」这种
    一句话解释，放悬停里不占版面，但点不动的时候有人告诉你为什么。
    """

    def _show(self):
        try:
            st = self.w.state()
            if 'disabled' not in (st if isinstance(st, (tuple, list)) else ()):
                return          # 能点的时候不打扰
        except Exception:
            pass
        super()._show()


class Toast:
    """底部浮出的一句话小结，几秒后自己消失。

    识别跑完的统计不该常驻——状态栏只留「就绪 / 正在识别」一句，也不值得
    为它弹一个要点确定的对话框。所以做成贴在状态栏上方的浮条：出现、读一眼、
    自己走。带可选的一行小字按钮（例如「查看详细报告」）。
    """

    def __init__(self, master, delay=2800):
        self.master = master
        self.delay = delay
        self.frame = None
        self.job = None

    def hide(self):
        if self.job:
            try:
                self.master.after_cancel(self.job)
            except Exception:
                pass
            self.job = None
        if self.frame is not None:
            try:
                self.frame.destroy()
            except Exception:
                pass
            self.frame = None

    def show(self, text, action_text='', action=None):
        self.hide()
        try:
            f = tk.Frame(self.master, bg=theme.C_CARD, highlightthickness=1,
                         highlightbackground=theme.C_LINE_D)
            tk.Label(f, text=text, bg=theme.C_CARD, fg=theme.C_TEXT,
                     font=(theme.FONT, 10)).pack(side='left', padx=(14, 0), pady=9)
            if action_text and action is not None:
                lb = tk.Label(f, text=action_text, bg=theme.C_CARD, fg=theme.C_ACCENT,
                              font=(theme.FONT, 9, 'underline'), cursor='hand2')
                lb.pack(side='left', padx=(12, 14), pady=9)
                lb.bind('<Button-1>', lambda e: (self.hide(), action()))
            else:
                tk.Frame(f, width=14, bg=theme.C_CARD).pack(side='left')
            f.place(relx=0.5, rely=1.0, anchor='s', y=-36)
            f.lift()
            self.frame = f
            self.job = self.master.after(self.delay, self.hide)
        except Exception:
            pass
