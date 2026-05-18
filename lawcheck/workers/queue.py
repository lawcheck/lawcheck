"""Подключение к Redis и доступ к очереди RQ.

В тестах / dev без Redis — fallback: если REDIS_URL пустой или подключение
не удалось, API сам выполняет задачу через BackgroundTasks.
"""
import logging
from functools import lru_cache

from lawcheck.config import settings

log = logging.getLogger(__name__)

QUEUE_NAME = "lawcheck"


@lru_cache(maxsize=1)
def get_queue():
    """Возвращает rq.Queue или None, если Redis недоступен."""
    redis_url = getattr(settings, "redis_url", "") or ""
    if not redis_url:
        return None
    try:
        from redis import Redis  # noqa: WPS433
        from rq import Queue     # noqa: WPS433
    except ImportError:  # pragma: no cover
        log.warning("rq/redis не установлены — задачи будут идти через BackgroundTasks")
        return None
    try:
        conn = Redis.from_url(redis_url, socket_timeout=2, socket_connect_timeout=2)
        conn.ping()
    except Exception as e:
        log.warning("Redis недоступен (%s) — задачи будут идти через BackgroundTasks", e)
        return None
    return Queue(QUEUE_NAME, connection=conn)
