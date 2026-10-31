# -*- coding: utf-8 -*-
"""Shared colors, spacing, font selection and ttk styles."""
from __future__ import annotations

import os
from tkinter import ttk

import strings


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
C_SW_OFF = '#c3cbd6'    # 手机式开关：关态轨道（比描边深一档，看得出是个可以拨的东西）
C_SW_KNOB = '#ffffff'   # 开关圆点

# 8pt 间距栅格：把原来 4/6/7/9/10/12/14 七种值收敛成五档
S1, S2, S3, S4, S5 = 4, 8, 12, 16, 24

FONT = 'Microsoft YaHei UI' if os.name == 'nt' else 'PingFang SC'


def button_width(zh_w, en_w=None):
    """ttk 按钮宽度按「0」字符数计：英文比中文长得多，固定宽度会截断。
    中文用原值，英文默认 +6（个别按钮可显式给 en_w）。"""
    return zh_w if strings.lang() == 'zh' else (en_w or zh_w + 6)


def setup_style(root):
    global FONT
    try:
        fams = set(root.tk.call('font', 'families'))
        for cand in ('Microsoft YaHei UI', 'Microsoft YaHei', 'SimHei',
                     'Segoe UI', 'PingFang SC', 'Songti SC',
                     'Helvetica Neue', 'Helvetica'):
            if cand in fams:
                FONT = cand
                break
    except Exception:
        pass
    st = ttk.Style(root)
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

    # 文字链接按钮：结果表标题行右侧的「打开目录 / 识别报告 / 改名台账」。
    # 这三项在原「⋯更多」菜单里是重复入口，摆成文字按钮既不抢主按钮的注意力，
    # 又不用记菜单。
    st.configure('Link.TButton', padding=(6, 3), font=(FONT, 9),
                 background=C_CARD, foreground=C_ACCENT,
                 bordercolor=C_CARD, lightcolor=C_CARD, darkcolor=C_CARD,
                 relief='flat', borderwidth=0)
    st.map('Link.TButton',
           background=[('disabled', C_CARD), ('pressed', C_CARD), ('active', C_CARD)],
           foreground=[('disabled', C_DIS_FG), ('active', C_ACCENT_D),
                       ('pressed', C_ACCENT_P)],
           bordercolor=[('disabled', C_CARD), ('active', C_CARD)])

    # ---- 第 1 步那个大按钮：整块就是一个可以点的框 ----
    # 原来这一行是「文件夹」标签 + 输入框 + 浏览…，三个控件并排，第一眼看不出
    # 该点哪儿。合成一个按钮后，它自己就是「选文件夹」这件事。
    st.configure('Dir.TButton', padding=(14, 13), font=(FONT, 11),
                 background=C_CARD, foreground=C_TEXT, bordercolor=C_LINE_D,
                 lightcolor=C_CARD, darkcolor='#cfd6e0')
    st.map('Dir.TButton',
           background=[('disabled', C_DIS_BG), ('pressed', C_BTN_P),
                       ('active', C_BTN_H)],
           foreground=[('disabled', C_DIS_FG)],
           bordercolor=[('disabled', C_LINE_D), ('focus', C_ACCENT),
                        ('active', C_ACCENT_D)])

    # ---- 主色小按钮：给第 3 步「核对并改名」接棒时用 ----
    # 和 Sec.TButton 同样的 padding，换样式时宽度/高度不跳；只把底色换成主色，
    # 达到「全场只有一个蓝按钮」的观感而不引起版面抖动。
    st.configure('GoSm.TButton', padding=(12, 8), font=(FONT, 10, 'bold'),
                 background=C_ACCENT, foreground='#ffffff',
                 bordercolor=C_ACCENT_D, lightcolor='#4a8ae8',
                 darkcolor=C_ACCENT_D)
    st.map('GoSm.TButton',
           background=[('disabled', C_DIS_BG), ('pressed', C_ACCENT_P),
                       ('active', C_ACCENT_D)],
           foreground=[('disabled', C_DIS_FG)],
           bordercolor=[('disabled', C_LINE_D), ('focus', C_ACCENT_F),
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
    # 表头：底色比白再深一档 + 加深描边，表头与数据行之间才有一条看得见的线
    #（ttk.Treeview 画不了逐行竖线，只能靠表头分隔 + 隔行底色把行分清）
    st.configure('Treeview.Heading', font=(FONT, 9, 'bold'),
                 background='#eef1f6', bordercolor=C_LINE_D,
                 lightcolor='#eef1f6', darkcolor=C_LINE_D, relief='flat',
                 padding=(6, 5))
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
