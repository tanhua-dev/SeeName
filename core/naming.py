"""Description prompts, response parsing and shared filename rules."""
from __future__ import annotations

import os
import re
from strings import T
from .defaults import (
    PHOTO_EXT,
)
from .places import (
    _short_place,
)


PROMPT = (
    T('下面这张图是一段手机视频按时间顺序抽出的若干画面拼成的网格' + '（从左到右、从上到下为时间先后）。\n' + '请用极短的词组说明画面主要内容，主体优先。\n' + '只输出一行，格式严格为：\n' + '描述=<4到8个汉字>\n' + '要求：\n' + '1) 画面里有人时，先写主体是谁（用 婆婆/爷爷/奶奶/宝宝/男孩/女孩/女子/男子/' + '一家人/两人 这类词），再写他在做什么，' + '例如 婆婆剥洋葱 / 宝宝吹蜡烛 / 一家人吃饭 / 男子江边钓鱼；\n' + '2) 画面里没有人物时，直接写核心事物或动作，' + '例如 拆包装 / 江边夜景 / 年夜饭；\n' + '3) 不要地点、不要场所词（室内/室外/厨房/客厅），' + '不要景色、光线、天气、心情、形容词，不要标点符号；\n' + '4) 不要解释，不要输出任何其它文字，也不要描述网格本身。')
)
PHOTO_PROMPT = (
    T('请看这张照片，给文件起一个内容描述，主体优先。\n' + '只输出一行，格式严格为：\n' + '描述=<4到8个汉字>\n' + '要求：\n' + '1) 画面里有人时，先写主体是谁（用 婆婆/爷爷/奶奶/宝宝/男孩/女孩/女子/男子/' + '一家人/两人 这类词），再写外观特征或正在做的事，' + '例如 婆婆戴帽微笑 / 宝宝吹蜡烛 / 一家人合影 / 爷爷打太极；\n' + '2) 画面里没有人物时，直接写事物本身，例如 年夜饭 / 生日蛋糕 / 江边夜景；\n' + '3) 不要地点、不要场所词，不要景色、光线、心情、形容词，不要标点符号；\n' + '4) 不要解释，不要输出任何其它文字。')
)
FULL_TAIL = T(
    '请严格按下面三行输出，不要多余空行、不要解释：\n'
    + '描述=<4到8个汉字的极短词组>\n'
    + '长描述=<100到200个汉字>\n'
    + '关键词=<3到5个名词短语，逗号分隔>\n'
    + '各行要求：\n'
    + '1) 描述：画面里有人时先写主体是谁（婆婆/爷爷/奶奶/宝宝/男孩/女孩/女子/男子/'
    '一家人/两人），再写他在做什么；没有人就写核心事物或动作。'
    '不要地点、不要场所词、不要景色光线心情形容词、不要标点符号；\n'
    + '2) 长描述：写成 2 到 4 句连贯的话，必须同时包含——'
    '时间（用上面给的拍摄时间）、地点（用上面给的地点，能细到街道或具体场所更好）、'
    '画面主体是谁、正在做什么、场景氛围；不要分点、不要小标题、'
    '不要再写「描述=」「长描述=」这类标记；没把握的具体路名门牌不要编；\n'
    + '3) 关键词：3 到 5 个名词短语，逗号分隔，例如 蓬安中学,校门,人物留影。')
PROMPT_FULL = (
    T('下面这张图是一段手机视频按时间顺序抽出的若干画面拼成的网格'
      '（从左到右、从上到下为时间先后）。\n'
      '已知：拍摄时间 {t}；地点 {p}。\n') + FULL_TAIL)
PHOTO_PROMPT_FULL = (
    T('请看这张照片。\n'
      '已知：拍摄时间 {t}；地点 {p}。\n') + FULL_TAIL)
LONG_MAX = 300      # 长描述硬上限（提示词要 100~200 字，留一点余量）
LONG_MIN = 40       # 短于这个数就当模型敷衍了，由引擎用已知信息补齐
KW_MAX = 5


def time_cn(dt) -> str:
    """2026-10-12 → 2026年10月12日（喂给模型/写进长描述用）。"""
    try:
        return T('%d年%d月%d日') % (dt.year, dt.month, dt.day)
    except Exception:
        return ''


def parse_full(text: str) -> tuple[str, str, str]:
    """解析三行输出 → (短描述, 长描述, 关键词)。拿不到就交给调用方兜底。"""
    short = parse_out(text)
    long, kw = '', ''
    m = re.search(r'长描述\s*[=:：]\s*(.+)', text)
    if m:
        long = m.group(1).strip()
    m = re.search(r'关键词\s*[=:：]\s*(.+)', text)
    if m:
        kw = m.group(1).strip()
    long = re.sub(r'\s+', ' ', long).strip().strip('"“”\'')
    kw = re.sub(r'[\[\]（）()「」【】]', '', kw).strip()
    kw = re.sub(r'\s*[,，]\s*', ',', kw)
    if len(kw) > 80:
        kw = kw[:80]
    return short, long[:LONG_MAX], kw


def fill_long(long: str, desc: str, when: str, where: str) -> str:
    """模型敷衍（太短或没给）时用已知信息拼一段能用的长描述。"""
    if len(long or '') >= LONG_MIN:
        return long
    bits = []
    if when:
        bits.append(when)
    if where:
        bits.append(where)
    bits.append(T('画面为%s') % (desc or T('内容不明')))
    s = '，'.join([b for b in bits if b]) + '。'
    if long:
        s = long + '　' + s
    return s[:LONG_MAX]
DESC_MAX = 9            # 描述字数「安全上限」。提示词要求模型给 3-6 字，但它常常给到
DESC_BAD = {T('无动作'), T('无活动'), T('无明显动作'), T('无明显活动'), T('未知'), '无', T('无法判断'),
            T('静止'), T('无内容'), T('看不到'), T('不清晰'), T('无变化')}


def parse_out(text: str) -> str:
    """从模型输出里取出描述，并硬截断到 DESC_MAX 个汉字。"""
    s = ''
    m = re.search(r'描述\s*[=:：]\s*(.+)', text)
    if m:
        s = m.group(1)
    else:
        for line in text.splitlines():
            line = re.sub(r'^\s*[-*\d.、)\s]+', '', line).strip()
            if line and '=' not in line and '：' not in line:
                s = line
                break
    s = s.strip().splitlines()[0] if s.strip() else ''
    s = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]+', '', s)
    s = s[:DESC_MAX]
    return '' if s in DESC_BAD else s
NAME_MAX = 80       # 文件名（不含扩展名）的长度上限


def compose_name(ts: str, place: str, desc: str, old_name: str = '',
                 kind: str = '', place_exact: bool = False) -> str:
    """统一的文件名规则：`视20241220成都肖家河_剥洋葱[_log]`。

    这是全软件唯一的命名规则，界面里改完时间/地点/描述后也走这里预览新名字。
      * 前缀：视频 `视`、照片 `图`（不传 kind 时按扩展名判断）；
      * 时间只写到「天」（ts 里的时:分不写进名字），同一天靠描述区分，
        描述也撞车时由改名环节自动补回时刻 `_时分`（再撞才 `_2`/`_3`）；
      * 地名与描述之间用 `_`；
      * 哪一段没有就省掉哪一段；原文件名里的 `_log` 之类的多余尾巴一律不带进来。
    """
    stem = os.path.splitext(old_name or '')[0]
    date = re.sub(r'\D', '', str(ts or ''))[:8]
    # 在线精确定位给的地名（如「成都郫都区郫筒街道」）已经是最终形态，
    # 不再过 _short_place 削后缀——削掉「街道」反而看不出是哪一级
    place = place if (place and place_exact) else (_short_place(place) if place else '')
    if not kind:
        ext = os.path.splitext(old_name or '')[1].lower()
        kind = 'photo' if ext in PHOTO_EXT else 'video'
    head = date + (place or '')
    pre = '图' if kind == 'photo' else '视'

    def build(d: str) -> str:
        body = '_'.join([p for p in (head, d) if p])
        return re.sub(r'[\\/:*?"<>|]', '', pre + body) if body else ''

    raw = build(desc)
    if len(raw) <= NAME_MAX:
        return raw
    # 超长时先砍描述、地名尽量保住（地址比「几个字的描述」更值钱）：
    # 描述砍到 4 字还剩不下，才允许动地名的尾巴。
    over = len(raw) - NAME_MAX
    if desc:
        keep = len(desc) - over
        if keep >= 4:
            return build(desc[:keep])[:NAME_MAX]
        short = build(desc[:4])
        if len(short) <= NAME_MAX:
            return short
        return short[:NAME_MAX]
    return raw[:NAME_MAX]


def name_from_item(it: dict) -> str:
    """按计划项（含用户改过的时间/地点/描述）算出完整新文件名。"""
    old = it.get('old') or ''
    stem = compose_name(str(it.get('time') or '').strip(),
                        str(it.get('place') or '').strip(),
                        str(it.get('desc') or '').strip(),
                        old, kind=it.get('kind') or '',
                        place_exact=bool(it.get('place_exact')))
    return (stem + os.path.splitext(old)[1]) if stem else ''
