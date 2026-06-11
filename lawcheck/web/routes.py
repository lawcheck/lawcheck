"""Web-UI: главная с формой, страница ожидания, отчёт и оплата."""
import asyncio
import logging
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lawcheck.api.routes.scan import _run_scan
from lawcheck.db import repo
from lawcheck.payments import tochka
from lawcheck.reporting import fines
from lawcheck.workers.queue import get_queue

log = logging.getLogger(__name__)

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _money(value: int) -> str:
    """1234567 -> '1 234 567' (неразрывные пробелы между разрядами)."""
    return f"{int(value):,}".replace(",", " ")


templates.env.filters["money"] = _money
templates.env.globals["fine_group"] = fines.group_for  # вызывается внутри Jinja-макроса

# Реквизиты оператора сервиса — единый источник для подвала и /privacy.
# None = реквизит ещё не указан, шаблоны не выводят пустые поля.
OPERATOR = {
    "name": "ИП Подольский Максим Александрович",
    "short_name": "ИП Подольский М.А.",
    "inn": "771481979800",
    "ogrnip": "322774600250213",
    "email": "juristlawer@gmail.com",
    "policy_date": "10 июня 2026 г.",
}
templates.env.globals["operator"] = OPERATOR


# === Главная: форма + список последних сканов ===

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    recent = await asyncio.to_thread(repo.list_recent_scans, 10)
    return templates.TemplateResponse(request, "index.html", {"recent": recent})


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {})


# === Оплата: Pro через эквайринг Точки ===

_PLANS = {"pro": ("LawCheck Pro, 1 месяц", 990)}


@router.post("/buy/{plan}", response_class=HTMLResponse)
async def buy(request: Request, plan: str):
    if plan not in _PLANS:
        raise HTTPException(status_code=404, detail="unknown plan")
    purpose, amount = _PLANS[plan]

    if not tochka.is_configured():
        # Эквайринг ещё не активирован в ЛК банка — принимаем заявку на email.
        return templates.TemplateResponse(request, "pay_fallback.html", {"plan": plan, "amount": amount})

    order_id = uuid.uuid4().hex
    await asyncio.to_thread(repo.create_order, order_id, plan, amount)
    try:
        link = await asyncio.to_thread(
            tochka.create_payment,
            amount_rub=amount, purpose=f"{purpose} (заказ {order_id[:8]})", order_id=order_id,
        )
    except Exception:
        log.exception("tochka: не удалось создать платёжную ссылку")
        return templates.TemplateResponse(request, "pay_fallback.html", {"plan": plan, "amount": amount})
    await asyncio.to_thread(repo.set_order_payment, order_id, link.operation_id, link.url)
    return RedirectResponse(url=link.url, status_code=303)


@router.get("/pay/success", response_class=HTMLResponse)
async def pay_success(request: Request, order: str = ""):
    paid = False
    o = await asyncio.to_thread(repo.get_order, order) if order else None
    if o and o.operation_id:
        # Не верим redirect'у: подтверждаем оплату запросом к API банка.
        paid = await asyncio.to_thread(tochka.is_paid, o.operation_id)
        if paid:
            await asyncio.to_thread(repo.mark_order_paid, order)
    return templates.TemplateResponse(request, "pay_result.html", {"ok": paid, "order": o})


@router.get("/pay/fail", response_class=HTMLResponse)
async def pay_fail(request: Request, order: str = ""):
    o = await asyncio.to_thread(repo.get_order, order) if order else None
    return templates.TemplateResponse(request, "pay_result.html", {"ok": False, "order": o})


@router.post("/webhooks/tochka")
async def tochka_webhook(request: Request):
    """Вебхук acquiringInternetPayment. Тело — JWT; используем его только как
    триггер: вытаскиваем operationId без проверки подписи и перепроверяем
    статус авторизованным запросом к API банка."""
    raw = (await request.body()).decode("utf-8", errors="replace").strip()
    operation_id = ""
    try:
        import base64
        import json
        payload_b64 = raw.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
        operation_id = payload.get("operationId") or payload.get("Data", {}).get("operationId", "")
    except Exception:
        log.warning("tochka webhook: не удалось разобрать тело: %.200s", raw)
    if operation_id:
        order = await asyncio.to_thread(repo.get_order_by_operation, operation_id)
        if order and await asyncio.to_thread(tochka.is_paid, operation_id):
            await asyncio.to_thread(repo.mark_order_paid, order.id)
            log.info("заказ %s оплачен (операция %s)", order.id, operation_id)
    return {"ok": True}


@router.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    recent = await asyncio.to_thread(repo.list_recent_scans, 10)
    example = next((s for s in recent if s.status == "done"), None)
    return templates.TemplateResponse(request, "pricing.html", {"example": example})


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

# Секции отчёта в порядке отображения: (slug-якорь, заголовок, [префиксы check_id])
_BLOCK_DEFS = [
    ("pdn", "Политика обработки ПДн", ["A1", "A2", "A3"]),
    ("forms", "Формы и согласия", ["B1", "B2"]),
    ("cookies", "Cookies и трекеры", ["D1", "D2"]),
    ("owner", "Реквизиты владельца", ["E1", "E2"]),
    ("rkn", "Реестр операторов РКН", ["C2"]),
    ("zozpp", "ЗОЗПП и Правила продажи", ["F1", "F2", "F3"]),
    ("ads", "ФЗ «О рекламе»", ["G1", "G2", "G3"]),
    ("kids", "Защита детей (436-ФЗ)", ["H1"]),
]


# Сколько рекомендаций «Как исправить» открыто в бесплатном отчёте.
# Диагноз (что сломано, цитата, штраф) открыт всегда; рецепты сверх лимита — в Pro.
_FREE_RECIPES = 2


@router.get("/report/{scan_id}", response_class=HTMLResponse)
async def report(request: Request, scan_id: str, sub: int = 0):
    scan = await asyncio.to_thread(repo.get_scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")

    by_prefix: dict[str, list] = defaultdict(list)
    counts = {"critical": 0, "warning": 0, "info": 0, "ok": 0}
    for f in scan.findings:
        by_prefix[f.check_id.split(".")[0]].append(f)
        counts[f.severity] = counts.get(f.severity, 0) + 1

    blocks = []
    for slug, title, prefixes in _BLOCK_DEFS:
        items = [f for p in prefixes for f in by_prefix.get(p, [])]
        if not items:
            continue
        items.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.check_id))
        problems = [f for f in items if f.severity != "ok"]
        oks = [f for f in items if f.severity == "ok"]
        worst = problems[0].severity if problems else "ok"
        blocks.append({
            "slug": slug, "title": title,
            "problems": problems, "oks": oks, "worst": worst,
        })

    total = sum(counts.values())
    compliance = round(counts["ok"] / total * 100) if total else 0

    # Gate рецептов: открываем «Как исправить» у первых N самых тяжёлых находок.
    all_problems = sorted(
        (f for f in scan.findings if f.severity != "ok" and f.recommendation),
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.check_id),
    )
    open_rec_ids = {f.id for f in all_problems[:_FREE_RECIPES]}
    locked_count = max(0, len(all_problems) - _FREE_RECIPES)

    return templates.TemplateResponse(request, "report.html", {
        "scan": scan,
        "blocks": blocks,
        "counts": counts,
        "compliance": compliance,
        "risk": fines.risk_total(scan.findings),
        "is_https": scan.url.startswith("https://"),
        "is_active": scan.status in ("pending", "running"),
        "open_rec_ids": open_rec_ids,
        "locked_count": locked_count,
        "subscribed": bool(sub),
    })


@router.post("/report/{scan_id}/subscribe", response_class=HTMLResponse)
async def report_subscribe(request: Request, scan_id: str, email: str = Form(...)):
    scan = await asyncio.to_thread(repo.get_scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")
    email = email.strip().lower()
    if "@" in email and "." in email.split("@")[-1]:
        await asyncio.to_thread(repo.create_lead, scan_id, scan.url, email)
        log.info("lead: %s (скан %s, %s)", email, scan_id[:8], scan.url)
    return RedirectResponse(url=f"/report/{scan_id}?sub=1", status_code=303)
