"""Рассылка писем-догонялок лидам (scan_submit → оплата).

Кому шлём и текст письма — см. reporting/followup.py и docs/followup-email-kit.md.
Отбор: лид оставил email на отчёте, но не оплатил; скан завершён и с нарушениями;
письмо ещё не отправляли; не отписан; возраст в окне [--delay-hours; --max-age-days].

Запуск в контейнере web (там БД и настроенный SMTP):

    # посмотреть, кому уйдёт, ничего не отправляя
    docker compose exec web python -m lawcheck.tools.send_followups --dry-run

    # разослать (не больше 20 писем за прогон)
    docker compose exec web python -m lawcheck.tools.send_followups --limit 20

Планово — раз в сутки (cron/RQ). До включения эквайринга Точки CTA письма
ведёт на отчёт (fallback-заявка), после — на реальную оплату.
"""
from __future__ import annotations

import argparse
import logging

from lawcheck.db.session import init_db
from lawcheck.reporting import followup


def main() -> None:
    ap = argparse.ArgumentParser(description="Рассылка писем-догонялок лидам")
    ap.add_argument("--limit", type=int, default=20, help="макс. писем за прогон")
    ap.add_argument("--delay-hours", type=int, default=24,
                    help="не писать раньше N часов после захвата лида")
    ap.add_argument("--max-age-days", type=int, default=14,
                    help="не писать лидам старше N дней")
    ap.add_argument("--dry-run", action="store_true",
                    help="только показать, кому уйдёт, без отправки")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_db()  # на случай неприменённой миграции колонок follow-up

    summary = followup.run(limit=args.limit, delay_hours=args.delay_hours,
                           max_age_days=args.max_age_days, dry_run=args.dry_run)
    mode = "DRY-RUN" if summary["dry_run"] else "ОТПРАВКА"
    print(f"[{mode}] кандидатов: {summary['candidates']}, "
          f"отправлено: {summary['sent']}, пропущено: {summary['skipped']}")


if __name__ == "__main__":
    main()
