# -*- coding: utf-8 -*-
"""Review, batch replacement and rename-result dialogs."""
from __future__ import annotations

import os
import tkinter as tk
import traceback
from tkinter import messagebox, ttk

import engine
from strings import T
from .. import theme
from ..common import APP_TITLE, cut, open_path
from ..controls import fit_window


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

    def __init__(self, master, directory, items, on_rollback=None):
        super().__init__(master)
        self.directory = directory
        # 「回滚本目录改名」原来在 ⋯更多 菜单里：它只在这个目录改过名后才有意义，
        # 和这张核对表天然是一件事，所以搬进这里的页脚。
        self.on_rollback = on_rollback
        self.items = [dict(x) for x in items]
        # rows 是全部数据（dict，不是控件）；frame 只在当前页存在
        self.rows: list[dict] = []
        self.page = 0
        self.page_size = 100
        self._suspend = False    # 批量改值时先挂起预览，改完只重算当前页
        self.applied = None     # (done, failed, skipped)
        self.detail = None      # 改名明细：谁改成了什么、谁失败、谁被跳过
        self.title(T('核对并改名 · %s') % APP_TITLE)
        fit_window(self, 1180, 700, min_w=900, min_h=460)
        self.configure(bg=theme.C_BG)
        self.transient(master)
        self.grab_set()
        self.protocol('WM_DELETE_WINDOW', self.on_cancel)
        self._build()
        self.bind('<Escape>', lambda *a: self.on_cancel())

    # ---------------------------------------------------------------- 构建
    def _build(self):
        head = tk.Frame(self, bg=theme.C_BG)
        head.pack(fill='x', padx=14, pady=(12, 2))
        tk.Label(head, text=T('核对并改名'), bg=theme.C_BG, fg=theme.C_TEXT,
                 font=(theme.FONT, 14, 'bold')).pack(side='left')
        tk.Label(head, text=T('时间、地名、描述直接改；时间留空不改名；' + '格子后面 ↓ 把值填到下面连续空白（同一场景填一次就够）'),
                 bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 9)).pack(side='left', padx=(10, 0),
                                                         pady=(4, 0))
        self.lbl_stat = tk.Label(head, text='', bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 9))
        self.lbl_stat.pack(side='right', pady=(4, 0))

        # 表头
        hd = tk.Frame(self, bg=theme.C_BG)
        hd.pack(fill='x', padx=14)
        for txt, w in (('#', 4), (T('原文件名'), 32), (T('拍摄时间'), 15), (T('地名'), 15),
                       (T('描述'), 12), (T('改完的新文件名'), 30)):
            tk.Label(hd, text=txt, width=w, anchor='w', bg=theme.C_BG, fg=theme.C_SUB,
                     font=(theme.FONT, 9, 'bold')).pack(side='left', padx=(0, 4))

        wrap = tk.Frame(self, bg=theme.C_CARD, highlightbackground=theme.C_LINE, highlightthickness=1)
        wrap.pack(fill='both', expand=True, padx=14, pady=(0, 0))
        self.canvas = tk.Canvas(wrap, bg=theme.C_CARD, highlightthickness=0)
        vs = ttk.Scrollbar(wrap, orient='vertical', command=self.canvas.yview)
        hs = ttk.Scrollbar(wrap, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side='right', fill='y')
        hs.pack(side='bottom', fill='x')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.rows_box = tk.Frame(self.canvas, bg=theme.C_CARD)
        self.canvas.create_window((0, 0), window=self.rows_box, anchor='nw')
        self.rows_box.bind(
            '<Configure>',
            lambda *a: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind_all('<MouseWheel>', self._on_wheel)

        self._build_pagebar()
        for i, it in enumerate(self.items, 1):
            self._make_row(it, it.get('index') or i)
        self._apply_filter()

        foot = tk.Frame(self, bg=theme.C_BG)
        foot.pack(fill='x', padx=14, pady=12)
        ttk.Button(foot, text=T('查找替换…'), style='Sec.TButton', width=theme.button_width(12, 18),
                   command=self.open_replace).pack(side='left')
        if self.on_rollback is not None:
            # 放左边、用次要样式：这是「撤回」动作，不能和主按钮抢位置
            ttk.Button(foot, text=T('回滚本目录改名…'), style='Sec.TButton',
                       width=theme.button_width(15, 18),
                       command=self._rollback).pack(side='left', padx=(10, 0))
        tk.Label(foot, text=T('时间留空的行不会被改名，原文件保持不动。'),
                 bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 9)).pack(side='left', padx=(12, 0))
        ttk.Button(foot, text=T('应用改名'), style='Go.TButton',
                   command=self.on_apply).pack(side='right')
        ttk.Button(foot, text=T('暂不改名，关闭'), style='Sec.TButton', width=theme.button_width(14, 18),
                   command=self.on_cancel).pack(side='right', padx=(0, 10))

    def _rollback(self):
        """先关窗再回滚：文件名会全部变回去，这张表随即作废。"""
        if self.on_rollback is None:
            return
        self.on_cancel()
        self.on_rollback()

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
        f = tk.Frame(self.rows_box, bg=theme.C_CARD)
        tk.Label(f, text=str(row['idx']), width=4, anchor='e', bg=theme.C_CARD, fg=theme.C_SUB,
                 font=(theme.FONT, 9)).pack(side='left', padx=(0, 4))
        kind = '照' if it.get('kind') == 'photo' else '影'
        tag = '' if it.get('ok', True) else T('（失败）')
        tk.Label(f, text='%s %s%s' % (kind, cut(it.get('old', ''), 28), tag),
                 width=32, anchor='w', bg=theme.C_CARD,
                 fg=(theme.C_TEXT if it.get('ok', True) else theme.C_ERR),
                 font=(theme.FONT, 9)).pack(side='left', padx=(0, 4))
        ttk.Entry(f, textvariable=vs['time'], width=15, font=(theme.FONT, 9)).pack(
            side='left', padx=(0, 4))
        e_place = ttk.Entry(f, textvariable=vs['place'], width=15, font=(theme.FONT, 9))
        e_place.pack(side='left', padx=(0, 0))
        e_place.bind('<FocusIn>', lambda *a: self._mark_old(row, 'place'))
        self._fill_btn(f, row, 'place')
        e_desc = ttk.Entry(f, textvariable=vs['desc'], width=12, font=(theme.FONT, 9))
        e_desc.pack(side='left', padx=(4, 0))
        e_desc.bind('<FocusIn>', lambda *a: self._mark_old(row, 'desc'))
        self._fill_btn(f, row, 'desc')
        tk.Label(f, textvariable=row['new'], width=30, anchor='w', bg=theme.C_CARD,
                 fg=theme.C_ACCENT, font=(theme.FONT, 9)).pack(side='left')
        row['frame'] = f
        self._preview(row)               # 显示前先走一遍「时间空白=不改名」的判断

    def _fill_btn(self, parent, row, field):
        """格子后面的小 ↓：把这一格的值填到下面**连续的空白格**（跨页有效）。

        同一场景的视频/照片地名都一样，填一次就够，不用一格一格抄。
        """
        # 静止时给一块浅灰蓝底（白底上才能看出它是可点的），悬浮变主色蓝底白字
        b = tk.Button(parent, text='↓', width=2, bd=0, relief='flat', cursor='hand2',
                      bg='#eef2f7', fg=theme.C_ACCENT, activebackground=theme.C_ACCENT_D,
                      activeforeground='#ffffff', font=(theme.FONT, 10, 'bold'),
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
            tk.Label(w, text=text, justify='left', bg='#fffbe6', fg=theme.C_TEXT,
                     font=(theme.FONT, 9), relief='solid', borderwidth=1).pack(padx=6, pady=5)
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
        bar = tk.Frame(self, bg=theme.C_BG)
        bar.pack(fill='x', padx=14, pady=(6, 0))
        self.btn_prev = ttk.Button(bar, text=T('← 上一页'), style='Sec.TButton', width=theme.button_width(11, 13),
                                   command=lambda: self._turn(-1))
        self.btn_prev.pack(side='left')
        self.btn_next = ttk.Button(bar, text=T('下一页 →'), style='Sec.TButton', width=theme.button_width(11, 13),
                                   command=lambda: self._turn(1))
        self.btn_next.pack(side='left', padx=(8, 0))
        self.lbl_page = tk.Label(bar, text='', bg=theme.C_BG, fg=theme.C_TEXT, font=(theme.FONT, 9))
        self.lbl_page.pack(side='left', padx=(12, 0))
        tk.Label(bar, text=T('每页'), bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 9)).pack(
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
        fit_window(self, 520, 320, min_w=460, min_h=260)
        self.configure(bg=theme.C_BG)
        self.transient(master)
        self.grab_set()
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.bind('<Escape>', lambda *a: self.on_close())

        tk.Label(self, text=T('查找替换'), bg=theme.C_BG, fg=theme.C_TEXT,
                 font=(theme.FONT, 14, 'bold')).pack(anchor='w', padx=14, pady=(12, 2))
        tk.Label(self, text=T('把表里所有「完全相同」的这一项换成新值（不分相邻，全表生效）'),
                 bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 9)).pack(anchor='w', padx=14)

        box = self._card()
        self.var_field = tk.StringVar(value='place')
        r0 = tk.Frame(box, bg=theme.C_CARD)
        r0.pack(fill='x', pady=(0, 8))
        tk.Label(r0, text=T('改哪一列'), bg=theme.C_CARD, fg=theme.C_TEXT, font=(theme.FONT, 10),
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
            r = tk.Frame(box, bg=theme.C_CARD)
            r.pack(fill='x', pady=3)
            tk.Label(r, text=label, bg=theme.C_CARD, fg=theme.C_TEXT, font=(theme.FONT, 10),
                     width=8, anchor='w').pack(side='left')
            e = ttk.Entry(r, textvariable=var, font=(theme.FONT, 10))
            e.pack(side='left', fill='x', expand=True)
            var.trace_add('write', lambda *a: self._recount())
        # 已有的值列出来，点一下就填进查找框，省得手打错别字
        self.lbl_hit = tk.Label(box, text='', bg=theme.C_CARD, fg=theme.C_SUB, font=(theme.FONT, 9),
                                anchor='w', wraplength=450, justify='left')
        self.lbl_hit.pack(fill='x', pady=(8, 2))
        self._build_values(box)

        foot = tk.Frame(self, bg=theme.C_BG)
        foot.pack(fill='x', padx=14, pady=10)
        ttk.Button(foot, text=T('替换全部'), style='Go.TButton',
                   command=self.on_go).pack(side='right')
        ttk.Button(foot, text=T('取消'), style='Sec.TButton', width=theme.button_width(10, 10),
                   command=self.on_close).pack(side='right', padx=(0, 10))
        self._recount()

    def _card(self):
        f = tk.Frame(self, bg=theme.C_CARD, highlightbackground=theme.C_LINE, highlightthickness=1)
        f.pack(fill='both', expand=True, padx=14, pady=(8, 0))
        inner = tk.Frame(f, bg=theme.C_CARD)
        inner.pack(fill='both', expand=True, padx=12, pady=10)
        return inner

    def _build_values(self, box):
        """把表里出现过的值列成小按钮，点一下填进查找框（免得手打错别字）。"""
        tk.Label(box, text=T('表里现有的值（点一下填入查找框）：'), bg=theme.C_CARD, fg=theme.C_SUB,
                 font=(theme.FONT, 9)).pack(anchor='w', pady=(4, 2))
        self.chips = tk.Frame(box, bg=theme.C_CARD)
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
                      fg=theme.C_TEXT, activebackground=theme.C_ACCENT_D,
                      activeforeground='#ffffff', font=(theme.FONT, 9),
                      cursor='hand2', command=lambda s=v: self.var_find.set(s)).pack(
                side='left', padx=(0, 6), pady=2)

    def _recount(self):
        f = self.var_find.get().strip()
        n = sum(1 for r in self.rows
                if r['vs'][self.var_field.get()].get().strip() == f) if f else 0
        to = self.var_to.get().strip()
        if not f:
            self.lbl_hit.configure(text=T('先填要查找的内容（或从下面点一个现有的值）'), fg=theme.C_SUB)
        elif not to:
            self.lbl_hit.configure(text=T('命中 %d 行。还要填「替换为」的内容。') % n, fg=theme.C_WARN)
        else:
            self.lbl_hit.configure(text=T('命中 %d 行：「%s」→「%s」') % (n, f, to), fg=theme.C_OK)

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
        fit_window(self, 760, 480, min_w=560, min_h=340)
        self.configure(bg=theme.C_BG)
        self.transient(master)
        self.grab_set()
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.bind('<Escape>', lambda *a: self.on_close())

        head = tk.Frame(self, bg=theme.C_BG)
        head.pack(fill='x', padx=14, pady=(14, 2))
        tk.Label(head, text=T('改名完成'), bg=theme.C_BG, fg=theme.C_TEXT,
                 font=(theme.FONT, 15, 'bold')).pack(side='left')
        tk.Label(head, text=cut(directory, 60), bg=theme.C_BG, fg=theme.C_SUB,
                 font=(theme.FONT, 9)).pack(side='left', padx=(10, 0), pady=(6, 0))

        nums = tk.Frame(self, bg=theme.C_BG)
        nums.pack(fill='x', padx=14, pady=(8, 6))
        for txt, val, col in ((T('已改名'), done, theme.C_OK), (T('未改动'), skipped, theme.C_SUB),
                              (T('失败'), failed, theme.C_ERR if failed else theme.C_SUB)):
            f = tk.Frame(nums, bg=theme.C_CARD, highlightbackground=theme.C_LINE,
                         highlightthickness=1)
            f.pack(side='left', padx=(0, 10), ipadx=14, ipady=6)
            tk.Label(f, text=str(val), bg=theme.C_CARD, fg=col,
                     font=(theme.FONT, 18, 'bold')).pack()
            tk.Label(f, text=txt, bg=theme.C_CARD, fg=theme.C_SUB, font=(theme.FONT, 9)).pack()

        wrap = tk.Frame(self, bg=theme.C_BG)
        wrap.pack(fill='both', expand=True, padx=14, pady=(0, 6))
        self.txt = tk.Text(wrap, height=12, bg=theme.C_CARD, fg=theme.C_TEXT, relief='flat',
                           font=('Consolas', 9), wrap='none')
        vs = ttk.Scrollbar(wrap, orient='vertical', command=self.txt.yview)
        hs = ttk.Scrollbar(wrap, orient='horizontal', command=self.txt.xview)
        self.txt.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.txt.grid(row=0, column=0, sticky='nsew')
        vs.grid(row=0, column=1, sticky='ns')
        hs.grid(row=1, column=0, sticky='ew')
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.txt.tag_configure('h', foreground=theme.C_TEXT, font=(theme.FONT, 10, 'bold'))
        self.txt.tag_configure('ok', foreground=theme.C_OK)
        self.txt.tag_configure('bad', foreground=theme.C_ERR)
        self.txt.tag_configure('dim', foreground=theme.C_SUB)
        self._fill(ok, bad, skip)
        self.txt.configure(state='disabled')

        foot = tk.Frame(self, bg=theme.C_BG)
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
            open_path(self.directory)

    def open_ledger(self):
        p = engine.state_paths(self.directory)['ledger']
        if os.path.exists(p):
            open_path(p)

    def on_close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
