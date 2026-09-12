"""Внутренние ручки для cron: мониторинг, письма-догонялки, проверка канала.

Пропуск — заголовок X-Internal-Key. Незаданный `internal_key` закрывает все
ручки (см. web/security.secret_matches): забытая переменная окружения не
должна открывать их всему интернету.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from lawcheck.config import settings
from lawcheck.db import repo
from lawcheck.notify import telegram
from lawcheck.reporting import followup
from lawcheck.web import security
from lawcheck.web.scanning import start_scan

log = logging.getLogger(__name__)

router = APIRouter()


def _internal_key_ok(request: Request) -> bool:
    """Пропуск к внутренним ручкам (их дёргает cron с X-Internal-Key)."""
    return security.secret_matches(request.headers.get("X-Internal-Key") or "",
                                   settings.internal_key)


@router.post("/internal/monitoring/run")
async def monitoring_run(request: Request, bg: BackgroundTasks):
    """Еженедельный мониторинг: вызывается cron'ом с X-Internal-Key.

    Для каждого оплаченного заказа с подключённым сайтом запускает новый скан,
    если последнему больше 6 дней.
    """
    if not _internal_key_ok(request):
        raise HTTPException(status_code=403, detail="forbidden")
    started = []
    orders = await asyncio.to_thread(repo.list_monitored_orders)
    for order in orders:
        last = await asyncio.to_thread(repo.latest_scan_for_url, order.monitored_url)
        if last is not None:
            age = datetime.now(timezone.utc) - last.created_at
            if age < timedelta(days=6) or last.status in ("pending", "running"):
                continue
        # Та же постановка в очередь, что и у остальных входов, — см. web/scanning.
        scan_id = await asyncio.to_thread(start_scan, bg, order.monitored_url)
        started.append({"order": order.id[:8], "url": order.monitored_url, "scan": scan_id})
        log.info("monitoring: запущен скан %s для %s", scan_id[:8], order.monitored_url)
    return {"monitored": len(orders), "started": started}


@router.post("/internal/followups/run")
async def followups_run(request: Request, limit: int = 20, dry_run: bool = False):
    """Письма-догонялки лидам: вызывается cron'ом раз в сутки с X-Internal-Key.

    Отбор и текст — reporting/followup.py. `limit` бережёт репутацию домена:
    лучше слать понемногу, чем залпом с молодого домена.
    """
    if not _internal_key_ok(request):
        raise HTTPException(status_code=403, detail="forbidden")
    summary = await asyncio.to_thread(followup.run, limit, 20, 14, dry_run)
    log.info("followups: %s", summary)
    return summary


@router.post("/internal/health/telegram")
async def telegram_health(request: Request):
    """Жив ли канал уведомлений: вызывается cron'ом раз в сутки.

    Канал уже дважды умирал молча. Сам по себе он ничего о себе не сообщает:
    ошибка отправки глушится в лог, а событий, на которых это вскрылось бы,
    может не быть неделями — к моменту первой настоящей оплаты сторож обязан
    быть живым.

    Если канал не отвечает, алерт уходит через notify_owner: Telegram там не
    получится, и сработает почтовый дубль. В сообщение кладём список адресов,
    которые сейчас отвечают, — чинится это правкой пина в extra_hosts, и без
    подсказки пришлось бы перебирать адреса руками.
    """
    if not _internal_key_ok(request):
        raise HTTPException(status_code=403, detail="forbidden")
    ok, detail = await asyncio.to_thread(telegram.check_api)
    if ok:
        log.info("health/telegram: канал жив (%s)", detail)
        return {"ok": True, "bot": detail}
    alive = await asyncio.to_thread(telegram.reachable_ips)
    log.error("health/telegram: канал не отвечает — %s; живые адреса: %s",
              detail, alive or "ни одного")
    await asyncio.to_thread(
        telegram.notify_owner,
        f"🔴 Канал уведомлений не отвечает: {telegram.esc(detail)}\n"
        f"Отвечают на 443: {telegram.esc(', '.join(alive) or 'ни один из известных')}.\n"
        f"Чинится пином в extra_hosts (api и worker) в docker-compose.yml.")
    return {"ok": False, "detail": detail, "reachable_ips": alive}
