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
from lawcheck.net import force_ipv4
from lawcheck.notify.monitoring import notify_monitoring
from lawcheck.notify.telegram import esc, notify_owner

# Контейнер IPv4-only — нужно и воркеру (рассылка клиентских diff в Telegram).
force_ipv4()

log = logging.getLogger(__name__)


def run_scan(scan_id: str, url: str, max_pages: int | None) -> None:
    """Синхронная обёртка для RQ — внутри прокручиваем async-краулер."""
    repo.mark_running(scan_id)
    try:
        asyncio.run(_crawl_and_check(scan_id, url, max_pages))
    except Exception as e:
        log.exception("scan %s failed", scan_id)
        repo.mark_error(scan_id, str(e))
    finally:
        _reap_stale()


def _reap_stale() -> None:
    """Зависшие «running» закрыть задним числом и доложить владельцу.

    Каждый прогон воркера подметает чужие зависшие сканы: RQ убивает процесс
    по job_timeout сигналом, и хвост run_scan (mark_error) не успевает
    выполниться — без этого скан висит «running» вечно. Алерт только про
    реально исправленные, чтобы не спамить при каждом запуске.
    """
    try:
        for scan in repo.reap_stale_scans():
            log.warning("скан %s (%s) завис в running — помечен как error",
                        scan.id[:8], scan.url)
            notify_owner(
                f"⚠️ Скан <b>{esc(scan.url)}</b> не уложился в лимит времени "
                f"и остановлен (заказу он был виден как вечная загрузка).")
    except Exception:
        log.exception("reap stale scans failed")


async def _crawl_and_check(scan_id: str, url: str, max_pages: int | None) -> None:
    from lawcheck.checks.registry import CHECKS

    async with Browser() as browser:
        crawler = Crawler(browser, max_pages=max_pages)
        snapshot = await crawler.crawl(url)

    all_findings = []
    for check in CHECKS:
        all_findings.extend(check.run(snapshot))
    repo.mark_done(scan_id, pages_crawled=len(snapshot.pages), findings=all_findings)
    # Если сайт под клиентским мониторингом — разослать diff в Telegram.
    notify_monitoring(url)
