"""Web-UI: главная с формой, страница ожидания и отчёт."""
import asyncio
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lawcheck.api.routes.scan import _run_scan
from lawcheck.db import repo
from lawcheck.workers.queue import get_queue

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# === Главная: форма + список последних сканов ===

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    recent = await asyncio.to_thread(repo.list_recent_scans, 10)
    return templates.TemplateResponse(request, "index.html", {"recent": recent})


# === POST формы — создаёт скан, редиректит на /report/{id} ===

@router.post("/scan", response_class=HTMLResponse)
async def create_scan_form(request: Request, bg: BackgroundTasks, url: str = Form(...),
                           max_pages: int = Form(10)):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    scan_id = uuid.uuid4().hex
    await asyncio.to_thread(repo.create_scan, scan_id, url, max_pages)

    queue = get_queue()
    if queue is not None:
        queue.enqueue("lawcheck.workers.scan_worker.run_scan",
                      scan_id, url, max_pages, job_timeout=600)
    else:
        bg.add_task(_run_scan, scan_id, url, max_pages)

    return RedirectResponse(url=f"/report/{scan_id}", status_code=303)


# === Страница отчёта (живёт и пока статус pending/running, и потом done) ===

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}


@router.get("/report/{scan_id}", response_class=HTMLResponse)
async def report(request: Request, scan_id: str):
    scan = await asyncio.to_thread(repo.get_scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")

    grouped: dict[str, list] = defaultdict(list)
    counts = {"critical": 0, "warning": 0, "info": 0, "ok": 0}
    for f in scan.findings:
        prefix = f.check_id.split(".")[0]
        grouped[prefix].append(f)
        counts[f.severity] = counts.get(f.severity, 0) + 1

    # сортируем findings внутри группы по severity
    for k in grouped:
        grouped[k].sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.check_id))

    block_titles = {
        "A1": "Политика обработки ПДн",
        "A2": "Политика обработки ПДн",
        "A3": "Политика обработки ПДн",
        "B1": "Формы и согласия",
        "B2": "Формы и согласия",
        "D1": "Cookies и трекеры",
        "D2": "Cookies и трекеры",
        "E1": "Реквизиты владельца",
        "E2": "Реквизиты владельца",
        "C2": "Реестр операторов РКН",
    }

    return templates.TemplateResponse(request, "report.html", {
        "scan": scan,
        "grouped": dict(grouped),
        "counts": counts,
        "block_titles": block_titles,
        "is_active": scan.status in ("pending", "running"),
    })
