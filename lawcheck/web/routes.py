"""Web-UI: главная с формой, страница ожидания, отчёт и оплата."""
import asyncio
import logging
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from lawcheck.api.routes.scan import _run_scan
from lawcheck.config import settings
from lawcheck.db import repo
from lawcheck.payments import tochka
from lawcheck.notify import telegram
from lawcheck.reporting import fines, followup, policy_draft
from lawcheck.web import auth, blog, deps, landings, ownership
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
templates.env.globals["metrika_id"] = settings.metrika_id
templates.env.globals["site_base_url"] = settings.site_base_url.rstrip("/")

# Блог и нишевые посадочные используют тот же экземпляр templates (общие globals)
# и подключаются как под-роутеры — только когда SEO-контент готов к публикации.
blog.templates = templates
landings.templates = templates
templates.env.globals["seo_enabled"] = settings.seo_enabled
if settings.seo_enabled:
    router.include_router(blog.router)
    router.include_router(landings.router)

# Аккаунты (регистрация/вход/выход). Сессии всегда включены (SessionMiddleware
# ставится в create_app с секретом из .env или эфемерным в dev).
auth.templates = templates
templates.env.globals["accounts_enabled"] = True
templates.env.globals["session_email"] = deps.session_email  # для навигации в шаблонах
templates.env.globals["session_unverified"] = deps.session_unverified  # баннер «подтвердите email»
router.include_router(auth.router)


# Связка «находка → готовый текст»: с какого раздела шаблонов (pro_templates.html)
# брать болванку под эту находку. Ключ — префикс check_id (часть до "."),
# значение — (id-якорь раздела, подпись). None — для находки готового текста нет.
_TEMPLATE_FIX = {
    "A1": ("tpl-policy", "Политика обработки ПДн"),
    "A2": ("tpl-policy", "Политика обработки ПДн"),
    "A3": ("tpl-policy", "Политика обработки ПДн"),
    "B2": ("tpl-consent", "Согласие для форм"),
    "C2": ("tpl-rkn", "Уведомление в РКН"),
    "D1": ("tpl-rkn", "Уведомление в РКН (трансграничная передача)"),
}


def _fix_template(check_id: str):
    return _TEMPLATE_FIX.get(check_id.split(".")[0])


templates.env.globals["fix_template"] = _fix_template


# === Главная: форма + список последних сканов ===

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    raw_recent = await asyncio.to_thread(repo.list_recent_scans, 50)
    # Анти-соцдоказательство: один и тот же домен 10 раз подряд выглядит как
    # «сервисом пользуется только владелец». Дедуплицируем по домену и
    # показываем блок только при достаточном разнообразии.
    seen: set[str] = set()
    recent = []
    for s in raw_recent:
        domain = urlparse(s.url).netloc.lower().removeprefix("www.")
        if domain in seen:
            continue
        seen.add(domain)
        recent.append(s)
        if len(recent) >= 10:
            break
    if len(recent) < 5:
        recent = []
    return templates.TemplateResponse(request, "index.html", {"recent": recent})


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {})


@router.post("/inquiry")
async def inquiry(request: Request, bg: BackgroundTasks,
                  message: str = Form(...), contact: str = Form(""),
                  page: str = Form(""), website: str = Form("")):
    """Вопрос из чат-виджета. Сохраняем + мгновенный алерт владельцу в Telegram."""
    if website:  # honeypot: бот заполнил скрытое поле — тихо игнорируем
        return {"ok": True}
    message = message.strip()
    contact = contact.strip()
    if len(message) < 2:
        raise HTTPException(status_code=422, detail="empty message")
    inq_id = await asyncio.to_thread(repo.create_inquiry, message, contact, page)
    log.info("inquiry #%s: %.60s | контакт: %s", inq_id, message, contact or "—")
    bg.add_task(
        telegram.notify_owner,
        f"💬 Вопрос с сайта #{inq_id}\n{message[:1500]}\n\n"
        f"Контакт: <b>{contact or 'не оставлен'}</b>"
        + (f"\nСтраница: {page}" if page else ""),
    )
    return {"ok": True}


@router.get("/oferta", response_class=HTMLResponse)
async def oferta(request: Request):
    return templates.TemplateResponse(request, "oferta.html", {})


@router.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request):
    """Входящие: вопросы чат-виджета + email-лиды. Защита — basic_auth на Caddy."""
    inquiries = await asyncio.to_thread(repo.list_inquiries, 200)
    leads = await asyncio.to_thread(repo.list_leads, 200)
    return templates.TemplateResponse(request, "inbox.html", {
        "inquiries": inquiries, "leads": leads,
    })


# === SEO: sitemap.xml + robots.txt ===

@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> str:
    base = settings.site_base_url.rstrip("/")
    return f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"


@router.get("/sitemap.xml")
async def sitemap() -> Response:
    base = settings.site_base_url.rstrip("/")
    # (path, lastmod|None)
    entries: list[tuple[str, str | None]] = [
        ("/", None), ("/pricing", None), ("/privacy", None), ("/oferta", None),
    ]
    if settings.seo_enabled:
        entries.append(("/blog", None))
        for a in blog.list_articles():
            lastmod = a.date.isoformat() if a.date and a.date.year > 1 else None
            entries.append((f"/blog/{a.slug}", lastmod))
        entries += [(f"/proverka/{niche}", None) for niche in landings.LANDINGS]
    items = "".join(
        f"<url><loc>{base}{path}</loc>" + (f"<lastmod>{lm}</lastmod>" if lm else "") + "</url>"
        for path, lm in entries
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'
    return Response(content=xml, media_type="application/xml")


# === Оплата: Pro через эквайринг Точки ===

_PLANS = {"pro": ("LawCheck Pro, 1 месяц", 990)}


@router.post("/buy/{plan}", response_class=HTMLResponse)
async def buy(request: Request, plan: str, bg: BackgroundTasks, email: str = Form(...),
              scan_id: str = Form("")):
    if plan not in _PLANS:
        raise HTTPException(status_code=404, detail="unknown plan")
    purpose, amount = _PLANS[plan]

    # Email — единственная связь с покупателем: без него оплаченный заказ
    # анонимен, а клиент, потерявший ссылку на кабинет, теряет доступ.
    email = email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="invalid email")

    if not tochka.is_configured():
        # Эквайринг ещё не активирован в ЛК банка — принимаем заявку на email.
        bg.add_task(telegram.notify_owner,
                    f"🔔 Клик «Оплатить {plan.capitalize()}» ({amount} ₽) от <b>{email}</b>. "
                    f"Касса в fallback — возможно, придёт заявка на {OPERATOR['email']}.")
        return templates.TemplateResponse(request, "pay_fallback.html", {"plan": plan, "amount": amount})

    order_id = uuid.uuid4().hex
    await asyncio.to_thread(repo.create_order, order_id, plan, amount, email, scan_id.strip())
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
async def pay_success(request: Request, bg: BackgroundTasks, order: str = ""):
    paid = False
    o = await asyncio.to_thread(repo.get_order, order) if order else None
    if o and o.operation_id:
        # Не верим redirect'у: подтверждаем оплату запросом к API банка.
        paid = await asyncio.to_thread(tochka.is_paid, o.operation_id)
        if paid and await asyncio.to_thread(repo.mark_order_paid, order):
            bg.add_task(telegram.notify_owner,
                        f"💰 Оплачен заказ <b>{o.id[:8]}</b> — {o.plan.capitalize()} {o.amount} ₽.\n"
                        f"Покупатель: <b>{o.email or 'email не указан'}</b>")
    tg_deeplink = ""
    if o and settings.telegram_bot_username:
        tg_deeplink = f"https://t.me/{settings.telegram_bot_username}?start={o.id}"
    return templates.TemplateResponse(request, "pay_result.html",
                                      {"ok": paid, "order": o, "tg_deeplink": tg_deeplink})


@router.get("/pay/fail", response_class=HTMLResponse)
async def pay_fail(request: Request, order: str = ""):
    o = await asyncio.to_thread(repo.get_order, order) if order else None
    return templates.TemplateResponse(request, "pay_result.html", {"ok": False, "order": o})


# === Личный кабинет заказа (активация Pro после оплаты) ===

def _scan_diff(prev, last) -> dict:
    """Что изменилось между двумя сканами одного сайта.

    Ключ находки — (check_id, location): новые проблемы, исправленные, без изменений.
    """
    def problems(scan):
        return {(f.check_id, f.location): f for f in scan.findings if f.severity != "ok"}
    p_prev, p_last = problems(prev), problems(last)
    new = [p_last[k] for k in p_last.keys() - p_prev.keys()]
    fixed = [p_prev[k] for k in p_prev.keys() - p_last.keys()]
    order = {"critical": 0, "warning": 1, "info": 2}
    new.sort(key=lambda f: (order.get(f.severity, 9), f.check_id))
    fixed.sort(key=lambda f: (order.get(f.severity, 9), f.check_id))
    return {"new": new, "fixed": fixed, "same": len(p_last.keys() & p_prev.keys()),
            "prev": prev, "last": last}


def _start_scan(bg: BackgroundTasks, url: str, max_pages: int = 25) -> str:
    """Ставит скан в очередь (RQ) либо в BackgroundTasks (dev). Возвращает scan_id."""
    scan_id = uuid.uuid4().hex
    repo.create_scan(scan_id, url, max_pages)
    queue = get_queue()
    if queue is not None:
        queue.enqueue("lawcheck.workers.scan_worker.run_scan",
                      scan_id, url, max_pages, job_timeout=600)
    else:
        bg.add_task(_run_scan, scan_id, url, max_pages)
    return scan_id


@router.get("/account/{order_id}", response_class=HTMLResponse)
async def account(request: Request, order_id: str, attached: int = 0,
                  verified: int = 0, vfail: int = 0):
    order = await asyncio.to_thread(repo.get_order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    scans, diff = [], None
    if order.monitored_url and order.verified_at:
        scans = await asyncio.to_thread(repo.list_done_scans_for_url, order.monitored_url, 5)
        if len(scans) >= 2:
            diff = _scan_diff(scans[1], scans[0])
    token = order.verify_token
    if order.status == "paid" and order.monitored_url and not order.verified_at and not token:
        token = await asyncio.to_thread(repo.ensure_verify_token, order_id, ownership.new_token())
    tg_deeplink = ""
    if settings.telegram_bot_username and order.monitored_url and order.verified_at:
        tg_deeplink = f"https://t.me/{settings.telegram_bot_username}?start={order.id}"
    return templates.TemplateResponse(request, "account.html", {
        "order": order, "scans": scans, "diff": diff,
        "attached": bool(attached), "verified": bool(verified), "vfail": bool(vfail),
        "verify_token": token,
        "monitored_domain": ownership.registered_domain(order.monitored_url) if order.monitored_url else "",
        "tg_deeplink": tg_deeplink,
    })


@router.post("/account/{order_id}/monitor", response_class=HTMLResponse)
async def account_monitor(request: Request, order_id: str, url: str = Form(...)):
    order = await asyncio.to_thread(repo.get_order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if order.status != "paid":
        raise HTTPException(status_code=403, detail="order not paid")
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    await asyncio.to_thread(repo.set_monitored_url, order_id, url)
    # Смена сайта сбрасывает подтверждение и выдаёт новый токен — мониторинг
    # не должен достаться вместе со старым подтверждением другому домену.
    await asyncio.to_thread(repo.reset_verification, order_id)
    await asyncio.to_thread(repo.ensure_verify_token, order_id, ownership.new_token())
    return RedirectResponse(url=f"/account/{order_id}?attached=1", status_code=303)


@router.post("/account/{order_id}/verify", response_class=HTMLResponse)
async def account_verify(request: Request, order_id: str, bg: BackgroundTasks):
    order = await asyncio.to_thread(repo.get_order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if order.status != "paid" or not order.monitored_url or not order.verify_token:
        raise HTTPException(status_code=403, detail="nothing to verify")
    method = await asyncio.to_thread(
        ownership.check_ownership, order.monitored_url, order.verify_token)
    if not method:
        return RedirectResponse(url=f"/account/{order_id}?vfail=1", status_code=303)
    await asyncio.to_thread(repo.mark_verified, order_id)
    log.info("ownership: заказ %s подтвердил %s через %s",
             order_id[:8], order.monitored_url, method)
    # Подтверждено — запускаем первый скан мониторинга, если истории ещё нет.
    if await asyncio.to_thread(repo.latest_scan_for_url, order.monitored_url) is None:
        await asyncio.to_thread(_start_scan, bg, order.monitored_url)
    return RedirectResponse(url=f"/account/{order_id}?verified=1", status_code=303)


@router.get("/account/{order_id}/templates", response_class=HTMLResponse)
async def account_templates(request: Request, order_id: str):
    order = await asyncio.to_thread(repo.get_order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if order.status != "paid":
        # Шаблоны — платный контент.
        return RedirectResponse(url=f"/account/{order_id}", status_code=303)
    return templates.TemplateResponse(request, "pro_templates.html", {"order": order})


@router.post("/internal/monitoring/run")
async def monitoring_run(request: Request, bg: BackgroundTasks):
    """Еженедельный мониторинг: вызывается cron'ом с X-Internal-Key.

    Для каждого оплаченного заказа с подключённым сайтом запускает новый скан,
    если последнему больше 6 дней.
    """
    if not settings.internal_key or request.headers.get("X-Internal-Key") != settings.internal_key:
        raise HTTPException(status_code=403, detail="forbidden")
    from datetime import datetime, timedelta, timezone
    started = []
    orders = await asyncio.to_thread(repo.list_monitored_orders)
    for order in orders:
        last = await asyncio.to_thread(repo.latest_scan_for_url, order.monitored_url)
        if last is not None:
            age = datetime.now(timezone.utc) - last.created_at
            if age < timedelta(days=6) or last.status in ("pending", "running"):
                continue
        scan_id = uuid.uuid4().hex
        await asyncio.to_thread(repo.create_scan, scan_id, order.monitored_url, 25)
        queue = get_queue()
        if queue is not None:
            queue.enqueue("lawcheck.workers.scan_worker.run_scan",
                          scan_id, order.monitored_url, 25, job_timeout=600)
        else:
            bg.add_task(_run_scan, scan_id, order.monitored_url, 25)
        started.append({"order": order.id[:8], "url": order.monitored_url, "scan": scan_id})
        log.info("monitoring: запущен скан %s для %s", scan_id[:8], order.monitored_url)
    return {"monitored": len(orders), "started": started}


@router.post("/internal/followups/run")
async def followups_run(request: Request, limit: int = 20, dry_run: bool = False):
    """Письма-догонялки лидам: вызывается cron'ом раз в сутки с X-Internal-Key.

    Отбор и текст — reporting/followup.py. `limit` бережёт репутацию домена:
    лучше слать понемногу, чем залпом с молодого домена.
    """
    if not settings.internal_key or request.headers.get("X-Internal-Key") != settings.internal_key:
        raise HTTPException(status_code=403, detail="forbidden")
    summary = await asyncio.to_thread(followup.run, limit, 24, 14, dry_run)
    log.info("followups: %s", summary)
    return summary


@router.get("/webhooks/tochka")
async def tochka_webhook_probe():
    """Точка при регистрации вебхука проверяет доступность URL (в т.ч. GET) —
    отвечаем 200, иначе «Failed to test webhook url accessibility»."""
    return {"ok": True}


@router.post("/webhooks/telegram")
async def telegram_webhook(request: Request):
    """Апдейты бота. Нужны только для deep-link подключения мониторинга:
    клиент жмёт Start по ссылке t.me/bot?start=<order_id> → привязываем его чат."""
    if (settings.telegram_webhook_secret
            and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != settings.telegram_webhook_secret):
        raise HTTPException(status_code=403, detail="bad secret")
    try:
        upd = await request.json()
    except Exception:
        return {"ok": True}
    msg = upd.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id") or "")
    if not chat_id or not text.startswith("/start"):
        return {"ok": True}
    parts = text.split(maxsplit=1)
    order_id = parts[1].strip() if len(parts) > 1 else ""
    if order_id:
        order = await asyncio.to_thread(repo.set_client_chat_id, order_id, chat_id)
        if order:
            lines = [f"✅ Доступ к заказу <b>{order.id[:8]}</b> сохранён.",
                     f"Личный кабинет: {settings.site_base_url}/account/{order.id}",
                     "(сохраните это сообщение — здесь ваша постоянная ссылка)"]
            if order.monitored_url:
                lines.append(f"\nБуду присылать сюда изменения по сайту "
                             f"<b>{order.monitored_url}</b> после еженедельных проверок.")
            await asyncio.to_thread(telegram.send_message, chat_id, "\n".join(lines))
        else:
            await asyncio.to_thread(
                telegram.send_message, chat_id,
                "Не нашёл заказ. Откройте ссылку из кабинета ещё раз.")
    else:
        await asyncio.to_thread(
            telegram.send_message, chat_id,
            "Это бот уведомлений LawCheck. Подключите его кнопкой в кабинете заказа.")
    return {"ok": True}


@router.post("/webhooks/tochka")
async def tochka_webhook(request: Request, bg: BackgroundTasks):
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
            if await asyncio.to_thread(repo.mark_order_paid, order.id):
                bg.add_task(telegram.notify_owner,
                            f"💰 Оплачен заказ <b>{order.id[:8]}</b> — "
                            f"{order.plan.capitalize()} {order.amount} ₽.\n"
                            f"Покупатель: <b>{order.email or 'email не указан'}</b>")
                log.info("заказ %s оплачен (операция %s)", order.id, operation_id)
    return {"ok": True}


@router.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request, scan: str = ""):
    recent = await asyncio.to_thread(repo.list_recent_scans, 10)
    example = next((s for s in recent if s.status == "done"), None)
    # scan прилетает с CTA отчёта («Открыть исправления») — привяжем к нему покупку,
    # чтобы после оплаты открыть рецепты именно на этом отчёте.
    return templates.TemplateResponse(request, "pricing.html",
                                      {"example": example, "scan_id": scan.strip()})


# === POST формы — создаёт скан, редиректит на /report/{id} ===

@router.post("/scan", response_class=HTMLResponse)
async def create_scan_form(request: Request, bg: BackgroundTasks, url: str = Form(...),
                           max_pages: int = Form(10)):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    scan_id = uuid.uuid4().hex
    await asyncio.to_thread(repo.create_scan, scan_id, url, max_pages)
    # Залогинен — привяжем скан к аккаунту, чтобы он попал в «Мои отчёты».
    uid = deps.session_uid(request)
    if uid:
        await asyncio.to_thread(repo.set_scan_user, scan_id, uid)

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


async def _unlock_order_id(request: Request, scan) -> str | None:
    """id оплаченного заказа, дающего полный доступ к отчёту этого скана:
    разовая покупка с него ИЛИ Pro-подписка залогиненного владельца скана."""
    oid = await asyncio.to_thread(repo.paid_order_id_for_scan, scan.id)
    if not oid:
        user = await deps.current_user(request)
        if user is not None and scan.user_id == user.id:
            oid = await asyncio.to_thread(repo.latest_paid_order_id_for_user, user.id)
    return oid


@router.get("/report/{scan_id}/documents", response_class=HTMLResponse)
async def report_documents(request: Request, scan_id: str):
    """Авто-черновик Политики ПДн + текста согласия под конкретный сайт (Pro)."""
    scan = await asyncio.to_thread(repo.get_scan, scan_id)
    if scan is None or scan.status != "done":
        raise HTTPException(status_code=404, detail="scan not found")
    if not await _unlock_order_id(request, scan):
        return RedirectResponse(url=f"/pricing?scan={scan_id}", status_code=303)
    html = await asyncio.to_thread(policy_draft.render, scan)
    return HTMLResponse(content=html)


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
    # Оплаченный заказ с этим scan_id снимает замок со всех рецептов.
    all_problems = sorted(
        (f for f in scan.findings if f.severity != "ok" and f.recommendation),
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.check_id),
    )
    # Разблокировка «Как исправить»: (1) разовая покупка с этого отчёта, либо
    # (2) Pro-подписка — залогиненный ВЛАДЕЛЕЦ скана с оплаченным заказом видит
    # свои отчёты открытыми целиком (чужие сканы так не открываются).
    unlock_order_id = await _unlock_order_id(request, scan)
    unlocked = bool(unlock_order_id)
    cabinet_href = f"/account/{unlock_order_id}" if unlock_order_id else "/dashboard"
    # База для ссылок на готовый текст в шаблонах (доступна при оплаченном заказе).
    templates_href = f"/account/{unlock_order_id}/templates" if unlock_order_id else ""
    if unlocked:
        open_rec_ids = {f.id for f in all_problems}
        locked_count = 0
    else:
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
        "unlocked": unlocked,
        "cabinet_href": cabinet_href,
        "templates_href": templates_href,
        "subscribed": bool(sub),
    })


@router.post("/report/{scan_id}/subscribe", response_class=HTMLResponse)
async def report_subscribe(request: Request, scan_id: str, bg: BackgroundTasks,
                           email: str = Form(...)):
    scan = await asyncio.to_thread(repo.get_scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")
    email = email.strip().lower()
    if "@" in email and "." in email.split("@")[-1]:
        if await asyncio.to_thread(repo.create_lead, scan_id, scan.url, email):
            log.info("lead: %s (скан %s, %s)", email, scan_id[:8], scan.url)
            bg.add_task(telegram.notify_owner,
                        f"📩 Новый лид: <b>{email}</b>\nсайт: {scan.url}\n"
                        f"отчёт: {settings.site_base_url}/report/{scan_id}")
    return RedirectResponse(url=f"/report/{scan_id}?sub=1", status_code=303)


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
async def unsubscribe(request: Request, token: str):
    """Отписка от писем-догонялок по токену из футера письма (ст. 18 ФЗ «О рекламе»)."""
    email = await asyncio.to_thread(repo.unsubscribe_lead, token)
    if email:
        title = "Вы отписаны"
        message = (f"Больше не будем писать на {email}. "
                   "Если передумаете — просто запустите проверку сайта заново.")
    else:
        title = "Ссылка недействительна"
        message = "Не нашли подписку по этой ссылке — возможно, вы уже отписались."
    return templates.TemplateResponse(request, "message.html", {
        "title": title, "message": message,
        "cta_href": "/", "cta_label": "На главную",
    })
