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
import time

_orig_getaddrinfo = socket.getaddrinfo

_TELEGRAM_HOST = "api.telegram.org"
# Запасные IP Bot API на случай блокировки текущего адреса домена.
_TELEGRAM_FALLBACK_IPS = [
    "149.154.167.220", "149.154.167.197", "149.154.167.222",
    "149.154.175.50", "91.108.4.5",
]
_tg_ip_cache: str | None = None
_tg_ip_cached_at: float = 0.0
# Кеш протухает: без TTL выбранный адрес пиннился на весь процесс, и если он
# переставал отвечать, воркер долбился в мёртвый IP до рестарта контейнера.
_TG_IP_TTL_SEC = 600
# Проба на один адрес. Было 4 с, и этого хватало, чтобы перебор не уложился в
# таймаут вызывающего: getaddrinfo отдаёт один и тот же адрес трижды, и если
# он заблокирован — это 12 с только на дубликатах при 10 с у httpx. Отсюда и
# бралось «уведомления то доходят, то нет»: кеш живёт 10 минут, и первая
# отправка после каждого протухания играла в орлянку. Заблокированный адрес
# не отвечает вовсе, живой отвечает за миллисекунды — полутора секунд хватает,
# а весь перебор укладывается в таймаут с запасом.
_TG_PROBE_TIMEOUT_SEC = 1.5


def _telegram_candidates() -> list[str]:
    """Адреса для перебора: сначала из DNS, затем запасные. Без повторов.

    Дедупликация обязательна: резолвер возвращает один и тот же адрес по
    числу записей, и без неё перебор тратит таймаут на повторные пробы
    того же самого заблокированного адреса.
    """
    candidates: list[str] = []
    try:
        candidates += [str(r[4][0])
                       for r in _orig_getaddrinfo(_TELEGRAM_HOST, 443, socket.AF_INET)]
    except Exception:
        pass
    candidates += _TELEGRAM_FALLBACK_IPS
    return list(dict.fromkeys(candidates))


def _pick_telegram_ip() -> str | None:
    global _tg_ip_cache, _tg_ip_cached_at
    if _tg_ip_cache and (time.monotonic() - _tg_ip_cached_at) < _TG_IP_TTL_SEC:
        return _tg_ip_cache
    _tg_ip_cache = None
    for ip in _telegram_candidates():
        try:
            socket.create_connection((ip, 443), timeout=_TG_PROBE_TIMEOUT_SEC).close()
            _tg_ip_cache = ip
            _tg_ip_cached_at = time.monotonic()
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
