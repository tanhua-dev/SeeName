"""Offline place lookup and optional online refinement with offline fallback."""
from __future__ import annotations

import geo
import io
import math
import os
import re
from strings import T
from .paths import (
    DATA_DIR,
    _bundle_roots,
)


_DISTRICTS = None
_WORLD = None
_WINDEX = None
_CN_MASK = None
_DIST_CACHE = {}
CN_LAT = (18.0, 54.0)
CN_LNG = (73.0, 135.0)
DIST_NEAR_KM = 40.0     # 县级点位的平均间距比乡镇大得多，所以阈值放宽：
# 40km 内标市+县，40~200km 只标市，更远留空。
DIST_FAR_KM = 200.0
WORLD_TIE_KM = 15.0     # 最近点周围这么宽的范围里，一律改用人口更多的那个城市名
WORLD_MAX_KM = 120.0    # 国外：超出最近城市这么远就不标（多为人迹罕至处）
WORLD_WITH_COUNTRY = True   # 国外地名是否带中文国名前缀，如「日本 Tokyo」


def _data_paths(name: str) -> list:
    """内置数据的候选位置：打包进 exe 的临时目录 → 软件 data 目录。"""
    ps = [os.path.join(r, 'data', name) for r in _bundle_roots()]
    ps.append(os.path.join(DATA_DIR, name))
    return ps


def _load_csv(name: str) -> list:
    """读 data 目录下的 csv，返回切分好的行列表（跳过表头）。"""
    for p in _data_paths(name):
        try:
            with io.open(p, encoding='utf-8') as f:
                head = f.readline()
                if ',' not in head:
                    continue
                return [ln.rstrip('\n').split(',')
                        for ln in f if ln.count(',') >= 3]
        except Exception:
            continue
    return []


def load_districts() -> list:
    """懒加载中国县级点位库 → [[县名, 经度, 纬度, 地级市名], ...]"""
    global _DISTRICTS
    if _DISTRICTS is None:
        _DISTRICTS = []
        for r in _load_csv('districts.csv'):
            try:
                _DISTRICTS.append([r[1], float(r[4]), float(r[3]), r[2]])
            except Exception:
                continue
    return _DISTRICTS


def load_world() -> list:
    """懒加载全球城市兜底库 → [[城市名, 经度, 纬度, 国家码, 人口, 中文名], ...]

    中文名可能为空（GeoNames 的别名里没收录时），此时回退到拉丁名。
    """
    global _WORLD
    if _WORLD is None:
        _WORLD = []
        for r in _load_csv('cities_world.csv'):
            try:
                pop = int(r[4]) if len(r) > 4 else 0
            except Exception:
                pop = 0
            try:
                zh = (r[5] or '').strip() if len(r) > 5 else ''
                _WORLD.append([r[0], float(r[2]), float(r[1]), r[3], pop, zh])
            except Exception:
                continue
    return _WORLD
_WCELL = 2.0            # 全球库的空间网格边长（度），用来把"逐条扫 10 万行"缩到几百行


def _world_index() -> dict:
    """按 2° 网格给全球城市建索引 {(igrid, jgrid): [行...]}"""
    global _WINDEX
    if _WINDEX is None:
        _WINDEX = {}
        for r in load_world():
            _WINDEX.setdefault((int(r[2] // _WCELL), int(r[1] // _WCELL)),
                               []).append(r)
    return _WINDEX
CN_CORE_KM = 60.0       # 离最近县城这么近，毫无疑问在中国，直接走县级库


def _cn_mask() -> set:
    """用县级点位生成一张 0.25° 的"中国陆域"遮罩，用来把国境线附近判准。

    只把行政中心周围 ~0.6° 的格子算进来：常住人口都在这些格子附近，
    而海参崴（距最近县城 105km）这类境外点就不会被误判成中国。
    """
    global _CN_MASK
    if _CN_MASK is None:
        step, rad = 0.25, 0.6
        cells = set()
        for _nm, lng, lat, _city in load_districts():
            dlo = rad / max(0.25, math.cos(math.radians(lat)))
            la = lat - rad
            while la <= lat + rad:
                lo = lng - dlo
                while lo <= lng + dlo:
                    cells.add((int(la / step), int(lo / step)))
                    lo += step
                la += step
        _CN_MASK = cells
    return _CN_MASK


def in_china(lat: float, lon: float) -> bool:
    """两步判定是否在"中国实际范围内"（决定走县级精确库还是全球城市库）。"""
    if not (CN_LAT[0] <= lat <= CN_LAT[1] and CN_LNG[0] <= lon <= CN_LNG[1]):
        return False
    _c, _t, km = _nearest_county(lat, lon)
    if km <= CN_CORE_KM:
        return True
    return (int(lat / 0.25), int(lon / 0.25)) in _cn_mask()


def _nearest(rows: list, lat: float, lon: float) -> tuple:
    """通用最近邻，返回 (命中行, 距离km)；rows 元素形如 [名称, 经度, 纬度, 附加]"""
    if not rows:
        return None, 1e18
    coslat = math.cos(math.radians(lat))
    best, bd = None, 1e18
    for r in rows:
        dx = (lon - r[1]) * coslat
        dy = lat - r[2]
        d = dx * dx + dy * dy
        if d < bd:
            bd, best = d, r
    return best, math.sqrt(bd) * 111.0


def _nearest_county(lat: float, lon: float) -> tuple:
    """中国境内最近邻县级点位，返回 (地级市名, 县名, 距离km)。"""
    key = '%.3f,%.3f' % (round(lat, 3), round(lon, 3))
    hit = _DIST_CACHE.get(key)
    if hit is not None:
        return hit
    row, km = _nearest(load_districts(), lat, lon)
    if row is None:
        return ('', '', 0.0)
    out = (row[3], row[0], km)
    _DIST_CACHE[key] = out
    return out


def _world_candidates(lat: float, lon: float, ring: int = 1) -> list:
    """取该坐标所在 2° 网格及其相邻一圈里的城市（够近也够快）。"""
    idx = _world_index()
    gi, gj = int(lat // _WCELL), int(lon // _WCELL)
    out = []
    for i in range(gi - ring, gi + ring + 1):
        for j in range(gj - ring, gj + ring + 1):
            out.extend(idx.get((i, j), ()))
    return out


def _nearest_world(lat: float, lon: float) -> tuple:
    """中国范围外最近城市，返回 (城市名, 国家码, 距离km)。

    先在 2° 网格里找最近点，再在这个点周围放宽一点点、挑人口更多的那个：
    直接取最近的话，巴黎容易命中「Paris 04 Hotel-de-Ville」这种区级小点。
    """
    cand = _world_candidates(lat, lon)
    if not cand:                       # 海里或极地等空白网格，退全量扫描
        cand = load_world()
    row, km = _nearest(cand, lat, lon)
    if row is None:
        return ('', '', km)
    # 窗口 = 固定的 15km 或按距离放宽（底线是"城市级"精度，宁可写大城市名）
    win = max(WORLD_TIE_KM, km * 1.5 + 5.0)
    best = row
    coslat = math.cos(math.radians(lat))
    for r in cand:
        if len(r) > 4 and r[4] <= best[4]:
            continue
        d = math.hypot((lon - r[1]) * coslat, lat - r[2]) * 111.0
        if d <= win:
            best, km = r, d
    # 优先用中文名；GeoNames 没收录中文别名的城市仍显示拉丁原名
    return (best[5] or best[0], best[3], km)
_CC_ZH = {
    'AD': '安道尔', 'AE': '阿联酋', 'AF': '阿富汗', 'AL': '阿尔巴尼亚',
    'AM': '亚美尼亚', 'AR': '阿根廷', 'AT': '奥地利', 'AU': '澳大利亚',
    'AZ': '阿塞拜疆', 'BA': '波黑', 'BD': '孟加拉国', 'BE': '比利时',
    'BG': '保加利亚', 'BH': '巴林', 'BO': '玻利维亚', 'BR': '巴西',
    'BT': '不丹', 'BY': '白俄罗斯', 'CA': '加拿大', 'CH': '瑞士',
    'CL': '智利', 'CO': '哥伦比亚', 'CR': '哥斯达黎加', 'CU': '古巴',
    'CY': '塞浦路斯', 'CZ': '捷克', 'DE': '德国', 'DK': '丹麦',
    'DO': '多米尼加', 'DZ': '阿尔及利亚', 'EC': '厄瓜多尔', 'EE': '爱沙尼亚',
    'EG': '埃及', 'ES': '西班牙', 'ET': '埃塞俄比亚', 'FI': '芬兰',
    'FR': '法国', 'GB': '英国', 'GE': '格鲁吉亚', 'GR': '希腊',
    'HR': '克罗地亚', 'HU': '匈牙利', 'ID': '印度尼西亚', 'IE': '爱尔兰',
    'IL': '以色列', 'IN': '印度', 'IQ': '伊拉克', 'IR': '伊朗',
    'IS': '冰岛', 'IT': '意大利', 'JM': '牙买加', 'JO': '约旦',
    'JP': '日本', 'KE': '肯尼亚', 'KG': '吉尔吉斯斯坦', 'KH': '柬埔寨',
    'KR': '韩国', 'KW': '科威特', 'KZ': '哈萨克斯坦', 'LA': '老挝',
    'LB': '黎巴嫩', 'LK': '斯里兰卡', 'LT': '立陶宛', 'LU': '卢森堡',
    'LV': '拉脱维亚', 'MA': '摩洛哥', 'MD': '摩尔多瓦', 'ME': '黑山',
    'MK': '北马其顿', 'MM': '缅甸', 'MN': '蒙古', 'MT': '马耳他',
    'MV': '马尔代夫', 'MX': '墨西哥', 'MY': '马来西亚', 'MZ': '莫桑比克',
    'NA': '纳米比亚', 'NG': '尼日利亚', 'NL': '荷兰', 'NO': '挪威',
    'NP': '尼泊尔', 'NZ': '新西兰', 'PA': '巴拿马', 'PE': '秘鲁',
    'PH': '菲律宾', 'PK': '巴基斯坦', 'PL': '波兰', 'PT': '葡萄牙',
    'PY': '巴拉圭', 'QA': '卡塔尔', 'RO': '罗马尼亚', 'RS': '塞尔维亚',
    'RU': '俄罗斯', 'SA': '沙特', 'SE': '瑞典', 'SG': '新加坡',
    'SI': '斯洛文尼亚', 'SK': '斯洛伐克', 'SN': '塞内加尔', 'TH': '泰国',
    'TJ': '塔吉克斯坦', 'TM': '土库曼斯坦', 'TN': '突尼斯', 'TR': '土耳其',
    'TT': '特立尼达', 'TW': '', 'TZ': '坦桑尼亚', 'UA': '乌克兰',
    'US': '美国', 'UY': '乌拉圭', 'UZ': '乌兹别克斯坦', 'VE': '委内瑞拉',
    'VN': '越南', 'ZA': '南非', 'ZM': '赞比亚', 'ZW': '津巴布韦',
    'CI': '科特迪瓦', 'AO': '安哥拉', 'HN': '洪都拉斯', 'PS': '巴勒斯坦',
    # 以下为中国领土，按惯例不加国名前缀
    'CN': '', 'HK': '', 'MO': '',
}


def _world_place(nm: str, cc: str) -> str:
    """国外地名：国名 + 城市名。港澳台与大陆一样不加前缀。"""
    cc = (cc or '').upper()
    pre = _CC_ZH.get(cc, '') if WORLD_WITH_COUNTRY else ''
    # 新加坡、摩纳哥这类城市国家：国名已等于（或包含于）城市名，不再叠加前缀
    if pre and (pre == nm or nm.startswith(pre)):
        return nm
    return pre + nm
_ADMIN_SUF = re.compile(T('(街道|镇|乡|办事处)$'))


def _short_place(name: str) -> str:
    """去掉地名末尾的行政级别词（街道/镇/乡/办事处）。"""
    s = _ADMIN_SUF.sub('', name or '')
    return s or (name or '')


def district_of(lat: float, lon: float) -> tuple:
    """离线最近邻取地名，返回 (地名, 距离km)。

    两步路由，全程离线、没有任何在线反查：
      * 在中国经纬度窗口内 → 县级点位库：40km 内命中县城标「市 + 县」
        （如「成都郫都区」「成都彭州」），40~200km 退化为「成都」，更远返回空串；
      * 窗口外 → 全球城市库（GeoNames cities1000）：输出「中文国名 + 城市名」
        （如「日本 Tokyo」），超过 WORLD_MAX_KM 返回空串。
    """
    if in_china(lat, lon):
        city, county, km = _nearest_county(lat, lon)
        if not city:
            # 窗口内但县级库没覆盖到（港澳台等）→ 退回全球城市库
            nm, cc, kmw = _nearest_world(lat, lon)
            return (_world_place(nm, cc), kmw) \
                if nm and kmw <= WORLD_MAX_KM else ('', kmw)
        if km <= DIST_NEAR_KM:
            return _short_place(city + county), km
        if km <= DIST_FAR_KM:
            return city, km
        return '', km
    nm, cc, km = _nearest_world(lat, lon)
    if not nm or km > WORLD_MAX_KM:
        return '', km
    return _world_place(nm, cc), km


def city_of(lat: float, lon: float) -> tuple:
    """只要城市级地名，返回 (地名, 距离km)；超出阈值返回空串。"""
    if in_china(lat, lon):
        city, _cnty, km = _nearest_county(lat, lon)
        if not city or km > DIST_FAR_KM:
            return '', km
        return city, km
    nm, cc, km = _nearest_world(lat, lon)
    if not nm or km > WORLD_MAX_KM:
        return '', km
    return _world_place(nm, cc), km


def place_of(gps, net_geo: bool = False, key: str = '',
             level: str = 'district') -> tuple:
    """地名：离线库打底，联网精确定位（可选）覆盖成更下一级。

    返回 (place, addr, km, src, exact)：
      place  文件名里那段短地名
      addr   完整地址（写进长描述 / .ai.txt / 目录说明）
      km     离线命中县城的距离（在线不改它）
      src    'offline' / 'amap' / 'nominatim'
      exact  True = 在线地名，别再过 _short_place 削后缀
    在线这条路无论出什么状况（无网、无 Key、报错、空结果）都自动回落到离线结果。
    """
    place, km = ('', 0.0)
    addr, src, exact = '', 'offline', False
    if gps:
        try:
            place, km = district_of(gps[0], gps[1])
        except Exception:
            place, km = ('', 0.0)
        addr = place or ''
    if net_geo and gps:
        try:
            r = geo.online_place(gps[0], gps[1], key, level,
                                 in_china=in_china(gps[0], gps[1]))
        except Exception:
            r = None
        if r and r.get('place'):
            place = r['place']
            addr = r.get('addr') or place
            src = r.get('src') or 'online'
            exact = True
    return place, addr, km, src, exact
