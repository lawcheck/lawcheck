"""Рассылка напоминаний о брошенной оплате (заказ создан → денег нет).

Кому шлём и текст письма — см. reporting/order_reminder.py.
Отбор: заказ в статусе `pending`, есть email, напоминание ещё не слали,
с этого email не оплачен другой заказ, возраст в окне
[--delay-hours; --max-age-days].

Планово гоняется сервисом `followups` из docker-compose.yml — раз в сутки.
Повтор безвреден: отправленным проставлен `Order.reminded_at`.

Руками (в контейнере api — там БД и настроенный SMTP):

    # посмотреть, кому уйдёт, ничего не отправляя
    docker compose exec api python -m lawcheck.tools.send_order_reminders --dry-run

    # разослать
    docker compose exec api python -m lawcheck.tools.send_order_reminders --limit 20
"""
from __future__ import annotations

import argparse
import logging

from lawcheck.db.session import init_db
from lawcheck.reporting import order_reminder


def main() -> None:
    ap = argparse.ArgumentParser(description="Напоминания о брошенной оплате")
    ap.add_argument("--limit", type=int, default=20, help="макс. писем за прогон")
    ap.add_argument("--delay-hours", type=int, default=6,
                    help="не писать раньше N часов после создания заказа")
    ap.add_argument("--max-age-days", type=int, default=14,
                    help="не писать по заказам старше N дней")
    ap.add_argument("--dry-run", action="store_true",
                    help="только показать, кому уйдёт, без отправки")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_db()  # на случай неприменённой миграции orders.reminded_at

    summary = order_reminder.run(limit=args.limit, delay_hours=args.delay_hours,
                                 max_age_days=args.max_age_days, dry_run=args.dry_run)
    mode = "DRY-RUN" if summary["dry_run"] else "ОТПРАВКА"
    print(f"[{mode}] кандидатов: {summary['candidates']}, "
          f"отправлено: {summary['sent']}, пропущено: {summary['skipped']}")


if __name__ == "__main__":
    main()
