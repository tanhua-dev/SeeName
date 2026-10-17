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

import os
import shutil
import sys
import threading
import time
import tkinter as tk

import engine
from ui.app import App
from ui.platform import clean_stale_mei, enable_dpi


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
        # 1) 请求中止本地推理，待原生调用安全返回后释放模型
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
