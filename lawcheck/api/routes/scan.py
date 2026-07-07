"""HTTP-эндпойнты сканирования.

Хранение результатов: SQLAlchemy (sqlite в dev по умолчанию, postgres в проде).
Запуск задачи: RQ-воркер при наличии Redis, иначе FastAPI BackgroundTasks.
"""
import asyncio
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException

from lawcheck.api.schemas import FindingOut, ScanCreated, ScanRequest, ScanResult
from lawcheck.checks.registry import CHECKS
from lawcheck.crawler.browser import Browser
from lawcheck.crawler.crawler import Crawler
from lawcheck.db import repo
from lawcheck.workers.queue import get_queue

router = APIRouter()
log = logging.getLogger(__name__)


def _scan_to_result(scan) -> ScanResult:
    return ScanResult(
        scan_id=scan.id, status=scan.status, url=scan.url,
        pages_crawled=scan.pages_crawled, error=scan.error,
        findings=[FindingOut(
            check_id=f.check_id, severity=f.severity, title=f.title,
            evidence=f.evidence, location=f.location,
            law_reference=f.law_reference, recommendation=f.recommendation,
        ) for f in scan.findings],
    )


async def _run_scan(scan_id: str, url: str, max_pages: int | None) -> None:
    """In-process fallback, когда Redis недоступен (dev-режим)."""
    await asyncio.to_thread(repo.mark_running, scan_id)
    try:
        async with Browser() as browser:
            crawler = Crawler(browser, max_pages=max_pages)
            snapshot = await crawler.crawl(url)

        def _run_all_checks_and_save() -> None:
            all_findings = []
            for check in CHECKS:
                all_findings.extend(check.run(snapshot))
            repo.mark_done(scan_id, pages_crawled=len(snapshot.pages), findings=all_findings)
            from lawcheck.notify.monitoring import notify_monitoring
            notify_monitoring(url)

        await asyncio.to_thread(_run_all_checks_and_save)
    except Exception as e:
        log.exception("scan failed")
        await asyncio.to_thread(repo.mark_error, scan_id, str(e))


@router.post("/scan", response_model=ScanCreated, status_code=202)
async def create_scan(req: ScanRequest, bg: BackgroundTasks) -> ScanCreated:
    scan_id = uuid.uuid4().hex
    await asyncio.to_thread(repo.create_scan, scan_id, str(req.url), req.max_pages)

    queue = get_queue()
    if queue is not None:
        queue.enqueue(
            "lawcheck.workers.scan_worker.run_scan",
            scan_id, str(req.url), req.max_pages,
            job_timeout=600,
        )
    else:
        bg.add_task(_run_scan, scan_id, str(req.url), req.max_pages)

    return ScanCreated(scan_id=scan_id)


@router.get("/scan/{scan_id}", response_model=ScanResult)
async def get_scan(scan_id: str) -> ScanResult:
    scan = await asyncio.to_thread(repo.get_scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return _scan_to_result(scan)
