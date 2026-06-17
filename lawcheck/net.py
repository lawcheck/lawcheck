"""IPv4-only резолвинг + обход частичной блокировки Telegram из РФ-ДЦ.

1. Docker bridge-сеть без IPv6, но публичные хосты отдают и AAAA; httpx/httpcore
   не делают Happy Eyeballs — берут первый адрес (часто IPv6) и падают с
   «Network is unreachable». Отфильтровываем AAAA.
2. РФ-дата-центры (Timeweb) режут часть диапазонов Telegram: текущий IP
   api.telegram.org может быть недоступен, а соседний DC — рабочим. Для
   api.telegram.org пробуем кандидатов и кешируем первый отвечающий по 443.
   SNI/проверка сертификата остаются на api.telegram.org (меняем только адрес).
"""
import socket

_orig_getaddrinfo = socket.getaddrinfo

_TELEGRAM_HOST = "api.telegram.org"
# Запасные IP Bot API на случай блокировки текущего адреса домена.
_TELEGRAM_FALLBACK_IPS = [
    "149.154.167.220", "149.154.167.197", "149.154.167.222",
    "149.154.175.50", "91.108.4.5",
]
_tg_ip_cache: str | None = None


def _pick_telegram_ip() -> str | None:
    global _tg_ip_cache
    if _tg_ip_cache:
        return _tg_ip_cache
    candidates: list[str] = []
    try:
        candidates += [r[4][0] for r in _orig_getaddrinfo(_TELEGRAM_HOST, 443, socket.AF_INET)]
    except Exception:
        pass
    candidates += [ip for ip in _TELEGRAM_FALLBACK_IPS if ip not in candidates]
    for ip in candidates:
        try:
            socket.create_connection((ip, 443), timeout=4).close()
            _tg_ip_cache = ip
            return ip
        except Exception:
            continue
    return None


def _ipv4_only(host, port, family=0, *args, **kwargs):
    if host == _TELEGRAM_HOST:
        ip = _pick_telegram_ip()
        if ip:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    res = _orig_getaddrinfo(host, port, family, *args, **kwargs)
    v4 = [r for r in res if r[0] == socket.AF_INET]
    return v4 or res


def force_ipv4() -> None:
    """Идемпотентно подменяет socket.getaddrinfo (IPv4 + пиннинг Telegram)."""
    socket.getaddrinfo = _ipv4_only
