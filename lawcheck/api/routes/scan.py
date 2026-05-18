"""HTTP-эндпойнты сканирования.

Хранение результатов: SQLAlchemy (sqlite по умолчанию, postgres в проде).
Запуск сканирования: FastAPI BackgroundTasks (RQ-воркер планируется
следующей итерацией).
"""
import asyncio
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException

from lawcheck.api.schemas import FindingOut, ScanCreated, ScanResult
from lawcheck.api.schemas import ScanRequest
from lawcheck.checks.cookies.banner import CookieBannerCheck
from lawcheck.checks.cookies.inventory import TrackersInventoryCheck
from lawcheck.checks.pd_152.form_consent import FormConsentCheck
from lawcheck.checks.pd_152.forms_inventory import FormsInventoryCheck
from lawcheck.checks.pd_152.policy_presence import PolicyPresenceCheck
from lawcheck.checks.pd_152.policy_sections import PolicySectionsCheck
from lawcheck.checks.pd_152.policy_validity import PolicyValidityCheck
from lawcheck.checks.requisites.egrul_match import EgrulMatchCheck
from lawcheck.checks.requisites.presence import RequisitesPresenceCheck
from lawcheck.checks.requisites.rkn_match import RknOperatorCheck
from lawcheck.crawler.browser import Browser
from lawcheck.crawler.crawler import Crawler
from lawcheck.db import repo

router = APIRouter()
log = logging.getLogger(__name__)

CHECKS = [
    PolicyPresenceCheck(),
    PolicyValidityCheck(),
    PolicySectionsCheck(),
    FormsInventoryCheck(),
    FormConsentCheck(),
    TrackersInventoryCheck(),
    CookieBannerCheck(),
    RequisitesPresenceCheck(),
    EgrulMatchCheck(),
    RknOperatorCheck(),
]


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
    await asyncio.to_thread(repo.mark_running, scan_id)
    try:
        async with Browser() as browser:
            crawler = Crawler(browser, max_pages=max_pages)
            snapshot = await crawler.crawl(url)

        # Проверки и запись в БД — синхронные. Гоним в threadpool, чтобы не
        # блокировать event loop FastAPI (E2/C2 ходят в httpx.Client).
        def _run_all_checks_and_save() -> None:
            all_findings = []
            for check in CHECKS:
                all_findings.extend(check.run(snapshot))
            repo.mark_done(scan_id, pages_crawled=len(snapshot.pages), findings=all_findings)

        await asyncio.to_thread(_run_all_checks_and_save)
    except Exception as e:
        log.exception("scan failed")
        await asyncio.to_thread(repo.mark_error, scan_id, str(e))


@router.post("/scan", response_model=ScanCreated, status_code=202)
async def create_scan(req: ScanRequest, bg: BackgroundTasks) -> ScanCreated:
    scan_id = uuid.uuid4().hex
    await asyncio.to_thread(repo.create_scan, scan_id, str(req.url), req.max_pages)
    bg.add_task(_run_scan, scan_id, str(req.url), req.max_pages)
    return ScanCreated(scan_id=scan_id)


@router.get("/scan/{scan_id}", response_model=ScanResult)
async def get_scan(scan_id: str) -> ScanResult:
    scan = await asyncio.to_thread(repo.get_scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return _scan_to_result(scan)
