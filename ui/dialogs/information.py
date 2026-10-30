# -*- coding: utf-8 -*-
"""Help/about dialogs and the small Markdown renderer."""
from __future__ import annotations

import io
import os
import re
import tkinter as tk
from tkinter import ttk

import engine
import strings
from strings import T
from .. import theme
from ..common import APP_AUTHOR, APP_SUB, APP_TITLE, APP_VER


def help_path() -> str:
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
    base = (theme.FONT, 10)
    cfg = txt.tag_configure
    cfg('h1', font=(theme.FONT, 16, 'bold'), spacing1=16, spacing3=6, foreground=theme.C_TEXT)
    cfg('h2', font=(theme.FONT, 13, 'bold'), spacing1=18, spacing3=4, foreground=theme.C_TEXT)
    cfg('h3', font=(theme.FONT, 11, 'bold'), spacing1=12, spacing3=2, foreground=theme.C_TEXT)
    cfg('p', font=base, spacing1=3, spacing3=3, foreground=theme.C_TEXT)
    cfg('li1', font=base, spacing1=3, lmargin1=18, lmargin2=30, foreground=theme.C_TEXT)
    cfg('li2', font=base, spacing1=3, lmargin1=38, lmargin2=50, foreground=theme.C_TEXT)
    cfg('quote', font=base, spacing1=8, spacing3=8, lmargin1=16, lmargin2=16,
        foreground=theme.C_SUB, background=theme.C_ACCENT_L)
    cfg('code', font=('Consolas', 9), spacing1=2, spacing3=2, lmargin1=18,
        lmargin2=18, background='#f2f4f7', foreground=theme.C_TEXT)
    cfg('b', font=(theme.FONT, 10, 'bold'), foreground=theme.C_TEXT)
    cfg('m', font=('Consolas', 9), foreground=theme.C_ACCENT_D)
    cfg('rule', font=base, foreground=theme.C_LINE)

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
        self.configure(bg=theme.C_BG)
        self.transient(master)
        # 880x680 是 1080p 上的设计值；小屏按比例缩，不再顶出屏幕
        from ..controls import fit_window
        fit_window(self, 880, 680, min_w=620, min_h=440)

        head = tk.Frame(self, bg=theme.C_BG)
        head.pack(fill='x', padx=16, pady=(12, 6))
        tk.Label(head, text=T('使用说明'), bg=theme.C_BG, fg=theme.C_TEXT,
                 font=(theme.FONT, 14, 'bold')).pack(side='left')
        tk.Label(head, text='%s　%s' % (APP_TITLE, APP_VER), bg=theme.C_BG, fg=theme.C_SUB,
                 font=(theme.FONT, 9)).pack(side='left', padx=(10, 0), pady=(5, 0))

        wrap = tk.Frame(self, bg=theme.C_CARD, highlightbackground=theme.C_LINE,
                        highlightthickness=1)
        wrap.pack(fill='both', expand=True, padx=14)
        txt = tk.Text(wrap, wrap='word', bg=theme.C_CARD, fg=theme.C_TEXT, bd=0,
                      highlightthickness=0, padx=18, pady=14,
                      cursor='arrow', relief='flat')
        vs = ttk.Scrollbar(wrap, orient='vertical', command=txt.yview)
        txt.configure(yscrollcommand=vs.set)
        vs.pack(side='right', fill='y')
        txt.pack(side='left', fill='both', expand=True)
        self.txt = txt

        md = ''
        p = help_path()
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

        foot = tk.Frame(self, bg=theme.C_BG)
        foot.pack(fill='x', padx=16, pady=12)
        self.var_noauto = tk.BooleanVar(
            value=not bool((engine.config() or {}).get('help_autoshow', True)))
        from ..controls import CheckRow
        CheckRow(foot, T('下次启动不再自动显示'), self.var_noauto).pack(side='left')
        ttk.Button(foot, text=T('关闭'), style='Go.TButton', width=theme.button_width(10, 12),
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
        # Release the Tcl variable on the Tk thread. A closed window may remain
        # in a Python cycle until a model worker triggers garbage collection.
        self.var_noauto = None
        self.destroy()


class AboutDialog(tk.Toplevel):
    """关于：版本 / 许可 / 隐私。

    隐私那一段跟着「联网精确定位」开关变：关着是纯离线（文件不上传），
    开着要如实说明「只发经纬度、不发文件」。
    """

    def __init__(self, master, app=None):
        super().__init__(master)
        self.title(T('关于'))
        self.configure(bg=theme.C_BG)
        self.resizable(False, False)
        self.transient(master)
        frm = tk.Frame(self, bg=theme.C_CARD, padx=18, pady=16)
        frm.pack(fill='both', padx=10, pady=10)
        tk.Label(frm, text=APP_TITLE, bg=theme.C_CARD, fg=theme.C_TEXT,
                 font=(theme.FONT, 14, 'bold')).pack(anchor='w')
        tk.Label(frm, text='%s　%s　%s' % (APP_SUB, APP_AUTHOR, APP_VER),
                 bg=theme.C_CARD, fg=theme.C_SUB, font=(theme.FONT, 9)).pack(anchor='w', pady=(2, 10))

        tk.Label(frm, text=T('隐私'), bg=theme.C_CARD, fg=theme.C_TEXT,
                 font=(theme.FONT, 10, 'bold')).pack(anchor='w')
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
            tk.Label(frm, text='· ' + s, bg=theme.C_CARD, fg=theme.C_SUB, font=(theme.FONT, 9),
                     anchor='w', justify='left', wraplength=430
                     ).pack(anchor='w', pady=(3, 0))

        tk.Label(frm, text=T('数据与许可'), bg=theme.C_CARD, fg=theme.C_TEXT,
                 font=(theme.FONT, 10, 'bold')).pack(anchor='w', pady=(12, 0))
        for s in (T('地名数据 © OpenStreetMap 贡献者（ODbL）· 全球城市 GeoNames（CC-BY 4.0）'),
                  T('联网地址：国内 高德开放平台（lbs.amap.com）· 国外 Nominatim / OpenStreetMap')):
            tk.Label(frm, text='· ' + s, bg=theme.C_CARD, fg=theme.C_SUB, font=(theme.FONT, 9),
                     anchor='w', justify='left', wraplength=430
                     ).pack(anchor='w', pady=(3, 0))

        ttk.Button(frm, text=T('关闭'), width=theme.button_width(10, 12),
                   command=self.destroy).pack(anchor='e', pady=(14, 0))
        self.geometry('+%d+%d' % (master.winfo_rootx() + 160,
                                  master.winfo_rooty() + 120))
        self.grab_set()
        self.focus_set()
        self.bind('<Escape>', lambda e: self.destroy())
