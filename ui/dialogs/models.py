# -*- coding: utf-8 -*-
"""Ollama model management UI; backend behavior is unchanged."""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import engine
from strings import T
from .. import theme
from ..common import APP_TITLE, fmt_dur, human_size, open_path


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
        self.configure(bg=theme.C_BG)
        self.transient(master)
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self._build()
        self.refresh()
        self._pump()

    # ---------------------------------------------------------- 布局
    def _build(self):
        head = tk.Frame(self, bg=theme.C_BG)
        head.pack(fill='x', padx=14, pady=(12, 6))
        tk.Label(head, text=T('视觉识别模型（模型包）'), bg=theme.C_BG, fg=theme.C_TEXT,
                 font=(theme.FONT, 13, 'bold')).pack(anchor='w')
        tk.Label(head, text=T('模型文件不随软件打包，按需在这里单独下载；下载后保存在本软件自带的模型库，' + '换台机器把整个文件夹拷过去即可继续用。'),
                 bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 9), justify='left').pack(anchor='w', pady=(3, 0))
        self.lbl_stat = tk.Label(head, text=T('正在读取…'), bg=theme.C_BG, fg=theme.C_SUB, font=(theme.FONT, 9),
                                 justify='left')
        self.lbl_stat.pack(anchor='w', pady=(5, 0))

        wrap = tk.Frame(self, bg=theme.C_BG)
        wrap.pack(fill='both', expand=True, padx=14)
        self.canvas = tk.Canvas(wrap, bg=theme.C_CARD, highlightthickness=1,
                                highlightbackground=theme.C_LINE)
        vs = ttk.Scrollbar(wrap, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vs.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        vs.pack(side='right', fill='y')
        self.inner = tk.Frame(self.canvas, bg=theme.C_CARD)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>',
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind_all('<MouseWheel>', self._on_wheel)

        foot = tk.Frame(self, bg=theme.C_BG)
        foot.pack(fill='x', padx=14, pady=12)
        self.pb = ttk.Progressbar(foot, mode='determinate', maximum=100)
        self.pb.pack(fill='x')
        self.lbl_prog = tk.Label(foot, text=T('就绪'), bg=theme.C_BG, fg=theme.C_SUB,
                                 font=(theme.FONT, 9), anchor='w')
        self.lbl_prog.pack(fill='x', pady=(4, 8))
        bar = tk.Frame(foot, bg=theme.C_BG)
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
        row = tk.Frame(self.inner, bg=theme.C_CARD)
        row.pack(fill='x', padx=10, pady=6)

        left = tk.Frame(row, bg=theme.C_CARD)
        left.pack(side='left', fill='x', expand=True)
        top = tk.Frame(left, bg=theme.C_CARD)
        top.pack(fill='x', anchor='w')
        tk.Label(top, text='✓' if ok else '↓', bg=theme.C_CARD,
                 fg=theme.C_OK if ok else theme.C_SUB, font=(theme.FONT, 11, 'bold')).pack(side='left')
        tk.Label(top, text=m['name'], bg=theme.C_CARD, fg=theme.C_TEXT,
                 font=(theme.FONT, 10, 'bold')).pack(side='left', padx=(6, 0))
        if m.get('tag'):
            tk.Label(top, text=' %s ' % m['tag'], bg='#e6f1fb', fg='#185fa5',
                     font=(theme.FONT, 8)).pack(side='left', padx=6)
        tk.Label(top, text=m['size'], bg=theme.C_CARD, fg=theme.C_SUB,
                 font=(theme.FONT, 9)).pack(side='left', padx=8)
        tk.Label(left, text=m['note'], bg=theme.C_CARD, fg=theme.C_SUB, font=(theme.FONT, 9),
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
            open_path(d)
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
