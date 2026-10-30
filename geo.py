# -*- coding: utf-8 -*-
"""联网精确定位（可选功能，默认关闭）。

本地离线库（data/districts.csv + cities_world.csv）只到「市 + 区县 / 城市」这一级，
而且点位稀少时误差可以到几十公里。开启本模块后，对**带 GPS 的文件**再问一次在线
地图服务，拿到更下一级的地址（街道 / 乡镇 / 道路 / POI），拼进文件名和元数据。

设计前提是「离线优先、联网只是增强」：

  * 开关默认关闭，关闭时本模块一行网络代码都不跑；
  * 无网、无 Key、接口报错、返回为空 —— 一律静默回落到离线库，不弹错、不中断；
  * 单次请求带超时（默认 5 秒）；连续失败 3 次就熔断，本轮剩下的文件不再等网络，
    避免几百个文件每个都卡 5 秒；
  * 结果按「坐标 + 粒度」缓存到 data/geo_online_cache.json，同一地点只问一次；
  * 国内坐标必须先 WGS-84 → GCJ-02 再请高德，否则整体偏移几百米；
  * 国外用 Nominatim 免费接口（遵守其使用政策：带真实 User-Agent、≤1 次/秒、
    结果保留 OSM 署名）。

只发送经纬度，不发送文件本身，也不发送文件名。
"""
import io
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

from strings import T

AMAP_URL = 'https://restapi.amap.com/v3/geocode/regeo'
NOMI_URL = 'https://nominatim.openstreetmap.org/reverse'
# Nominatim 的使用政策要求能联系到使用方，必须带真实 UA（否则会收到 403）
UA = 'SeeName/1.0 (https://github.com/tanhua-dev/SeeName; offline-first media renamer)'
TIMEOUT = 5.0
NOMI_GAP = 1.1        # Nominatim 限 1 次/秒
FAIL_MAX = 3          # 连续失败这么多次后熔断

CACHE_FILE = 'geo_online_cache.json'

_S = {'fails': 0, 'dead': False, 'net': None, 'net_t': 0.0, 'last_nomi': 0.0,
      'hits': 0, 'miss': 0, 'reason': ''}
_C = None


# ------------------------------------------------------------------ 基础
def _data_dir() -> str:
    """可写数据目录（缓存放这里）。Windows=软件目录；macOS .app 内只读，
    改放 ~/Library/Application Support/SeeName/data（与 engine 同一套逻辑）。"""
    if sys.platform == 'darwin' and getattr(sys, 'frozen', False):
        return os.path.join(os.path.expanduser('~'), 'Library',
                            'Application Support', 'SeeName', 'data')
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'data')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


def _cache() -> dict:
    global _C
    if _C is None:
        p = os.path.join(_data_dir(), CACHE_FILE)
        try:
            with io.open(p, encoding='utf-8') as f:
                v = json.load(f)
            _C = v if isinstance(v, dict) else {}
        except Exception:
            _C = {}
    return _C


def _cache_save():
    if _C is None:
        return
    try:
        p = os.path.join(_data_dir(), CACHE_FILE)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with io.open(p, 'w', encoding='utf-8') as f:
            json.dump(_C, f, ensure_ascii=False, indent=0)
    except Exception:
        pass


def reset():
    """新一轮任务开始：清掉熔断计数（缓存不清，跨轮复用）。"""
    _S.update({'fails': 0, 'dead': False, 'hits': 0, 'miss': 0, 'reason': ''})


def status() -> dict:
    return dict(_S)


def _fail(reason: str):
    _S['fails'] += 1
    _S['miss'] += 1
    _S['reason'] = reason
    if _S['fails'] >= FAIL_MAX:
        _S['dead'] = True


def _ok():
    _S['fails'] = 0
    _S['hits'] += 1


# ------------------------------------------------------------------ WGS-84 → GCJ-02
# 手机拍照/录像写进 EXIF 的是 WGS-84（GPS 原始坐标）。高德/腾讯/国内所有合规地图
# 服务都用 GCJ-02（火星坐标），直接把 WGS-84 丢过去会整体偏移 300~600 米，
# 落在城市里就是「差一条街到两条街」，所以这一步不能省。
_X_PI = 3.14159265358979324 * 3000.0 / 180.0
_A = 6378245.0
_EE = 0.00669342162296594323


def _out_of_china(lat: float, lng: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _t_lat(x: float, y: float) -> float:
    r = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
    r += 0.2 * math.sqrt(abs(x))
    r += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    r += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    r += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return r


def _t_lng(x: float, y: float) -> float:
    r = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    r += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    r += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    r += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return r


def wgs2gcj(lat: float, lng: float) -> tuple[float, float]:
    """WGS-84 → GCJ-02（火星坐标）。范围外原样返回。"""
    try:
        if _out_of_china(lat, lng):
            return lat, lng
        dlat = _t_lat(lng - 105.0, lat - 35.0)
        dlng = _t_lng(lng - 105.0, lat - 35.0)
        rlat = lat / 180.0 * math.pi
        m = math.sin(rlat)
        m = 1 - _EE * m * m
        sm = math.sqrt(m)
        dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (m * sm) * math.pi)
        dlng = (dlng * 180.0) / (_A / sm * math.cos(rlat) * math.pi)
        return lat + dlat, lng + dlng
    except Exception:
        return lat, lng


# ------------------------------------------------------------------ 网络
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url: str, params: dict, headers: dict | None = None,
         timeout: float = TIMEOUT):
    """GET 一个 JSON 接口。先按系统代理走，不通再直连一次（两种网络环境都照顾到）。"""
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request('%s?%s' % (url, q), headers=headers or {})
    for op in (urllib.request.urlopen, _NO_PROXY.open):
        try:
            with op(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8', 'replace'))
        except Exception:
            continue
    return None


def net_ok(force: bool = False) -> bool:
    """有没有网。结果缓存 60 秒（逐个文件探测太慢）。"""
    now = time.time()
    if not force and _S['net'] is not None and now - _S['net_t'] < 60:
        return bool(_S['net'])
    ok = False
    try:
        req = urllib.request.Request('https://restapi.amap.com/v3/ip?key=',
                                     headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=3.0) as r:
            ok = r.status == 200
    except Exception:
        try:
            req = urllib.request.Request('https://nominatim.openstreetmap.org/status',
                                         headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=3.0) as r:
                ok = r.status == 200
        except Exception:
            ok = False
    _S['net'], _S['net_t'] = ok, now
    return ok


# ------------------------------------------------------------------ 高德（国内）
def _suffix_off(s: str, suf: str) -> str:
    return s[:-len(suf)] if (s and s.endswith(suf) and len(s) > len(suf)) else s


def _cn_place(c: dict, level: str) -> tuple[str, str]:
    """高德 addressComponent → (文件名用的短地名, 完整地址)。"""
    prov = (c.get('province') or '').strip()
    city = (c.get('city') or '').strip() or prov
    dist = (c.get('district') or '').strip()
    town = (c.get('township') or '').strip()
    street = ((c.get('streetNumber') or {}).get('street') or '').strip()
    num = ((c.get('streetNumber') or {}).get('number') or '').strip()

    # 注意：行政区划后缀只按中文字面量处理（不能过 T()，英文界面下地名还是中文）
    city_s = _suffix_off(city, '市')
    if not city and prov:
        city_s = _suffix_off(prov, '省')
    parts = [p for p in (city_s, dist) if p]
    if level == 'street':
        sub = town or street
        if sub:
            parts.append(sub)
    place = ''.join(parts)

    addr_bits = [p for p in (prov, city, dist, town, street + num) if p]
    seen, addr = set(), []
    for b in addr_bits:
        if b and b not in seen:
            seen.add(b)
            addr.append(b)
    return place, ''.join(addr) if addr else place


def amap_reverse(lat: float, lng: float, key: str, level: str) -> dict | None:
    """高德逆地理编码。key 为空直接返回 None（不占用网络）。"""
    if not (key or '').strip():
        return None
    glat, glng = wgs2gcj(lat, lng)
    d = _get(AMAP_URL, {
        'key': key.strip(), 'output': 'JSON',
        'location': '%.6f,%.6f' % (glng, glat),
        'extensions': 'base', 'radius': '1000', 'roadlevel': '0',
    }, headers={'User-Agent': UA})
    if not d or str(d.get('status')) != '1':
        return None
    rg = d.get('regeocode') or {}
    comp = rg.get('addressComponent') or {}
    if not comp:
        return None
    place, addr = _cn_place(comp, level)
    if not place:
        return None
    return {'place': place, 'addr': addr or (rg.get('formatted_address') or place),
            'src': 'amap', 'raw': comp}


# ------------------------------------------------------------------ Nominatim（国外）
def nominatim_reverse(lat: float, lng: float, level: str) -> dict | None:
    """OSM Nominatim 逆地理编码。自带 1 次/秒的节流，UA 必带。"""
    gap = NOMI_GAP - (time.time() - _S['last_nomi'])
    if gap > 0:
        time.sleep(gap)
    d = _get(NOMI_URL, {
        'format': 'jsonv2', 'lat': '%.6f' % lat, 'lon': '%.6f' % lng,
        'zoom': '17' if level == 'street' else '12',
        'accept-language': 'zh-CN,zh', 'addressdetails': '1',
    }, headers={'User-Agent': UA, 'Accept-Language': 'zh-CN,zh'})
    _S['last_nomi'] = time.time()
    if not d:
        return None
    a = d.get('address') or {}
    if not a:
        return None
    city = (a.get('city') or a.get('town') or a.get('village')
            or a.get('municipality') or a.get('county') or '')
    country = a.get('country') or ''
    get = lambda k: (a.get(k) or '').strip()
    if level == 'street':
        sub = get('suburb') or get('neighbourhood') or get('quarter') or get('road')
        place = ''.join([p for p in (city, sub) if p])
    else:
        place = city
    if not place:
        place = get('state') or get('country')
    if not place:
        return None
    # 保留 OSM 署名：addr 里附一行来源，写进元数据时才不会丢掉 CC-BY 的署名义务
    addr = (d.get('display_name') or place)
    return {'place': place, 'addr': addr, 'src': 'nominatim',
            'country': country, 'raw': a}


# ------------------------------------------------------------------ 对外主入口
def online_place(lat: float, lng: float, key: str = '', level: str = 'district',
                 in_china=None) -> dict | None:
    """查在线地址；拿不到就返回 None（调用方回落离线库）。

    in_china: 由调用方（engine.in_china）决定走高德还是 Nominatim；
              不传时按矩形粗判。
    """
    if not lat or not lng:
        return None
    level = 'street' if level == 'street' else 'district'
    ck = '%.5f,%.5f,%s' % (lat, lng, level)
    hit = _cache().get(ck)
    if hit is not None:
        # 空串代表「查过但没结果」，同样直接复用，不去重复请求
        return hit or None
    if _S['dead']:
        return None
    if not net_ok():
        # net_ok 自己的结果缓存 60 秒，后面的文件不会跟着一起等超时
        _fail(T('无网络'))
        return None
    if in_china is None:
        in_china = (18.0 <= lat <= 54.0 and 73.0 <= lng <= 135.0)
    try:
        r = (amap_reverse(lat, lng, key, level) if in_china
             else nominatim_reverse(lat, lng, level))
    except Exception:
        r = None
    if r and r.get('place'):
        _ok()
        _cache()[ck] = r
        _cache_save()
        return r
    _fail(T('在线接口无结果'))
    try:
        _cache()[ck] = ''
        _cache_save()
    except Exception:
        pass
    return None


def cache_clear():
    """清掉在线地址缓存（设置里换了 Key / 粒度之后用得上）。"""
    global _C
    _C = {}
    try:
        p = os.path.join(_data_dir(), CACHE_FILE)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


if __name__ == '__main__':
    # 手工验证：python geo.py <lat> <lng> [amap_key]
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('lat', type=float)
    ap.add_argument('lng', type=float)
    ap.add_argument('key', nargs='?', default='')
    a = ap.parse_args()
    print(T('网络'), net_ok())
    print(T('GCJ-02'), wgs2gcj(a.lat, a.lng))
    for lv in ('district', 'street'):
        print(lv, online_place(a.lat, a.lng, a.key, lv))
