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
from concurrent.futures import ThreadPoolExecutor, as_completed

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


def _probe(ip: str) -> bool:
    try:
        socket.create_connection((ip, 443), timeout=_TG_PROBE_TIMEOUT_SEC).close()
        return True
    except Exception:
        return False


def _pick_telegram_ip() -> str | None:
    """Первый отвечающий адрес из списка кандидатов, с кешем на TTL.

    Пробы идут ПАРАЛЛЕЛЬНО. Последовательный перебор стоил столько, сколько
    мёртвых адресов встретится до живого: на проде из шести кандидатов
    отвечает ровно один, то есть до пяти проб по 1.5 с — девять секунд при
    бюджете подключения в пять. Именно так и выглядели «зависания» по 15 с:
    httpx упирался в свой таймаут внутри резолвера, а не на соединении с
    Telegram (прямой запрос к живому адресу укладывается в 0.34 с).

    Параллельно перебор стоит столько, сколько думает САМЫЙ БЫСТРЫЙ живой
    адрес, а не сумма таймаутов мёртвых. Побеждает первый ответивший: любой
    адрес, обслуживающий Bot API, одинаково пригоден, а предпочтение порядку
    стоило бы ожидания более приоритетных проб — то есть ровно той задержки,
    ради устранения которой всё и делается.
    """
    global _tg_ip_cache, _tg_ip_cached_at
    if _tg_ip_cache and (time.monotonic() - _tg_ip_cached_at) < _TG_IP_TTL_SEC:
        return _tg_ip_cache
    _tg_ip_cache = None
    candidates = _telegram_candidates()
    if not candidates:
        return None
    # Берём ПЕРВЫЙ ответивший и не ждём остальных. Ожидание всех проб сводило
    # параллельность на нет: живой адрес отвечает за миллисекунды, а каждый
    # мёртвый честно выбирает свой таймаут, и перебор снова стоил полторы
    # секунды вместо сотых. Executor гасим без ожидания — недобежавшие пробы
    # никому не мешают и завершатся сами.
    ex = ThreadPoolExecutor(max_workers=len(candidates))
    try:
        futures = {ex.submit(_probe, ip): ip for ip in candidates}
        try:
            for fut in as_completed(futures, timeout=_TG_PROBE_TIMEOUT_SEC + 0.5):
                if fut.result():
                    _tg_ip_cache = futures[fut]
                    _tg_ip_cached_at = time.monotonic()
                    return _tg_ip_cache
        except TimeoutError:
            pass
        return None
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def _ipv4_only(host, port, family=0, *args, **kwargs):
    if host == _TELEGRAM_HOST:
        ip = _pick_telegram_ip()
        if ip:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    res = _orig_getaddrinfo(host, port, family, *args, **kwargs)
    v4 = [r for r in res if r[0] == socket.AF_INET]
    return v4 or res


def reset_telegram_ip() -> None:
    """Забыть выбранный адрес, чтобы следующий запрос перебрал заново.

    Нужно на повторе после сетевой ошибки: DPI роняет часть соединений уже
    после успешного TCP-рукопожатия, поэтому «адрес отвечает на пробу» не
    значит «по нему пройдёт запрос». Держаться за него до истечения TTL в
    такой ситуации — значит повторять в ту же стену.
    """
    global _tg_ip_cache, _tg_ip_cached_at
    _tg_ip_cache = None
    _tg_ip_cached_at = 0.0


def force_ipv4() -> None:
    """Идемпотентно подменяет socket.getaddrinfo (IPv4 + пиннинг Telegram)."""
    socket.getaddrinfo = _ipv4_only
