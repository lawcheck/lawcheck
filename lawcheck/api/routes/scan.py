"""HTTP-эндпойнты сканирования.

TODO: для прода вынести запуск проверки в RQ-воркер и хранить результат в БД.
Сейчас — FastAPI BackgroundTasks + in-memory store, чтобы вертикальный срез работал
без зависимостей от Redis/PostgreSQL.
"""
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException

from lawcheck.api.schemas import FindingOut, ScanCreated, ScanRequest, ScanResult
from lawcheck.checks.pd_152.form_consent import FormConsentCheck
from lawcheck.checks.pd_152.forms_inventory import FormsInventoryCheck
from lawcheck.checks.pd_152.policy_presence import PolicyPresenceCheck
from lawcheck.checks.pd_152.policy_sections import PolicySectionsCheck
from lawcheck.checks.pd_152.policy_validity import PolicyValidityCheck
from lawcheck.crawler.browser import Browser
from lawcheck.crawler.crawler import Crawler

router = APIRouter()
log = logging.getLogger(__name__)

_STORE: dict[str, ScanResult] = {}

CHECKS = [
    PolicyPresenceCheck(),
    PolicyValidityCheck(),
    PolicySectionsCheck(),
    FormsInventoryCheck(),
    FormConsentCheck(),
]


async def _run_scan(scan_id: str, url: str, max_pages: int | None) -> None:
    result = _STORE[scan_id]
    result.status = "running"
    try:
        async with Browser() as browser:
            crawler = Crawler(browser, max_pages=max_pages)
            snapshot = await crawler.crawl(url)

        findings = []
        for check in CHECKS:
            for f in check.run(snapshot):
                findings.append(FindingOut(
                    check_id=f.check_id, severity=f.severity.value, title=f.title,
                    evidence=f.evidence, location=f.location,
                    law_reference=f.law_reference, recommendation=f.recommendation,
                ))

        result.pages_crawled = len(snapshot.pages)
        result.findings = findings
        result.status = "done"
    except Exception as e:
        log.exception("scan failed")
        result.status = "error"
        result.error = str(e)


@router.post("/scan", response_model=ScanCreated, status_code=202)
async def create_scan(req: ScanRequest, bg: BackgroundTasks) -> ScanCreated:
    scan_id = uuid.uuid4().hex
    _STORE[scan_id] = ScanResult(scan_id=scan_id, status="pending", url=str(req.url))
    bg.add_task(_run_scan, scan_id, str(req.url), req.max_pages)
    return ScanCreated(scan_id=scan_id)


@router.get("/scan/{scan_id}", response_model=ScanResult)
async def get_scan(scan_id: str) -> ScanResult:
    result = _STORE.get(scan_id)
    if not result:
        raise HTTPException(status_code=404, detail="scan not found")
    return result
