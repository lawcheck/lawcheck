"""Отметка «email-джобы отработали» для сторожа heartbeat.

Контейнер followups гоняет три рассылки раз в сутки и зовёт этот инструмент,
только если все три вышли с кодом 0. Смысл тот же, что у отметок хостового
cron: тревогу поднимает не пойманная ошибка (её глушит `|| true`), а
отсутствие отметки — сломается SMTP или контейнер умрёт, сторож в api это
заметит (см. notify/heartbeat.py).

Отметка идёт прямо в БД, а не через HTTP /internal/heartbeat: контейнеру
рассыльщику INTERNAL_KEY намеренно не выдаётся (docker-compose.yml — меньше
секретов в процессе, который крутит cron-команды), а DATABASE_URL у него есть.
"""
import logging

from lawcheck.db import repo
from lawcheck.db.session import init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> None:
    init_db()
    repo.mark_job_ok("email-jobs")
    logging.info("email-jobs отметились")


if __name__ == "__main__":
    main()
