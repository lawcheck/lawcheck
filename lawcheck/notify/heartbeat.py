"""Сторож за задачами по расписанию: молчание считается отказом.

Обычный алерт ловит пойманное исключение. Задача, которая не запустилась
вовсе, исключения не бросает — и именно так были потеряны три недели бэкапов
Postgres и три прогона мониторинга клиентских сайтов: после переезда деплоя
хостовые скрипты падали на первой строке в лог-файл, который никто не читает.

Схема обратная привычной: cron отмечается ПОСЛЕ успеха
(`POST /internal/heartbeat/{job}`), а приложение раз в час смотрит, у какой
задачи отметка просрочена. Проверка живёт внутри api, а не в ещё одном cron:
сторож, которого заводит тот же механизм, за которым он следит, охраняет сам
себя. Если api не жив, сайт лежит — это заметно и без алертов.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from lawcheck.db import repo
from lawcheck.db.models import utcnow
from lawcheck.notify import telegram

log = logging.getLogger(__name__)

# Задача → сколько ждать до тревоги. Запас к периоду нужен, иначе алерт
# приходит от обычного сдвига запуска на пару минут.
JOBS: dict[str, timedelta] = {
    "backup": timedelta(hours=26),           # хостовый cron, ежедневно 03:30
    "monitoring": timedelta(days=8),         # хостовый cron, понедельник 04:15
    "telegram-health": timedelta(hours=26),  # хостовый cron, ежедневно
}
# Сюда попадают только задачи хостового cron: именно они тихо отваливаются,
# потому что за ними никто не следит. Контейнерные циклы (`followups`,
# `inbox`) держит compose с restart: unless-stopped — у них другой режим
# отказа. Добавлять их сюда без реальной отметки нельзя: задача без отметок
# просроченной не считается, и строка в списке выглядела бы работающей
# проверкой, не будучи ей. Их `|| true` глушит ошибки не хуже — но это
# отдельная задача.

# Пока задачу не починили, напоминаем раз в сутки, а не каждый час.
_ALERT_EVERY = timedelta(hours=24)
_CHECK_EVERY_SEC = 3600


def _aware(value: datetime) -> datetime:
    """sqlite отдаёт naive datetime — нормализуем к UTC (как в db/repo.py).

    Без этого вычитание дат бросает TypeError внутри фоновой проверки, она
    ловится общим except и уходит в лог — сторож молчал бы ровно тем способом,
    который и должен ловить.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def overdue() -> list[tuple[str, str]]:
    """Просроченные задачи: [(имя, человекочитаемый возраст отметки)].

    Задача без единой отметки НЕ считается просроченной: так выглядит только
    что выкаченная схема, и поднимать по ней тревогу — значит приучить к
    ложным срабатываниям. Она станет видимой, как только отметится впервые.
    """
    now = utcnow()
    seen = {r.name: r for r in repo.list_job_runs()}
    late = []
    for name, limit in JOBS.items():
        row = seen.get(name)
        if row is None:
            continue
        age = now - _aware(row.last_ok_at)
        if age > limit:
            hours = int(age.total_seconds() // 3600)
            late.append((name, f"{hours // 24} сут {hours % 24} ч" if hours >= 24
                         else f"{hours} ч"))
    return late


def check_and_alert() -> list[str]:
    """Найти просроченные задачи и сообщить владельцу. Возвращает их имена."""
    late = overdue()
    if not late:
        return []
    now = utcnow()
    to_report = [(n, age) for n, age in late
                 if repo.job_alert_due(n, now - _ALERT_EVERY)]
    if not to_report:
        return [n for n, _ in late]
    lines = "\n".join(f"• <b>{telegram.esc(n)}</b> — молчит {telegram.esc(age)}"
                      for n, age in to_report)
    telegram.notify_owner(
        f"🔴 Задачи по расписанию не отмечались:\n{lines}\n\n"
        f"Смотреть логи на проде: <code>/var/log/lawcheck-*.log</code>")
    for name, _ in to_report:
        repo.mark_job_alerted(name, now)
    log.error("heartbeat: просрочены %s", [n for n, _ in to_report])
    return [n for n, _ in late]


async def watch() -> None:
    """Фоновая проверка раз в час. Запускается из api на старте."""
    while True:
        try:
            await asyncio.to_thread(check_and_alert)
        except Exception:
            # Сторож не имеет права уронить приложение — но и молчать о своей
            # поломке не должен, поэтому не `pass`, а полный трейсбек в лог.
            log.exception("heartbeat: проверка расписаний упала")
        await asyncio.sleep(_CHECK_EVERY_SEC)
