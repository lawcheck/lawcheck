"""Постановка скана в работу — одна точка на все входы.

Скан запускают из трёх мест: форма на главной, подтверждение владения в
кабинете и еженедельный cron мониторинга. Раньше каждое собирало очередь
само, и у cron была своя копия этих семи строк.
"""
import logging
import uuid

from fastapi import BackgroundTasks

from lawcheck.api.routes.scan import _run_scan
from lawcheck.db import repo
from lawcheck.workers.queue import get_queue

log = logging.getLogger(__name__)

# Бюджет страниц для сканов, которые запускаем мы сами (мониторинг,
# подтверждение владения). Форма на главной присылает свой.
DEFAULT_PAGES = 25


def start_scan(bg: BackgroundTasks, url: str, max_pages: int = DEFAULT_PAGES,
               *, user_id: int | None = None) -> str:
    """Ставит скан в очередь (RQ) либо в BackgroundTasks (dev). Возвращает scan_id."""
    scan_id = uuid.uuid4().hex
    repo.create_scan(scan_id, url, max_pages)
    if user_id is not None:
        # Залогиненный запустил проверку — скан попадёт в «Мои отчёты».
        repo.set_scan_user(scan_id, user_id)
    queue = get_queue()
    if queue is not None:
        queue.enqueue("lawcheck.workers.scan_worker.run_scan",
                      scan_id, url, max_pages, job_timeout=600)
    else:
        bg.add_task(_run_scan, scan_id, url, max_pages)
    return scan_id
