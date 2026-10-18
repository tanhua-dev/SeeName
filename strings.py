# -*- coding: utf-8 -*-
"""显影 SeeName（作者：tanhua）界面多语言。

key 直接就是中文字符串：T('照片') 在中文界面返回「照片」，英文界面返回 "Photos"；
查不到的 key 原样返回中文（这是有意的兜底）——以下几类刻意**不翻译**：

  * 送给视觉模型的提示词（PROMPT / PHOTO_PROMPT 的片段）：提示词语言和
    parse_out() 里解析用的「描述=」是绑定的，换成英文会改模型行为；
  * DESC_BAD 里的词、以及 (街道|镇|乡|办事处)$ 这类正则：要和模型输出做
    精确比较，不能动；
  * 自检用的示例文件名 / 示例地名（成都肖家河街道、洗车、林间散步…）；
  * 目录路径（D:\\实验 等）。

语言来源：config.json 的 "lang" 字段（zh / en），默认 zh。
切换语言：strings.set_lang(lang) + 主界面保存 config，重启后生效。
"""
import io
import json
import os
import sys

LANGS = ('zh', 'en')
_CUR = 'zh'


def _config_path() -> str:
    # 与 engine 的 _app_dir() 同一套逻辑：打包后取 exe 所在目录（不是 _MEIPASS）
    if getattr(sys, 'frozen', False):
        root = os.path.dirname(os.path.abspath(sys.executable))
    else:
        root = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, 'config.json')


def _load_lang():
    global _CUR
    try:
        cfg = json.load(io.open(_config_path(), encoding='utf-8'))
        lang = str(cfg.get('lang') or 'zh').strip().lower()
        if lang in LANGS:
            _CUR = lang
    except Exception:
        pass


_load_lang()


def set_lang(lang: str):
    global _CUR
    if lang in LANGS:
        _CUR = lang


def lang() -> str:
    return _CUR


def T(s: str, **kw) -> str:
    """取当前语言下的文案；查不到就原样返回（保持中文）。"""
    if _CUR != 'en':
        return s
    out = EN.get(s)
    return s if out is None else out


# ---------------------------------------------------------------- 英文文案
# 说明：%s / %d / %.1f 等占位符与中文版一一对应，\n 与全角空格原样保留；
#       英文里适当加词让语句自然，但不敢改动占位符的数量和顺序。
EN = {}
EN.update({
    # ↓ v3.16 界面改版新增（操作行 / 底部状态栏 / 右侧栏 / ⋯更多 菜单）
    '⚙ 设置': '⚙ Settings',
    '⋯ 更多': '⋯ More',
    '▸ 运行日志': '▸ Run log',
    '▾ 运行日志': '▾ Run log',
    '显示运行日志': 'Show run log',
    '隐藏运行日志': 'Hide run log',
    '展开侧栏（预览 + 设置）': 'Expand sidebar (preview + settings)',
    '收起侧栏': 'Collapse sidebar',
    '届时显示抽帧缩略图': 'Frame thumbnails show up here',
    '设置': 'Setup',
    '%s\n%s': '%s\n%s',
    '点这里启动模型服务': 'Click here to start the model service',
    '端口 %d': 'Port %d',
    '模型库 %s': 'Models %s',
    '模型': 'Model',
    '最多处理个数': 'Max items',
    '服务在线 · 还没下载任何模型（可在「⋯更多 → 模型管理…」下载）':
        'Service online · no model yet (get one via "⋯ More → Manage models…")',
    '%s 未下载 · 在「⋯更多 → 模型管理…」下载':
        '%s not downloaded · get it via "⋯ More → Manage models…"',
    '已就绪 · %s · %.1fG %s': 'Ready · %s · %.1fG %s',
    '服务在线 · 模型待命（点「开始识别」时自动加载，无需手动）':
        'Service online · model on standby (loads automatically on start)',
    '自检：底部状态栏（●模型状态 / 端口 / 模型库）切换正常':
        'Self-test: status bar (model dot / port / models dir) switches fine',
    '自检：⋯更多菜单构建正常（%d 项：核对改名/回滚/打开…/模型管理/日志）':
        'Self-test: ⋯More menu OK (%d items: review/rollback/open…/manage/log)',
    '自检：⋯更多菜单项禁用/启用正常':
        'Self-test: ⋯More entries enable/disable fine',
    '自检：右侧栏展开/收起正常（收起只剩 ⚙ 按钮）':
        'Self-test: sidebar collapse/expand fine (⚙ only when collapsed)',
    '自检：运行日志抽屉展开/收起正常':
        'Self-test: run-log drawer collapse/expand fine',
    '自检：停止按钮显隐（未开始/识别中/停止中/已停止/已完成 = %s）':
        'Self-test: stop-button visibility (idle/running/stopping/stopped/done = %s)',
    # ↑ v3.16 新增结束
    '语言': 'Language',
    '语言已切换，重启软件后生效。':
        'Language changed. Restart the program to apply it.',
    '2026手机视频': 'Phone videos 2026',
    '=== 参考照片：扫描 %d 张（缓存命中 %d），其中 %d 张带 GPS':
        '=== Reference photos: scanned %d (cache hits %d), %d with GPS',
    '\n\n===== 启动 %s（端口 %d，模型库 %s）=====\n':
        '\n\n===== Starting %s (port %d, models dir %s) =====\n',
    '\n\n改名后可以在主界面点「回滚本目录改名」还原。\n确定继续？':
        '\n\nAfter renaming you can undo it with "Roll back renames" on the main window.\nContinue?',
    '\n%d 帧 / %s / 宫格 %d×%d / %d token':
        '\n%d frames / %s / grid %d×%d / %d tokens',
    '\n=== 改名 %d 个，跳过 %d 个（时间空白），失败 %d 个\n':
        '\n=== Renamed %d, skipped %d (no time), failed %d\n',
    '\n=== 改名对照（可据此回滚）===\n':
        '\n=== Rename log (for rollback) ===\n',
    '\n=== 识别完成 %d 个，失败 %d 个，用时 %.1fs\n':
        '\n=== Analyzed %d, failed %d, took %.1fs\n',
    '\n地名来源：%s%s': '\nLocation source: %s%s',
    '\n失败原因：%s': '\nReason: %s',
    '\n没有可改名的条目。': '\nNothing to rename.',
    '\n照片：只读拍摄时间与 GPS，不送模型':
        '\nPhotos: only EXIF time & GPS are read, no model',
    '\n原图 1 张 / %s / %d token':
        '\n1 original image / %s / %d tokens',
    '       原始输出: %s\n': '       raw output: %s\n',
    '    读不到时间：%s': '    no time: %s',
    '   （点位库拿街道办代表整片区域，边界上常常一整段都判错）':
        '   (the location DB uses one admin centre as the point for a whole county, so long stretches near borders are often mislabeled)',
    '  !! 改名失败 %s：%r': '  !! rename failed %s: %r',
    '  !! 改名失败 %s：目标名已存在': '  !! rename failed %s: target name exists',
    '  …另有 %d 条未列出\n': '  …%d more not shown\n',
    '  回滚失败 %s: %r': '  rollback failed %s: %r',
    ' · 用时 %s': ' · %s',
    ' 处理范围 ': ' Scope ',
    '!! 推理服务不可用（自带运行时启动失败），任务终止':
        '!! Inference service unavailable (bundled runtime failed to start), task aborted',
    '%d 分 %d 秒': '%dm %ds',
    '%d 时 %d 分': '%dh %dm',
    '%d 秒': '%ds',
    '%s\n→ %s\n\n地名：%s\n描述：%s\n':
        '%s\n→ %s\n\nLocation: %s\nDescription: %s\n',
    '%s\n→ %s\n\n（这一条是上次识别留下的，没有更多细节）':
        '%s\n→ %s\n\n(from a previous run, no further details)',
    '%s · 端口 %d · 模型库 %s': '%s · port %d · models dir %s',
    '%s 下载完成，可直接开始处理':
        '%s downloaded. You can start processing now.',
    '(无描述)': '(no description)',
    '=== 待处理 %d 个（视频 %d ／ 照片 %d）':
        '=== %d to process (videos %d / photos %d)',
    '=== 推理运行时：%s（%s）': '=== Runtime: %s (%s)',
    '=== 收到停止请求，已在第 %d 个停下':
        '=== Stop requested, stopped at #%d',
    '=== 文件名尚未改动。可在结果表里核对/修改「时间、地点、描述」，':
        '=== Filenames are unchanged yet. Review/edit time, location and description in the table,',
    '=== 模型: %s   ctx=%d   宫格像素预算 %d':
        '=== Model: %s   ctx=%d   grid pixel budget %d',
    '=== 模型库：%s': '=== Models dir: %s',
    '=== 模型热身%s': '=== Warming up model%s',
    '=== 没有 GPS 的一律留空（相邻借鉴已下线），读不到时间的也留空，之后在表格里手填':
        '=== Files without GPS are left blank (neighbor borrowing is off); files without time are left blank too. Fill them in the table later',
    '=== 照片只读 EXIF 时间与 GPS，不调模型，秒级完成':
        '=== Photos: only EXIF time & GPS are read, no model, done in seconds',
    '=== 目录: %s': '=== Directory: %s',
    '=== 识别完成：成功 %d ／ 失败 %d ／ 用时 %.1fs':
        '=== Done: %d ok / %d failed / %.1fs',
    '=== 预扫描：视频自带 GPS %d 个 ｜ 照片带 GPS %d/%d 张':
        '=== Pre-scan: videos with GPS %d | photos with GPS %d/%d',
    'Google 多模态小模型，可作为备选对比。':
        'Small Google multimodal model; usable as a fallback for comparison.',
    'Ollama 官方源': 'Ollama official',
    '[%d/%d] %s !! 异常 %r\n': '[%d/%d] %s !! error %r\n',
    '[%d/%d] %s 失败：%s': '[%d/%d] %s failed: %s',
    '[%d/%d] %s 异常：%r': '[%d/%d] %s error: %r',
    '[错误] %s': '[error] %s',
    '[错误] 启动自带运行时失败：%r':
        '[error] failed to start bundled runtime: %r',
    '[错误] 无法使用该目录：%r': '[error] cannot use this directory: %r',
    '[错误] 运行时进程提前退出（code=%s），详见 %s':
        '[error] runtime process exited early (code=%s), see %s',
    '——— 已手动停止（已完成的 %d 条已写进计划表）———':
        '--- Stopped manually (%d finished items were saved to the plan) ---',
    '——— 开始识别　目录：%s　%s ———':
        '--- Analysis started  dir: %s  %s ---',
    '——— 识别结束 ———': '--- Analysis finished ---',
    '← 上一页': '← Previous',
    '↻ 再识别一次': '↻ Analyze again',
    '① 把这一格的%s填到下面连续的空白格（遇到有值就停，跨页有效）\n':
        '① Fill this cell\'s %s down through consecutive blanks (stops at a filled cell, works across pages)\n',
    '② 改过这一格后点它：把上下连续的同名格一起换成新值\n':
        '② After editing a cell, click it: replaces the contiguous same-value cells above and below with the new value\n',
    '▶ 开始识别': '▶ Start analysis',
    '▶ 重新开始识别': '▶ Restart analysis',
    '● 模型启动中…': '● Starting model…',
    '● 模型未启动': '● Model stopped',
    '● 模型运行中': '● Model running',
    '● 模型运行中 · 待下载': '● Running · model missing',
    '● 已手动停止 · 完成 %d/%d': '● Stopped manually · %d/%d done',
    '● 未开始': '● Not started',
    '● 正在停止…': '● Stopping…',
    '● 识别中 %d/%d': '● Analyzing %d/%d',
    '● 识别中…': '● Analyzing…',
    '● 识别完成 · %d 条%s': '● Analysis done · %d items%s',
    '　剩余约 %s': ' about %s left',
    '上一代视觉模型；7B 在本机溢出到 CPU，单张图约 191 秒，不推荐。':
        'Previous-gen vision model; the 7B spills to CPU on this machine (~191s per image), not recommended.',
    '下一页 →': 'Next →',
    '下载': 'Download',
    '下载中': 'Downloading',
    '下载失败：\n%s': 'Download failed:\n%s',
    '下载失败：%s': 'Download failed: %s',
    '下载源…': 'Source…',
    '两个都没勾选 · 不会处理任何文件':
        'Neither is checked · no files will be processed',
    '个（0=全部）': ' (0=all)',
    '使用': 'Use',
    '使用的模型': 'Model in use',
    '停止中…': 'Stopping…',
    '停止识别': 'Stop',
    '先填「替换为」的内容。': 'Fill in the replacement text first.',
    '先填要查找的内容。': 'Fill in the text to find first.',
    '先填要查找的内容（或从下面点一个现有的值）':
        'Fill in the text to find (or click an existing value below)',
    '全部 %d 条': 'All %d',
    '共 %d 项 · 将改名 %d · 跳过（时间空白/失败） %d':
        '%d items · %d to rename · %d skipped (no time / failed)',
    '关键帧': 'Keyframes',
    '关闭': 'Close',
    '写入清单': 'Writing manifest',
    '准备下载 %s …': 'Preparing to download %s …',
    '准备中': 'Preparing',
    '准备中…': 'Preparing…',
    '切换失败': 'Switch failed',
    '刷新': 'Refresh',
    '原文件名': 'Original name',
    '原文件：%s': 'Original: %s',
    '原目录里的模型不会自动搬过去（需要时手动复制即可）。\n\n继续？':
        'Models in the old directory are not moved automatically (copy them manually if needed).\n\nContinue?',
    '取消': 'Cancel',
    '只处理照片，不动视频': 'Photos only, leave videos alone',
    '只看空白': 'Blanks only',
    '只看空白/失败，共 %d 条': 'Blanks/failures only, %d items',
    '可以点「模型库位置…」手动指定一个目录，或直接在列表里下载模型。':
        'Click "Models dir…" to pick a directory manually, or download a model from the list.',
    '台账在「核对并改名」里点过「应用改名」之后才会生成。':
        'The ledger is created after you click "Apply renames" in the review window.',
    '启动模型服务': 'Start model service',
    '命中 %d 行。还要填「替换为」的内容。':
        '%d rows matched. Now fill in the replacement text.',
    '命中 %d 行：「%s」→「%s」': '%d rows matched: "%s" → "%s"',
    '回滚完成：%d 个文件已还原': 'Rollback done: %d files restored',
    '回滚本目录改名': 'Roll back renames',
    '固定抽帧数，0=按时长自适应':
        'Fixed frame count; 0 = adapt to duration',
    '地名': 'Location',
    '地名留空': 'Location blank',
    '地名：%s　描述：%s': 'Location: %s  Desc: %s',
    '处理结果': 'Results',
    '复制全部': 'Copy all',
    '复制清单': 'Copy list',
    '失败': 'Failed',
    '失败的文件保持原名，可在表格里改掉后重试':
        'Failed files keep their names; fix them in the table and retry',
    '失败（继续）：%s': 'Failed (continuing): %s',
    '如需国内镜像，填写镜像域名，例如 mirror.example.com\n':
        'For a domestic mirror, enter its domain, e.g. mirror.example.com\n',
    '完成': 'Done',
    '定位抽帧': 'Seek frames',
    '实验': 'Lab',
    '将按表格里的内容改名 %d 个文件（其余 %d 个时间空白或无需改动的保持原名）。':
        '%d files will be renamed per the table (the other %d keep their names: blank time or no change needed).',
    '就绪': 'Ready',
    '已下载 %d 个模型，共 %s　·　模型库 %s　·　下载源 %s':
        '%d models downloaded, %s total · models dir %s · source %s',
    '已停止 · 完成 %d/%d · 用时 %s': 'Stopped · %d/%d done · %s',
    '已取消下载': 'Download canceled',
    '已处理 %d/%d · 平均 %.1fs/条 · 已用 %s · 预计剩余 %s':
        '%d/%d processed · avg %.1fs/item · elapsed %s · eta %s',
    '已存在，复用': 'Exists, reused',
    '已按核对后的表格改名：成功 %d 个，跳过 %d 个（时间空白），失败 %d 个':
        'Renamed per the reviewed table: %d ok, %d skipped (no time), %d failed',
    '已改名': 'Renamed',
    '已改名 %d 个': '%d renamed',
    '已替换 %d 行。': '%d rows replaced.',
    '已有下载任务在进行中。': 'A download is already in progress.',
    '已请求停止，正在收尾…': 'Stop requested, wrapping up…',
    '已还原 %d 个文件': '%d files restored',
    '已选用模型：%s': 'Selected model: %s',
    '应用改名': 'Apply renames',
    '开始下载模型 %s …': 'Downloading model %s …',
    '当前已经在使用这个模型库了。':
        'This models directory is already in use.',
    '待处理': 'Pending',
    '待改名': 'To rename',
    '待补时间': 'Time needed',
    '想接着处理就点「核对并改名…」（只含已识别的部分），':
        'To continue, click "Review & rename…" (only analyzed items are included),',
    '或改完参数再「▶ 重新开始识别」':
        'or adjust the settings and click "▶ Restart analysis"',
    '手工': 'Manual',
    '打开改名台账': 'Open rename ledger',
    '打开模型库': 'Open models dir',
    '打开识别报告': 'Open analysis report',
    '打开该目录': 'Open directory',
    '找不到 ffmpeg，请确认软件目录里的 ffmpeg.exe 还在。':
        'ffmpeg not found. Make sure ffmpeg.exe is still in the program folder.',
    '把 %d 行的「%s」改成「%s」？\n\n':
        'In %d rows, replace "%s" with "%s"?\n\n',
    '把模型库切换到这里？（会重启推理服务；原目录里的文件不会被动）':
        'Switch the models directory here? (restarts the inference service; files in the old directory are untouched)',
    '把模型库切换到：\n%s\n\n切换后会重启自带推理服务。':
        'Switch models directory to:\n%s\n\nThe bundled inference service will restart.',
    '把表里所有「完全相同」的这一项换成新值（不分相邻，全表生效）':
        'Replace every exactly-matching cell in the table with the new value (whole table, not just neighbors)',
    '抽帧失败': 'Frame extraction failed',
    '抽帧数': 'Frames',
    '拍摄时间': 'Time taken',
    '按上次识别出的计划表直接改名（表格里改过的结果也走这个）':
        'Rename directly from the last plan (edits made in the table are honored)',
    '按台账回滚': 'Roll back by ledger',
    '按台账把 %s 里已改名的文件还原为原名？':
        'Restore original names in %s according to the ledger?',
    '换台机器把整个文件夹拷过去即可继续用。':
        'To move to another machine, just copy the whole folder.',
    '推理服务不可用，无法下载。\n':
        'Inference service unavailable, cannot download.\n',
    '推理服务启动失败': 'Failed to start inference service',
    '推理服务已在线': 'Inference service online',
    '推理服务已就绪': 'Inference service ready',
    '推理运行时 = %s（%s）': 'Runtime = %s (%s)',
    '推理运行时已启动（%s，端口 %d）': 'Runtime started (%s, port %d)',
    '推理运行时：%s（%s）': 'Runtime: %s (%s)',
    '推荐': 'Recommended',
    '描述': 'Description',
    '改名中': 'Renaming',
    '改名后想还原：主界面点「回滚本目录改名」。':
        'To undo later: click "Roll back renames" on the main window.',
    '改名失败': 'Rename failed',
    '改名失败 %d 个（保持原名，改掉后可再试）':
        '%d renames failed (names unchanged; fix and retry)',
    '改名失败：%r': 'Rename failed: %r',
    '改名完成': 'Rename complete',
    '改名完成：成功 %d 个，跳过 %d 个，失败 %d 个':
        'Rename complete: %d ok, %d skipped, %d failed',
    '改名结果 · %s': 'Rename results · %s',
    '改哪一列': 'Column to change',
    '改完的新文件名': 'New filename after edits',
    '文件名还没有改动。正在打开「核对并改名」窗口…':
        'Filenames are unchanged. Opening the review window…',
    '文件自带 GPS': 'File has GPS',
    '新文件名': 'New filename',
    '新文件：%s': 'New: %s',
    '无法打开目录：%r': 'Cannot open directory: %r',
    '无需改名': 'No change needed',
    '时间、地名、描述直接改；时间留空不改名；':
        'Edit time, location and description directly; a blank time means no rename;',
    '时间空白': 'No time',
    '暂不改名，关闭': "Don't rename, close",
    '替换为': 'Replace with',
    '替换全部': 'Replace all',
    '最多处理几个，0=全部': 'Max items to process, 0=all',
    '有 %d 行的时间写得不认识，请改成 20251011_1430 这种写法。\n\n%s\n  「%s」':
        '%d rows have times that cannot be parsed. Use the 20251011_1430 format.\n\n%s\n  "%s"',
    '未找到': 'Not found',
    '未找到推理运行时：软件的 runtime\\ollama 目录缺失。':
        'Inference runtime not found: the runtime\\ollama folder is missing.',
    '未找到自带运行时，请确认软件目录里的 runtime 文件夹完整':
        'Bundled runtime not found. Make sure the runtime folder in the program directory is complete.',
    '未找到运行时': 'Runtime not found',
    '未改动': 'Unchanged',
    '未改动 %d 个（时间空白、或新名和原名一样）':
        '%d unchanged (blank time, or new name equals the old one)',
    '本机已安装的其它模型。':
        'Other models installed on this machine.',
    '本机已有': 'On this machine',
    '本机还没下载任何模型　·　模型库 %s　·　下载源 %s':
        'No models downloaded yet · models dir %s · source %s',
    '查找': 'Find',
    '查找替换': 'Find & replace',
    '查找替换 · %s': 'Find & replace · %s',
    '查找替换…': 'Find & replace…',
    '校验完整性': 'Verifying integrity',
    '核对并改名': 'Review & rename',
    '核对并改名 · %s': 'Review & rename · %s',
    '核对并改名…': 'Review & rename…',
    '格子后面 ↓ 把值填到下面连续空白（同一场景填一次就够）':
        'The ↓ after a cell fills its value down through consecutive blanks (one fill per scene is enough)',
    '检测到本机已安装的 Ollama 模型库：\n%s\n（约 %d 个模型，可直接复用）\n\n':
        'Found an Ollama models directory on this machine:\n%s\n(~%d models, can be reused directly)\n\n',
    '模型 %s 下载失败：%s': 'Model %s download failed: %s',
    '模型 %s 下载完成': 'Model %s downloaded',
    '模型 %s 下载已取消': 'Model %s download canceled',
    '模型下载源': 'Model download source',
    '模型下载源 → %s': 'Model source → %s',
    '模型不存在，请检查名称拼写（%s）':
        'Model does not exist, check the spelling (%s)',
    '模型已加载进显存': 'Model loaded into VRAM',
    '模型库 = %s': 'Models dir = %s',
    '模型库 → %s': 'Models dir → %s',
    '模型库位置…': 'Models dir…',
    '模型库已切换为 %s，正在重启推理服务…':
        'Models dir switched to %s, restarting the inference service…',
    '模型库已切换到 %s': 'Models dir switched to %s',
    '模型库：%s': 'Models dir: %s',
    '模型文件不随软件打包，按需在这里单独下载；下载后保存在本软件自带的模型库，':
        'Model files are not bundled with the program. Download them here on demand; they are stored in the program\'s own models directory,',
    '模型服务不可用': 'Model service unavailable',
    '模型服务启动失败': 'Model service failed to start',
    '模型服务已就绪，正在预热 %s…':
        'Model service ready, warming up %s…',
    '模型热身': 'Warm up model',
    '模型看到的画面': 'Model view',
    '模型管理': 'Model manager',
    '模型管理 · %s': 'Model manager · %s',
    '模型管理…': 'Model manager…',
    '模型预热失败（不影响使用，识别时会自动重试）':
        'Model warm-up failed (harmless; it retries automatically during analysis)',
    '正在下载，确定关闭并停止下载吗？':
        'A download is in progress. Close and stop it?',
    '正在停止…': 'Stopping…',
    '正在切换模型库并重启服务…':
        'Switching models dir and restarting the service…',
    '正在取消…': 'Canceling…',
    '正在启动 Ollama 模型服务…':
        'Starting the Ollama model service…',
    '正在启动模型服务…': 'Starting the model service…',
    '正在读取…': 'Loading…',
    '每页': 'Per page',
    '没在本机找到已安装的 Ollama 模型库。\n\n':
        'No installed Ollama models directory found on this machine.\n\n',
    '没有任何一行可以改名（时间都是空的）。':
        'No row can be renamed (all times are blank).',
    '没有可填的空白，上下相邻格的值也不一样。\n':
        'No blanks to fill, and the neighboring cells above/below differ.\n',
    '没有可改名的项目': 'Nothing to rename',
    '没有找到「%s」。': '"%s" not found.',
    '没有找到计划表，先跑一次不带 --apply-plan 的识别。':
        'No plan found. Run analysis first without --apply-plan.',
    '浏览…': 'Browse…',
    '清理旧层': 'Cleaning old layers',
    '清空': 'Clear',
    '照片': 'Photos',
    '照片 %d 张': '%d photos',
    '照片+模型': 'Photo + model',
    '照片/视频 AI 改名引擎（无界面）':
        'Photo/video AI renamer engine (no GUI)',
    '照片也写内容描述': 'Describe photos too',
    '照片也调模型写描述（每张约 11 秒，很慢）':
        'Also run the model to describe photos (~11s each, slow)',
    '照片：写内容描述，约 11 秒/张':
        'Photos: described by the model, ~11s each',
    '照片：只读拍摄时间和 GPS，秒级':
        'Photos: only EXIF time & GPS, seconds',
    '状态': 'Status',
    '状态：%s': 'Status: %s',
    '现在打开「模型管理」下载它吗？\n（模型文件约 3–8 GB，只下载一次）':
        'Open the model manager to download it now?\n(the model file is ~3-8 GB, downloaded once)',
    '用本机已有模型库': 'Use existing local models',
    '留空 = Ollama 官方源（registry.ollama.ai）。\n':
        'Leave blank = Ollama official source (registry.ollama.ai).\n',
    '目录': 'Directory',
    '目录不存在': 'Directory does not exist',
    '目录不存在：\n%s': 'Directory does not exist:\n%s',
    '目录不存在：%s': 'Directory does not exist: %s',
    '目标文件名已存在': 'Target filename already exists',
    '相邻%.1fh·%s': 'neighbor %.1fh · %s',
    '相邻文件（可标到镇）': 'Neighbor file (town level)',
    '确认后点「应用改名」。': 'Click "Apply renames" when done.',
    '第 %d / %d 页　·　显示第 %d–%d 条　·　%s':
        'Page %d / %d · showing %d–%d · %s',
    '系统安装的 Ollama': 'System Ollama',
    '约 3.3 GB': '~3.3 GB',
    '约 5.5 GB': '~5.5 GB',
    '约 6.0 GB': '~6.0 GB',
    '约 7.9 GB': '~7.9 GB',
    '耗时': 'Time',
    '自动': 'Auto',
    '自检：手工时间解析 %r -> %s': 'Selftest: manual time parse %r -> %s',
    '自检：控件构建正常': 'Selftest: widgets built OK',
    '自检：改名结果窗口构建正常':
        'Selftest: rename-result window built OK',
    '自检：查找替换窗口构建正常':
        'Selftest: find-replace window built OK',
    '自检：核对并改名窗口构建正常（%d 行）':
        'Selftest: review window built OK (%d rows)',
    '自检：模型管理对话框构建正常（列出 %d 个候选模型）':
        'Selftest: model-manager dialog built OK (%d candidates listed)',
    '自检：运行状态机（未开始/识别中/停止中/已停止/已完成）文案切换正常':
        'Selftest: run-state machine text switching OK (idle/analyzing/stopping/stopped/done)',
    '英文场景表现更好，中文描述偏弱，显存需求大。':
        'Better on English scenes, weaker Chinese descriptions, needs lots of VRAM.',
    '获取清单': 'Fetching manifest',
    '表里现有的值（点一下填入查找框）：':
        'Existing values in the table (click to fill the find box):',
    '要处理的目录': 'Directory to process',
    '视觉主力。4B 量化后可全部进显存，实测生成约 55–62 tok/s，本机首选。':
        'Main workhorse. The 4B quantized build fits entirely in VRAM; measured ~55-62 tok/s; first choice on this machine.',
    '视觉模型「%s」还没有下载到本机。\n\n':
        'Vision model "%s" is not downloaded yet.\n\n',
    '视觉识别模型（模型包）': 'Vision models (downloads)',
    '视频': 'Videos',
    '视频 %d 个': '%d videos',
    '视频或照片': 'Videos or photos',
    '视频改名助手': 'Video renamer',
    '视频：抽帧送模型，约 11 秒/条':
        'Videos: frames sent to the model, ~11s each',
    '计划表：%s': 'Plan: %s',
    '识别中 %d/%d': 'Analyzing %d/%d',
    '识别中…': 'Analyzing…',
    '识别失败': 'Analysis failed',
    '识别完成 · 共 %d 条 · 用时 %s':
        'Analysis complete · %d items · %s',
    '识别完直接改名（默认只出计划表）':
        'Rename right after analysis (default: plan only)',
    '识别质量更高，但需 8GB 以上显存；6GB 显存会溢出到内存、明显变慢。':
        'Higher quality, but needs 8GB+ VRAM; 6GB spills to RAM and slows down noticeably.',
    '该目录下没找到%s': 'No %s found in this directory',
    '请先勾选要处理的内容：视频、照片，或两个都勾。':
        'Check what to process first: videos, photos, or both.',
    '请确认软件目录里的 runtime\\ollama 是否完整。':
        'Make sure runtime\\ollama in the program folder is complete.',
    '软件自带运行时': 'Bundled runtime',
    '运行日志': 'Log',
    '还没有识别结果。先点「开始识别」，跑完会自动打开这个窗口。':
        'No analysis results yet. Click "Start analysis"; this window opens automatically when it finishes.',
    '这一格是空的，先填上内容再向下填。':
        'This cell is empty. Fill it in before filling down.',
    '这个目录还没有改名记录。\n':
        'This directory has no rename records yet.\n',
    '连已命名的文件也重做': 'Redo already-renamed files too',
    '连目录里的照片一起处理': 'Include photos in the directory',
    '选择模型库目录（模型包会下载到这里）':
        'Choose the models directory (model files download here)',
    '通用视觉模型，中文场景描述尚可，速度中等。':
        'General vision model, decent Chinese scene descriptions, medium speed.',
    '邻近%.0fh·%s': 'nearby %.0fh · %s',
    '邻近文件（只能标到城市）': 'Nearby file (city level only)',
    '重新处理已改过名的': 'Reprocess already-renamed',
    '预设目录：%s': 'Presets: %s',
    '（%.1fG/%.1fG 部分在内存）': '(%.1fG/%.1fG partly in RAM)',
    '（之后下载的模型会从该源拉取）':
        '(future downloads use this source)',
    '（全部在显存）': '(all in VRAM)',
    '（只改表格内容，文件名要等点「应用改名」才动）':
        '(edits the table only; filenames change when you click "Apply renames")',
    '（失败）': '(failed)',
    '（想改不相邻的同名格，用左下角「查找替换…」）':
        '(for non-adjacent matches, use "Find & replace…" at the bottom left)',
    '（无）': '(none)',
    '（时间看不懂）': '(unparsed time)',
    '（时间空白）': '(no time)',
    '（时间空白，不改名）': '(no time, not renamed)',
    '（时间空白，先不改名）': '(no time, left alone)',
    '（未改名）': '(not renamed)',
    '（没有可预览的画面）': '(no preview available)',
    '（照片识别失败：%r）': '(photo analysis failed: %r)',
    '（空）': '(blank)',
    '；已开启「照片也写描述」，每张约 11 秒':
        '; photo descriptions enabled, ~11s each',
})

# ---------------------------------------------------------------- 组合 key
# 以下是「隐式字符串拼接」被整体包裹后产生的组合 key（T('a' + 'b')），
# 英文按各部分拼接并做了边界润色（补空格、句号改分号等）。
EN.update({
    '中文': '中文',
    '=== 文件名尚未改动。可在结果表里核对/修改「时间、地点、描述」，确认后点「应用改名」。':
        '=== Filenames are unchanged yet. Review/edit time, location and description in the table, then click "Apply renames" when done.',
    '① 把这一格的%s填到下面连续的空白格（遇到有值就停，跨页有效）\n② 改过这一格后点它：把上下连续的同名格一起换成新值\n   （点位库用一个行政中心代表整片区域，边界上常常一整段都判错）':
        "① Fill this cell's %s down through consecutive blanks (stops at a filled cell, works across pages)\n② After editing a cell, click it: replaces the contiguous same-value cells above and below with the new value\n   (the location DB uses one admin centre as the point for a whole county, so long stretches near borders are often mislabeled)",
    '将按表格里的内容改名 %d 个文件（其余 %d 个时间空白或无需改动的保持原名）。\n\n改名后可以在主界面点「回滚本目录改名」还原。\n确定继续？':
        '%d files will be renamed per the table (the other %d keep their names: blank time or no change needed).\n\nAfter renaming you can undo it with "Roll back renames" on the main window.\nContinue?',
    '想接着处理就点「核对并改名…」（只含已识别的部分），或改完参数再「▶ 重新开始识别」':
        'To continue, click "Review & rename…" (only analyzed items are included), or adjust the settings and click "▶ Restart analysis"',
    '把 %d 行的「%s」改成「%s」？\n\n（只改表格内容，文件名要等点「应用改名」才动）':
        'In %d rows, replace "%s" with "%s"?\n\n(edits the table only; filenames change when you click "Apply renames")',
    '把模型库切换到：\n%s\n\n切换后会重启自带推理服务。原目录里的模型不会自动搬过去（需要时手动复制即可）。\n\n继续？':
        'Switch models directory to:\n%s\n\nThe bundled inference service will restart. Models in the old directory are not moved automatically (copy them manually if needed).\n\nContinue?',
    '推理服务不可用，无法下载。\n请确认软件目录里的 runtime\\ollama 是否完整。':
        'Inference service unavailable, cannot download.\nMake sure runtime\\ollama in the program folder is complete.',
    '时间、地名、描述直接改；时间留空不改名；格子后面 ↓ 把值填到下面连续空白（同一场景填一次就够）':
        'Edit time, location and description directly; a blank time means no rename; the ↓ after a cell fills its value down through consecutive blanks (one fill per scene is enough)',
    '检测到本机已安装的 Ollama 模型库：\n%s\n（约 %d 个模型，可直接复用）\n\n把模型库切换到这里？（会重启推理服务；原目录里的文件不会被动）':
        'Found an Ollama models directory on this machine:\n%s\n(~%d models, can be reused directly)\n\nSwitch the models directory here? (restarts the inference service; files in the old directory are untouched)',
    '模型文件不随软件打包，按需在这里单独下载；下载后保存在本软件自带的模型库，换台机器把整个文件夹拷过去即可继续用。':
        "Model files are not bundled with the program. Download them here on demand; they are stored in the program's own models directory. To move to another machine, just copy the whole folder.",
    '没在本机找到已安装的 Ollama 模型库。\n\n可以点「模型库位置…」手动指定一个目录，或直接在列表里下载模型。':
        'No installed Ollama models directory found on this machine.\n\nClick "Models dir…" to pick a directory manually, or download a model from the list.',
    '没有可填的空白，上下相邻格的值也不一样。\n（想改不相邻的同名格，用左下角「查找替换…」）':
        'No blanks to fill, and the neighboring cells above/below differ.\n(for non-adjacent matches, use "Find & replace…" at the bottom left)',
    '留空 = Ollama 官方源（registry.ollama.ai）。\n如需国内镜像，填写镜像域名，例如 mirror.example.com\n（之后下载的模型会从该源拉取）':
        'Leave blank = Ollama official source (registry.ollama.ai).\nFor a domestic mirror, enter its domain, e.g. mirror.example.com\n(future downloads use this source)',
    '视觉模型「%s」还没有下载到本机。\n\n现在打开「模型管理」下载它吗？\n（模型文件约 3–8 GB，只下载一次）':
        'Vision model "%s" is not downloaded yet.\n\nOpen the model manager to download it now?\n(the model file is ~3-8 GB, downloaded once)',
    '这个目录还没有改名记录。\n台账在「核对并改名」里点过「应用改名」之后才会生成。':
        'This directory has no rename records yet.\nThe ledger is created after you click "Apply renames" in the review window.',
    '地名数据 © OpenStreetMap 贡献者（ODbL）· 全球城市 GeoNames（CC-BY 4.0）':
        'Place data (c) OpenStreetMap contributors (ODbL) - worldwide cities from GeoNames (CC-BY 4.0)',
})

EN.update({
    # ↓ v3.20 联网精确定位 / 描述落盘 / 关于
    '联网精确定位':
        'Online precise location',
    '开启后将在有网时调用在线地图接口获取更精确地址，会产生网络请求':
        'When on, it queries an online map service for a finer address whenever the network is up (this makes network requests)',
    '高德 API Key':
        'Amap API key',
    '国内坐标用它查街道级地址，去 lbs.amap.com 免费申请；不填就一直走离线库':
        'Used for street-level addresses in China. Apply for a free key at lbs.amap.com; leave it empty to keep using the offline database',
    '地址粒度':
        'Address detail',
    '到区县':
        'District',
    '到街道':
        'Street',
    '联网精确定位：开':
        'Online location: on',
    '联网精确定位：开（%s）｜ 地址粒度：%s':
        'Online location: on (%s) | detail: %s',
    '已填高德 Key':
        'Amap key set',
    '未填高德 Key，国内坐标走离线库':
        'no Amap key, China coordinates fall back to the offline DB',
    '    只发送经纬度给地图服务商，不发送文件本身；无网或失败自动回落离线库':
        '    Only latitude/longitude is sent to the map provider, never the file itself; offline or failed requests fall back to the local database',
    '联网精确定位：关（纯离线，全程不发网络请求）':
        'Online location: off (fully offline, no network requests at all)',
    '联网精确定位已开启，但还没填高德 API Key：国内坐标会继续用离线库，国外坐标走 Nominatim。':
        'Online location is on, but no Amap API key yet: China coordinates keep using the offline database, others use Nominatim.',
    '写入文件元数据':
        'Write metadata into files',
    '生成 .ai.txt 摘要':
        'Write .ai.txt summaries',
    '生成 目录说明.md':
        'Write folder summary (目录说明.md)',
    '元数据写进文件本身（照片 EXIF/XMP、视频容器标签），拷贝到任何地方都带着走；三个开关各管一件事，关掉就不生成。':
        'Metadata is written into the file itself (EXIF/XMP for photos, container tags for videos) and travels with it. The three switches are independent.',
    '并写入元数据/摘要，每条多几秒':
        'plus metadata/summaries, a few seconds more per file',
    '写入描述':
        'Writing descriptions',
    '=== 元数据写入：成功 %d 个，失败 %d 个（原文件不动）':
        '=== Metadata: %d written, %d failed (original files untouched)',
    '=== 摘要 .ai.txt：%d 个':
        '=== Summaries (.ai.txt): %d',
    '=== 目录说明.md：汇总 %d 个文件':
        '=== 目录说明.md: %d files summarized',
    '=== 三个落盘开关都关着：不写元数据、不写 .ai.txt、不写目录说明.md':
        '=== All three write-out switches are off: no metadata, no .ai.txt, no 目录说明.md',
    '  · 元数据未写入 %s：%s':
        '  - metadata not written for %s: %s',
    '  · 摘要未写入 %s：%s':
        '  - summary not written for %s: %s',
    '  · 目录说明.md 未写入：%s':
        '  - 目录说明.md not written: %s',
    '写完回读不一致':
        'read-back mismatch after writing',
    'HEIC/HEIF 需要 exiftool（未找到，已跳过）':
        'HEIC/HEIF needs exiftool (not found, skipped)',
    'exiftool 写入失败':
        'exiftool failed to write',
    'exiftool 调用失败：%r':
        'exiftool call failed: %r',
    '已写入 exiftool':
        'written with exiftool',
    '已写入 exiftool（回读失败）':
        'written with exiftool (read-back failed)',
    '不是 PNG':
        'not a PNG',
    'PNG 写入后校验失败，已放弃':
        'PNG failed verification after writing, discarded',
    'moov 标签':
        'moov tags',
    '容器标签（时长 %.2fs 不变）':
        'container tags (duration %.2fs unchanged)',
    'ffmpeg 调用失败：%r':
        'ffmpeg call failed: %r',
    'ffmpeg 写入失败':
        'ffmpeg failed to write',
    '时长校验未通过（%.2fs → %.2fs），已放弃':
        'duration check failed (%.2fs -> %.2fs), discarded',
    '标签回读不一致，已放弃':
        'tag read-back mismatch, discarded',
    'mutagen 写入失败：%r':
        'mutagen failed to write: %r',
    '这个格式暂不支持写元数据：%s':
        'metadata writing is not supported for: %s',
    '写元数据失败：%r':
        'metadata write failed: %r',
    '目录说明':
        'Folder summary',
    '生成时间':
        'Generated',
    '文件数':
        'Files',
    '内容':
        'Content',
    '（地点不明）':
        '(location unknown)',
    '（无描述）':
        '(no description)',
    '画面为%s':
        'the scene shows %s',
    '内容不明':
        'unclear content',
    '时间不明':
        'unknown date',
    '地点不明':
        'unknown location',
    '%d年%d月%d日':
        '%d-%d-%d',
    '无网络':
        'no network',
    '在线接口无结果':
        'online service returned nothing',
    '关于':
        'About',
    '隐私':
        'Privacy',
    '数据与许可':
        'Data & licence',
    '识别与改名全程在你的电脑上完成，照片和视频不会离开本机，识别结果也不上传。':
        'Everything runs on your computer: photos and videos never leave this machine, and results are never uploaded.',
    '联网精确定位开启时，仅经纬度坐标会发送给地图服务商用于解析地址，不上传文件本身。':
        'With online precise location on, only latitude/longitude is sent to the map provider to resolve an address; the file itself is never uploaded.',
    '联网精确定位当前是关闭的：软件不发任何网络请求，地址全部来自内置离线库。':
        'Online precise location is currently off: the app makes no network requests and all addresses come from the built-in offline database.',
    '联网地址：国内 高德开放平台（lbs.amap.com）· 国外 Nominatim / OpenStreetMap':
        'Online addresses: Amap (lbs.amap.com) for China, Nominatim / OpenStreetMap elsewhere',
})

EN.update({
    '地点': 'Location',
})

EN.update({
    '❓ 使用说明': '? User guide',
    '使用说明': 'User guide',
    '下次启动不再自动显示': "Don't show this again on startup",
    '帮助文件缺失：请确认软件目录下有 help_zh.md / help_en.md':
        'Help file missing: make sure help_zh.md / help_en.md sit next to the app',
    '（缺文件）': '(file missing)',
    '自检：使用说明窗口构建正常（%d 段 · %s）':
        'selftest: user guide window built (%d blocks · %s)',
})

EN.update({
    '找不到 ffmpeg：软件目录里没有，系统 PATH 里也没有。Mac 版可执行 brew install ffmpeg。':
        'ffmpeg not found (neither in the app folder nor on PATH). On macOS you can run: brew install ffmpeg',
    '未找到推理运行时：软件自带的 runtime 目录缺失，系统里也没装 Ollama。':
        'Inference runtime not found: the bundled runtime folder is missing and no system Ollama was found.',
})

# —— AI 描述总开关（v3.22）：关掉后视频不抽帧、照片不送模型，只按「时间+地名」命名
EN.update({
    'AI描述': 'AI description',
    '纯离线，不调用模型': 'Fully offline, no model calls',
    '视频：只读拍摄时间和 GPS，秒级':
        'Videos: read time & GPS only, instant',
    '自检：AI 描述开关构建正常': 'selftest: AI description switch built OK',
    '自检：AI 描述开关异常：%r': 'selftest: AI description switch error: %r',
    'AI 描述：关（纯离线，不调用模型），跳过推理服务与模型热身':
        'AI description: off (fully offline, no model calls) - skipping inference service and model warmup',
})

# Local GGUF inference and model-library management.
EN.update({
    '无法加载 PyAV，请安装 requirements-media.txt 中的依赖。':
        'Cannot load PyAV. Install the dependencies in requirements-media.txt.',
    '自检：本地模型状态与设备显示正常': 'selftest: local model and device status display OK',
    '照片也调用模型生成描述（CPU 耗时较长）': 'Also describe photos with the model (slower on CPU)',
    '；已开启「照片也写描述」，耗时取决于图片和 CPU': '; photo descriptions enabled; time depends on images and CPU',
    '请将 GGUF 主模型和 mmproj 文件放入模型目录。': 'Place the GGUF model and mmproj files in the model directory.',
    '当前使用本地模型目录。': 'Models are read from a local directory.',
    '主模型和同目录的 mmproj 视觉投影文件必须配套存在。': 'The model and matching mmproj file must be in the same directory.',
    '不支持的模型：%s': 'Unsupported model: %s',
    '存在多个同名模型，请选择具体的模型目录：%s': 'Multiple copies found; select the specific model directory: %s',
    '缺少主模型或配套的 mmproj 文件：%s': 'Model or matching mmproj file missing: %s',
    '模型目录不存在或无法读取：%s': 'Model directory missing or unreadable: %s',
    '模型正在释放，请稍后重试。': 'Model resources are being released; try again shortly.',
    '模型操作超时': 'Model operation timed out',
    '无法初始化本地推理库：%s': 'Cannot initialize the local inference library: %s',
    '模型推理已中止': 'Model inference aborted',
    '选择模型目录…': 'Select model directory…',
    '选择 GGUF 模型目录': 'Select GGUF model directory',
    '可用模型 %d 个 · 模型库 %s': '%d usable models · Library: %s',
    '已配对 · 文件大小 %s': 'Model pair found · File size: %s',
    '模型文件不完整': 'Incomplete model files',
    '模型库切换失败': 'Failed to change model directory',
    '本地视觉模型 · GGUF': 'Local vision models · GGUF',
    '选择已有模型目录。主模型和匹配的 mmproj 文件须放在同一目录；也会检查下一层子目录。':
        'Select an existing model directory. Keep the model and matching mmproj together; immediate subdirectories are also checked.',
    '当前使用 CPU。本轮仅支持本地 Qwen3-VL-4B-Instruct 模型，不提供在线下载。':
        'Uses CPU. Supports local Qwen3-VL-4B-Instruct models; online downloads are not provided.',
    '缺少文件': 'Missing files',
    '点这里加载所选模型': 'Click to load the selected model',
    '本地推理，不需要端口或服务进程': 'In-process inference; no port or server process required',
    '模型已加载 · %s': 'Model loaded · %s',
    '● 模型操作中…': '● Model busy…',
    '=== 推理后端：%s · %s': '=== Inference backend: %s · %s',
    '模型「%s」缺少配套文件。\n\n现在打开「模型管理」选择本地 GGUF 目录吗？':
        'Files for model "%s" are incomplete.\n\nOpen Model manager to select a local GGUF directory?',
    '本地推理库已就绪 · CPU': 'Local inference library ready · CPU',
    '本地推理库不可用': 'Local inference library unavailable',
    '● 模型操作失败': '● Model operation failed',
    '请选择配套的 GGUF 主模型与 mmproj 文件': 'Select a matching GGUF model and mmproj pair',
    '● 模型未初始化': '● Model not initialized',
    '识别时自动加载，也可点击提前加载': 'Loads automatically for recognition; click to load now',
    '● 模型文件不完整': '● Incomplete model files',
    '● 模型已加载': '● Model loaded',
    '● 模型待加载': '● Model not loaded',
    '加载模型': 'Loading model',
    '=== 模型加载完成': '=== Model loaded',
    '=== 当前任务不需要模型，跳过模型加载': '=== This task does not need a model; skipping model loading',
    '!! 本地推理库初始化失败，任务终止': '!! Local inference library initialization failed; task stopped',
    '!! 模型加载失败：%s': '!! Model loading failed: %s',
})

EN.update({
    '支持下载 4B / 2B Q8 模型；已有 Q4 / F16 模型仍可手动导入。当前使用 CPU。': 'Download 4B / 2B Q8 pairs, or import existing Q4 / F16 models. Uses CPU.',
    '国外：Hugging Face': 'International: Hugging Face',
    '国内：ModelScope': 'China: ModelScope',
    '取消下载': 'Cancel download',
    '正在下载…': 'Downloading…',
    '下载': 'Download',
    '下载模型': 'Download model',
    '下载 %s（%s）到：\n%s\n\n是否开始下载？': 'Download %s (%s) to:\n%s\n\nStart download?',
    '下载已取消，保留进度，可重新点击下载继续。': 'Download cancelled. Progress is retained; click Download to resume.',
    '模型下载完成，可点击“使用”：%s': 'Download complete. Click Use to select: %s',
    '下载任务结束，详情见主窗口运行日志。': 'Download task ended. See the main window log for details.',
    '正在取消下载，请稍候…': 'Cancelling download, please wait…',
    '未找到本地模型，请在模型管理中下载 4B / 2B 模型，或选择已有模型目录。': 'No local models found. Download a 4B / 2B model in Model manager, or select an existing directory.',
    '模型「%s」缺少配套文件。\n\n现在打开「模型管理」下载模型或选择本地目录吗？': 'Files for model "%s" are incomplete.\n\nOpen Model manager to download or select a local directory?',
    '该模型仅支持手动导入。': 'This model supports manual import only.',
})

EN.update({
    '2B 和 4B 均可下载 Q4 / Q8 主模型，共用各自的 Q8 视觉投影。当前使用 CPU。':
        'Both 2B and 4B offer Q4 / Q8 models, sharing their respective Q8 vision projectors. Uses CPU.',
})

EN.update({
    'GPU 加速…': 'GPU acceleration…',
    'GPU 加速 · Vulkan': 'GPU acceleration · Vulkan',
    '可选 Vulkan 加速（实验功能）': 'Optional Vulkan acceleration (experimental)',
    '支持兼容的 NVIDIA / AMD / Intel 显卡。需要系统显卡驱动；初始化失败时使用 CPU。': 'Supports compatible NVIDIA / AMD / Intel GPUs. Requires graphics drivers; initialization failures fall back to CPU.',
    '当前设备：%s\n组件目录：%s': 'Current device: %s\nComponent directory: %s',
    '组件下载大小：%s': 'Component download size: %s',
    '下载并启用 Vulkan': 'Download and enable Vulkan',
    '启用 Vulkan': 'Enable Vulkan',
    '使用 CPU': 'Use CPU',
    '已切换到 CPU': 'Switched to CPU',
    '下载 Vulkan 加速组件（%s）并启用？': 'Download and enable the Vulkan component (%s)?',
    'Vulkan 组件已启用，识别时加载模型。': 'Vulkan component enabled. The model loads when recognition starts.',
})

EN.update({
    '2B 和 4B 均可下载 Q4 / Q8 主模型，共用各自的 Q8 视觉投影。可在“GPU 加速”中配置设备。':
        'Both 2B and 4B offer Q4 / Q8 models with matching Q8 projectors. Configure devices under GPU acceleration.',
})
