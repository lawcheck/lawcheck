"""Оплата тарифа: платёжная ссылка, возврат из банка, вебхук Точки.

Под-роутер по образцу web/blog.py: общий экземпляр `templates` проставляется
из web/routes.py при подключении.
"""
import asyncio
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lawcheck.config import settings
from lawcheck.db import repo
from lawcheck.db.models import Order
from lawcheck.notify import telegram
from lawcheck.payments import tochka
from lawcheck.utils import consent
from lawcheck.utils.email import valid_email
from lawcheck.web import deps, ratelimit
from lawcheck.web.operator import OPERATOR

log = logging.getLogger(__name__)

router = APIRouter()
templates: Jinja2Templates = None  # type: ignore[assignment]  # задаётся из routes.py

# Вебхук банка открыт всем, и каждый POST порождает исходящий запрос к API Точки.
# Настоящий банк столько не шлёт.
_RL_WEBHOOK = ratelimit.Limit(limit=60, window_sec=60)
# Перевыпуск платёжной ссылки: каждый GET создаёт операцию в кассе банка,
# а order_id знает любой, кому пришло письмо-напоминание.
_RL_PAY_RETRY = ratelimit.Limit(limit=10, window_sec=3600)


_PLANS = {"pro": ("LawCheck Pro, 1 месяц", 990)}


def _paid_alert(order: Order) -> str:
    """Текст алерта владельцу об оплате. Источник визита — в том же сообщении:
    иначе он лежит в БД, и вопрос «реклама это или Инстаграм» опять решается
    руками (первый такой разбор шёл грепом по логам Caddy)."""
    parts = [p for p in (order.entry_ref, order.entry_url) if p]
    src = " → ".join(parts) if parts else "прямой заход"
    return (f"💰 Оплачен заказ <b>{order.id[:8]}</b> — "
            f"{order.plan.capitalize()} {order.amount} ₽.\n"
            f"Покупатель: <b>{telegram.esc(order.email) or 'email не указан'}</b>\n"
            f"Источник: {telegram.esc(src)}")


@router.post("/buy/{plan}", response_class=HTMLResponse)
async def buy(request: Request, plan: str, bg: BackgroundTasks, email: str = Form(...),
              scan_id: str = Form(""), pd_consent: str = Form("")):
    if plan not in _PLANS:
        raise HTTPException(status_code=404, detail="unknown plan")
    purpose, amount = _PLANS[plan]

    # Согласие проверяем на сервере, а не только атрибутом `required` в форме:
    # POST приходит и в обход браузера, а без согласия email хранить нельзя
    # (ст. 9 152-ФЗ) — заказ же начинается именно с записи email в orders.
    if not consent.checked(pd_consent):
        raise HTTPException(status_code=422, detail="no pd consent")

    # Email — единственная связь с покупателем: без него оплаченный заказ
    # анонимен, а клиент, потерявший ссылку на кабинет, теряет доступ.
    email = email.strip().lower()
    if not valid_email(email):
        raise HTTPException(status_code=422, detail="invalid email")

    if not tochka.is_configured():
        # Эквайринг ещё не активирован в ЛК банка — принимаем заявку на email.
        bg.add_task(telegram.notify_owner,
                    f"🔔 Клик «Оплатить {plan.capitalize()}» ({amount} ₽) от <b>{telegram.esc(email)}</b>. "
                    f"Касса в fallback — возможно, придёт заявка на {OPERATOR['email']}.")
        return templates.TemplateResponse(request, "pay_fallback.html", {"plan": plan, "amount": amount})

    order_id = uuid.uuid4().hex
    entry_ref, entry_url = deps.entry_source(request)
    await asyncio.to_thread(repo.create_order, order_id, plan, amount, email, scan_id.strip(),
                            entry_ref, entry_url)
    try:
        link = await asyncio.to_thread(
            tochka.create_payment,
            amount_rub=amount, purpose=f"{purpose} (заказ {order_id[:8]})", order_id=order_id,
            email=email,
        )
    except Exception:
        log.exception("tochka: не удалось создать платёжную ссылку")
        return templates.TemplateResponse(request, "pay_fallback.html", {"plan": plan, "amount": amount})
    await asyncio.to_thread(repo.set_order_payment, order_id, link.operation_id, link.url)
    return RedirectResponse(url=link.url, status_code=303)


@router.get("/pay/retry/{order_id}", response_class=HTMLResponse)
async def pay_retry(request: Request, order_id: str):
    """Перевыпуск платёжной ссылки по существующему заказу — цель ссылки из
    письма-напоминания о брошенной оплате.

    Ссылку Точки нельзя просто переслать из `Order.payment_link`: её срок жизни
    задаёт банк, и через неделю-другую она мертва. Поэтому выписываем новую, но
    ПО ТОМУ ЖЕ заказу — иначе каждое напоминание плодило бы дубль в `orders`,
    и отличить отработку письма от свежего спроса стало бы нечем.

    Сумму берём из заказа, а не из прайса: человек платит ту цену, которую
    видел при оформлении, даже если тариф с тех пор подорожал.
    """
    ratelimit.enforce(request, "pay_retry", _RL_PAY_RETRY, extra=order_id,
                      message="Слишком много попыток. Попробуйте позже.")
    order = await asyncio.to_thread(repo.get_order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if order.status == "paid":
        return RedirectResponse(url=f"/account/{order_id}", status_code=303)

    purpose = _PLANS.get(order.plan, (f"LawCheck {order.plan}", order.amount))[0]
    if not tochka.is_configured():
        return templates.TemplateResponse(request, "pay_fallback.html",
                                          {"plan": order.plan, "amount": order.amount})
    try:
        link = await asyncio.to_thread(
            tochka.create_payment,
            amount_rub=order.amount, purpose=f"{purpose} (заказ {order_id[:8]})",
            order_id=order_id, email=order.email,
        )
    except Exception:
        log.exception("tochka: не удалось перевыпустить ссылку по заказу %s", order_id)
        return templates.TemplateResponse(request, "pay_fallback.html",
                                          {"plan": order.plan, "amount": order.amount})
    await asyncio.to_thread(repo.set_order_payment, order_id, link.operation_id, link.url)
    return RedirectResponse(url=link.url, status_code=303)


@router.get("/pay/success", response_class=HTMLResponse)
async def pay_success(request: Request, bg: BackgroundTasks, order: str = ""):
    state = "unknown"
    o = await asyncio.to_thread(repo.get_order, order) if order else None
    if o and o.operation_id:
        # Не верим redirect'у: подтверждаем оплату запросом к API банка.
        state = await asyncio.to_thread(tochka.payment_state, o.operation_id)
        if state == "paid":
            # Покупатель вернулся из банка в своём браузере — единственный момент,
            # когда мы можем связать оплату с этой сессией. Без этого он попадёт
            # на свой же отчёт как посторонний.
            deps.remember_order(request, o.id)
            if await asyncio.to_thread(repo.mark_order_paid, order):
                bg.add_task(telegram.notify_owner, _paid_alert(o))
    tg_deeplink = ""
    if o and settings.telegram_bot_username:
        tg_deeplink = f"https://t.me/{settings.telegram_bot_username}?start={o.id}"
    # pending — деньги захолдированы, но не списаны: доступ не открываем, однако
    # и «оплата не прошла» не пишем. Дожмёт вебхук банка.
    return templates.TemplateResponse(request, "pay_result.html",
                                      {"ok": state == "paid", "pending": state == "pending",
                                       "order": o, "tg_deeplink": tg_deeplink})


@router.get("/pay/fail", response_class=HTMLResponse)
async def pay_fail(request: Request, order: str = ""):
    o = await asyncio.to_thread(repo.get_order, order) if order else None
    return templates.TemplateResponse(request, "pay_result.html", {"ok": False, "order": o})


@router.get("/webhooks/tochka")
async def tochka_webhook_probe():
    """Точка при регистрации вебхука проверяет доступность URL (в т.ч. GET) —
    отвечаем 200, иначе «Failed to test webhook url accessibility»."""
    return {"ok": True}


@router.post("/webhooks/tochka")
async def tochka_webhook(request: Request, bg: BackgroundTasks):
    """Вебхук acquiringInternetPayment. Тело — JWT; используем его только как
    триггер: вытаскиваем operationId без проверки подписи и перепроверяем
    статус авторизованным запросом к API банка."""
    # Эндпоинт открыт всем, а каждый разобранный operationId порождает исходящий
    # запрос к API банка — дешёвый способ нагрузить и нас, и Точку.
    ratelimit.enforce(request, "tochka_webhook", _RL_WEBHOOK)
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
        paid = order is not None and await asyncio.to_thread(tochka.is_paid, operation_id)
        if order is not None and paid:
            if await asyncio.to_thread(repo.mark_order_paid, order.id):
                bg.add_task(telegram.notify_owner, _paid_alert(order))
                log.info("заказ %s оплачен (операция %s)", order.id, operation_id)
    return {"ok": True}
