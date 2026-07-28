"""Ограничение частоты запросов для форм, которые дорого дёргать.

Что защищаем и от чего:
- регистрация шлёт письмо на ЛЮБОЙ введённый адрес, то есть без лимита это
  инструмент рассылки с нашего домена по чужим ящикам — бьёт по репутации почты;
- вход без лимита перебирается;
- сброс пароля без лимита заваливает письмами существующих пользователей;
- запуск скана поднимает Chromium и краулит ЧУЖОЙ сайт с нашего IP, поэтому
  abuse-жалобы за такой краулинг придут нам.

Окно фиксированное — этого достаточно против скриптов и переборов; точность
скользящего окна здесь не окупается.

Redis, если он есть (тот же, что под очередью RQ), иначе счётчик в памяти
процесса. Память переживает рестарт хуже и не общая между воркерами, но
деградация мягкая: лимит остаётся, просто становится «на процесс».
"""
import logging
import time
from threading import Lock

from fastapi import HTTPException, Request

from lawcheck.workers.queue import get_queue

log = logging.getLogger(__name__)

# Счётчик в памяти: ключ → (когда окно истекает, сколько запросов было).
_local: dict[str, tuple[float, int]] = {}
_local_lock = Lock()
# Чистим память не чаще раза в минуту, чтобы редкие ключи не копились вечно.
_last_sweep = 0.0
_SWEEP_EVERY_SEC = 60


def reset() -> None:
    """Сбросить счётчик в памяти. Нужен тестам: состояние глобальное на процесс."""
    global _last_sweep
    with _local_lock:
        _local.clear()
        _last_sweep = 0.0


def client_ip(request: Request) -> str:
    """IP клиента с учётом того, что мы стоим за Caddy.

    Caddy ДОПИСЫВАЕТ реальный адрес пира в конец X-Forwarded-For, поэтому берём
    ПОСЛЕДНИЙ элемент: первые могут быть подделаны самим клиентом, если он
    прислал свой X-Forwarded-For.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        last = xff.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def _sweep_locked(now: float) -> None:
    global _last_sweep
    if now - _last_sweep < _SWEEP_EVERY_SEC:
        return
    _last_sweep = now
    for key in [k for k, (exp, _) in _local.items() if exp <= now]:
        del _local[key]


def _hit_local(key: str, window_sec: int) -> int:
    now = time.monotonic()
    with _local_lock:
        _sweep_locked(now)
        expires, count = _local.get(key, (0.0, 0))
        if expires <= now:
            expires, count = now + window_sec, 0
        count += 1
        _local[key] = (expires, count)
        return count


def _hit_redis(conn, key: str, window_sec: int) -> int | None:
    """Счётчик в Redis. None — если Redis подвёл (тогда падаем на память)."""
    try:
        pipe = conn.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_sec, nx=True)  # TTL ставим только при создании
        count, _ = pipe.execute()
        return int(count)
    except Exception as e:
        log.warning("ratelimit: redis недоступен (%s) — считаем в памяти", e)
        return None


def hit(bucket: str, identity: str, *, limit: int, window_sec: int) -> bool:
    """Учесть запрос. True — лимит исчерпан, запрос надо отклонить."""
    key = f"rl:{bucket}:{identity}"
    queue = get_queue()
    count = None
    if queue is not None:
        count = _hit_redis(queue.connection, key, window_sec)
    if count is None:
        count = _hit_local(key, window_sec)
    return count > limit


def exceeded(request: Request, bucket: str, *, limit: int, window_sec: int,
             extra: str = "") -> bool:
    """Учесть запрос и сказать, исчерпан ли лимит. Для мест, где нужен свой
    ответ вместо 429 (например, отрисовать ошибку прямо в форме).

    `extra` добавляется к ключу — так один и тот же email нельзя перебирать
    с разных адресов, а один адрес не может дёргать разные email.
    """
    identity = client_ip(request)
    if extra:
        identity = f"{identity}|{extra.strip().lower()}"
    if hit(bucket, identity, limit=limit, window_sec=window_sec):
        log.info("ratelimit: %s исчерпан для %s", bucket, identity)
        return True
    return False


def enforce(request: Request, bucket: str, *, limit: int, window_sec: int,
            extra: str = "", message: str = "") -> None:
    """Бросает 429, если лимит исчерпан."""
    if exceeded(request, bucket, limit=limit, window_sec=window_sec, extra=extra):
        raise HTTPException(
            status_code=429,
            detail=message or "Слишком много попыток. Попробуйте позже.",
            headers={"Retry-After": str(window_sec)},
        )
