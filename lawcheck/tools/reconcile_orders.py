"""Сверка неоплаченных заказов с банком: не потеряли ли мы оплату.

Заказ помечают оплаченным два пути — вебхук банка и возврат покупателя на
`/pay/success`. Оба могут не сработать: вебхук потеряться, а покупатель не
вернуться в браузер вовсе (при оплате через СБП он платит в приложении банка и
вкладку больше не открывает). Тогда деньги у нас, а заказ висит `pending`:
покупатель без доступа, выручка не видна, а письмо-напоминание ещё и просит его
заплатить второй раз.

Отбор: `pending`, есть `operation_id`, возраст до `--max-age-days`. По каждому
спрашиваем банк; статус решает `tochka.payment_state`, здесь его не толкуем.

Планово гоняется сервисом `followups` из docker-compose.yml — раз в час, а не
раз в сутки: деньги ждать сутки нельзя. Повтор безвреден, `mark_order_paid`
переводит статус один раз и только в направлении «не оплачен → оплачен».

Руками (в контейнере api — там БД и креды банка):

    # посмотреть, что нашлось, ничего не меняя
    docker compose exec api python -m lawcheck.tools.reconcile_orders --dry-run

    # проставить оплату найденным и уведомить владельца
    docker compose exec api python -m lawcheck.tools.reconcile_orders
"""
from __future__ import annotations

import argparse
import logging

from lawcheck.db import repo
from lawcheck.db.session import init_db
from lawcheck.notify import telegram
from lawcheck.payments import tochka

log = logging.getLogger(__name__)


def run(*, max_age_days: int = 60, limit: int = 50, dry_run: bool = False) -> dict:
    """Возвращает сводку: сколько проверили, сколько оказалось оплаченными."""
    orders = repo.orders_awaiting_confirmation(max_age_days=max_age_days, limit=limit)
    found = []
    for order in orders:
        if tochka.payment_state(order.operation_id) != "paid":
            continue
        found.append(order)
        if dry_run:
            continue
        # Тот же переход, что делают вебхук и /pay/success, включая разовость
        # уведомления: True возвращается только на переходе «не оплачен → оплачен».
        if repo.mark_order_paid(order.id):
            telegram.notify_owner(telegram.paid_alert(order))
            log.info("заказ %s: банк подтвердил оплату, статус исправлен", order.id)
    return {"checked": len(orders), "found": len(found), "dry_run": dry_run,
            "orders": [(o.id, o.email, o.amount) for o in found]}


def main() -> None:
    ap = argparse.ArgumentParser(description="Сверка неоплаченных заказов с банком")
    ap.add_argument("--limit", type=int, default=50, help="макс. заказов за прогон")
    ap.add_argument("--max-age-days", type=int, default=60,
                    help="не проверять заказы старше N дней")
    ap.add_argument("--dry-run", action="store_true",
                    help="только показать найденное, не менять статусы")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_db()

    summary = run(max_age_days=args.max_age_days, limit=args.limit, dry_run=args.dry_run)
    mode = "DRY-RUN" if summary["dry_run"] else "СВЕРКА"
    print(f"[{mode}] проверено заказов: {summary['checked']}, "
          f"оплачено по данным банка: {summary['found']}")
    for oid, email, amount in summary["orders"]:
        print(f"  {oid[:8]}  {email or 'email не указан':<32} {amount} ₽")


if __name__ == "__main__":
    main()
