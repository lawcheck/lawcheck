"""Разбор входящих на служебном ящике: ответы лидов и отбойники → Telegram.

Кого уведомляем и как считаем дубли — см. notify/inbox.py.
Планово гоняется сервисом `inbox` из docker-compose.yml раз в 5 минут.

Руками (в контейнере api — там БД и учётка почты):

    # посмотреть, что лежит непрочитанным, ничего не отправляя и не помечая
    docker compose exec api python -m lawcheck.tools.poll_inbox --dry-run

    # разобрать
    docker compose exec api python -m lawcheck.tools.poll_inbox
"""
from __future__ import annotations

import argparse
import logging

from lawcheck.notify import inbox


def main() -> None:
    ap = argparse.ArgumentParser(description="Входящие письма → алерт в Telegram")
    ap.add_argument("--limit", type=int, default=20,
                    help="макс. писем за прогон")
    ap.add_argument("--dry-run", action="store_true",
                    help="только показать, без уведомлений и без пометки Seen")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    summary = inbox.run(limit=args.limit, dry_run=args.dry_run)
    mode = "DRY-RUN" if summary["dry_run"] else "РАЗБОР"
    print(f"[{mode}] непрочитанных: {summary['seen']}, "
          f"уведомлений: {summary['notified']}, пропущено: {summary['skipped']}")


if __name__ == "__main__":
    main()
