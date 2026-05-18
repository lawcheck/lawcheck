"""RQ-воркер: выполняет одно сканирование и пишет результат в БД.

В проде запускается отдельным процессом (см. docker-compose.yml):
    rq worker --url $REDIS_URL lawcheck

Контракт с API: API кладёт в очередь задачу с именем функции
`lawcheck.workers.scan_worker.run_scan` и аргументами (scan_id, url, max_pages).
Воркер ничего не возвращает — состояние читается через GET /scan/{id}.
"""
import asyncio
import logging

from lawcheck.crawler.browser import Browser
from lawcheck.crawler.crawler import Crawler
from lawcheck.db import repo

log = logging.getLogger(__name__)


def run_scan(scan_id: str, url: str, max_pages: int | None) -> None:
    """Синхронная обёртка для RQ — внутри прокручиваем async-краулер."""
    repo.mark_running(scan_id)
    try:
        asyncio.run(_crawl_and_check(scan_id, url, max_pages))
    except Exception as e:
        log.exception("scan %s failed", scan_id)
        repo.mark_error(scan_id, str(e))


async def _crawl_and_check(scan_id: str, url: str, max_pages: int | None) -> None:
    from lawcheck.checks.registry import CHECKS

    async with Browser() as browser:
        crawler = Crawler(browser, max_pages=max_pages)
        snapshot = await crawler.crawl(url)

    all_findings = []
    for check in CHECKS:
        all_findings.extend(check.run(snapshot))
    repo.mark_done(scan_id, pages_crawled=len(snapshot.pages), findings=all_findings)
