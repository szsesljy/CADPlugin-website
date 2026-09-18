"""IP 归属地区判断（离线 GeoLite2 国家库，仅区分中国大陆/其他地区）

数据库来自 maxminddb-geolite2 包自带的 GeoLite2-Country（2018 版，国家级精度）。
国家代码 CN 视为中国大陆；港澳台及海外、私有地址、未知归属一律归入其他地区。
"""
from functools import lru_cache

from geolite2 import geolite2

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        _reader = geolite2.reader()
    return _reader


@lru_cache(maxsize=8192)
def ip_region(ip: str) -> str:
    """返回 IP 的地区：中国大陆 / 其他地区"""
    if not ip:
        return "其他地区"
    try:
        info = _get_reader().get(ip.strip())
    except Exception:
        return "其他地区"
    try:
        iso_code = (info or {}).get("country", {}).get("iso_code")
    except Exception:
        iso_code = None
    return "中国大陆" if iso_code == "CN" else "其他地区"
