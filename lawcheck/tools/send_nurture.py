"""Рассылка писем nurture-цепочки лидам.

Кому шлём и текст писем — см. reporting/nurture.py.
Отбор: подписчик активен (не отписан), шаг <= 8, next_send_at <= сейчас.

Планово гоняется сервисом `nurture` из docker-compose.yml — ежедневно
в 09:00 МСК (06 UTC), вместе с followups.

Руками (в контейнере followups — там БД и настроенный SMTP):

    # посмотреть, кому уйдёт, ничего не отправляя
    docker compose exec followups python -m lawcheck.tools.send_nurture --dry-run

    # разослать
    docker compose exec followups python -m lawcheck.tools.send_nurture --limit 20
"""
from __future__ import annotations

import argparse
import logging

from lawcheck.db.session import init_db
from lawcheck.reporting import nurture


def main() -> None:
    ap = argparse.ArgumentParser(description="Рассылка nurture-цепочки")
    ap.add_argument("--limit", type=int, default=20,
                    help="макс. писем за прогон")
    ap.add_argument("--dry-run", action="store_true",
                    help="только показать, кому уйдёт, без отправки")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_db()

    summary = nurture.run(limit=args.limit, dry_run=args.dry_run)
    mode = "DRY-RUN" if summary["dry_run"] else "ОТПРАВКА"
    print(f"[{mode}] кандидатов: {summary['candidates']}, "
          f"отправлено: {summary['sent']}, пропущено: {summary['skipped']}")


if __name__ == "__main__":
    main()
