"""Личный кабинет заказа: мониторинг сайта, подтверждение владения, шаблоны.

Доступ — капабилити: кто знает `order_id`, тот и владелец. Поэтому страницы
помечены `_private_page` (no-referrer + noindex), а Метрика на них выключена
в шаблоне: она шлёт путь страницы, то есть сам пропуск.

Под-роутер по образцу web/blog.py: `templates` проставляется из web/routes.py.
"""
import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from lawcheck.config import settings
from lawcheck.db import repo
from lawcheck.web import ownership
from lawcheck.web.scanning import start_scan

log = logging.getLogger(__name__)

router = APIRouter()
templates: Jinja2Templates = None  # type: ignore[assignment]  # задаётся из routes.py


def private_page(response: Response) -> Response:
    """Пометить страницу, чей URL сам по себе является пропуском.

    `/account/{order_id}` — магик-ссылка: кто знает id, тот и владелец заказа.
    Такой URL нельзя отдавать третьим сторонам, поэтому: no-referrer (не утечёт
    при переходе по внешней ссылке), noindex (не попадёт в поиск) и отключённая
    Метрика в шаблоне (она шлёт путь текущей страницы, и Referrer-Policy её
    не останавливает — это отдельный канал утечки).
    """
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


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
    active = repo.subscription_active(order)
    if active and order.monitored_url and not order.verified_at and not token:
        token = await asyncio.to_thread(repo.ensure_verify_token, order_id, ownership.new_token())
    tg_deeplink = ""
    if settings.telegram_bot_username and order.monitored_url and order.verified_at:
        tg_deeplink = f"https://t.me/{settings.telegram_bot_username}?start={order.id}"
    return private_page(templates.TemplateResponse(request, "account.html", {
        "order": order, "scans": scans, "diff": diff,
        "active": active, "no_analytics": True,
        "attached": bool(attached), "verified": bool(verified), "vfail": bool(vfail),
        "verify_token": token,
        "monitored_domain": ownership.registered_domain(order.monitored_url) if order.monitored_url else "",
        "tg_deeplink": tg_deeplink,
    }))


@router.post("/account/{order_id}/monitor", response_class=HTMLResponse)
async def account_monitor(request: Request, order_id: str, url: str = Form(...)):
    order = await asyncio.to_thread(repo.get_order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if not repo.subscription_active(order):
        raise HTTPException(status_code=403, detail="subscription inactive")
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
    if not repo.subscription_active(order) or not order.monitored_url or not order.verify_token:
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
        await asyncio.to_thread(start_scan, bg, order.monitored_url)
    return RedirectResponse(url=f"/account/{order_id}?verified=1", status_code=303)


@router.get("/account/{order_id}/templates", response_class=HTMLResponse)
async def account_templates(request: Request, order_id: str):
    order = await asyncio.to_thread(repo.get_order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if not repo.subscription_active(order):
        # Шаблоны — платный контент.
        return RedirectResponse(url=f"/account/{order_id}", status_code=303)
    return private_page(templates.TemplateResponse(
        request, "pro_templates.html", {"order": order, "no_analytics": True}))
