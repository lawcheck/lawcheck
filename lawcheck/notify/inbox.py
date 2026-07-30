"""Входящие на служебный ящик → алерт владельцу в Telegram.

Письма-догонялки просят лида ответить, но уходят от `noreply@`, который никто
не открывает. Сюда же падают отбойники почтовика — три штуки пролежали
непрочитанными месяц, и о неудачных доставках никто не узнал.

Дедупликация — через флаг `\\Seen` самого IMAP: уведомили → пометили
прочитанным → в следующий прогон письмо не попадёт. Своей таблицы не заводим,
состояние живёт там же, где письма. Обратная сторона: если открыть ящик
в вебмейле раньше поллера, уведомление не придёт.

Батч запускает `lawcheck.tools.poll_inbox`, планово — сервис `inbox` в compose.
"""
from __future__ import annotations

import email
import imaplib
import logging
import ssl
from email.header import decode_header, make_header
from email.utils import parseaddr

from lawcheck.config import settings
from lawcheck.notify import telegram

log = logging.getLogger(__name__)

# Отбойники приходят от почтовика, а не от человека: их помечаем отдельно,
# чтобы владелец не искал в них ответ лида.
_BOUNCE_SENDERS = ("mailer-daemon", "postmaster")


def is_configured() -> bool:
    """Читать ящик можно, когда известны хост и учётка. Хост выводим из SMTP:
    у Timeweb smtp.timeweb.ru ↔ imap.timeweb.ru."""
    return bool(_host() and settings.smtp_user and settings.smtp_password)


def _host() -> str:
    if settings.imap_host:
        return settings.imap_host
    if settings.smtp_host.startswith("smtp."):
        return "imap." + settings.smtp_host[len("smtp."):]
    return ""


def _decode(raw: str) -> str:
    """MIME-заголовок → читаемая строка. Кривую кодировку не роняем: заголовок
    от чужого почтовика не должен останавливать разбор ящика."""
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return raw or ""


def _is_bounce(sender: str) -> bool:
    return any(s in sender.lower() for s in _BOUNCE_SENDERS)


def _format(sender: str, subject: str, date: str) -> str:
    """Сообщение владельцу. Всё, что пришло из письма, обязано пройти esc():
    чужая тема с `<` — это 400 от Telegram и потерянное уведомление."""
    name, addr = parseaddr(sender)
    who = f"{name} &lt;{telegram.esc(addr)}&gt;" if name else telegram.esc(addr or sender)
    if _is_bounce(sender):
        head = "📮 Письмо не доставлено"
    else:
        head = "✉️ Ответ на письмо"
    lines = [head, "", f"от: {who}", f"тема: {telegram.esc(subject)}"]
    if date:
        lines.append(f"когда: {telegram.esc(date)}")
    return "\n".join(lines)


def run(limit: int = 20, dry_run: bool = False) -> dict:
    """Разобрать непрочитанные письма: уведомить владельца и пометить Seen.

    Помечаем ТОЛЬКО после успешной отправки в Telegram — иначе упавший
    Telegram молча съедал бы входящие: письмо помечено, уведомления нет.
    """
    if not is_configured():
        log.warning("inbox: IMAP не настроен — пропускаем")
        return {"seen": 0, "notified": 0, "skipped": 0, "dry_run": dry_run}

    notified = skipped = 0
    ctx = ssl.create_default_context()
    with imaplib.IMAP4_SSL(_host(), settings.imap_port, ssl_context=ctx,
                           timeout=30) as m:
        m.login(settings.smtp_user, settings.smtp_password)
        m.select("INBOX", readonly=dry_run)
        status, data = m.search(None, "UNSEEN")
        ids = data[0].split() if status == "OK" and data and data[0] else []
        for msg_id in ids[:limit]:
            # BODY.PEEK — читаем заголовки, не трогая флаг: пометим сами и
            # только после того, как уведомление действительно ушло.
            status, raw = m.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if status != "OK" or not raw or not raw[0]:
                skipped += 1
                continue
            head = email.message_from_bytes(raw[0][1])
            sender = _decode(head.get("From", ""))
            subject = _decode(head.get("Subject", "(без темы)"))
            date = _decode(head.get("Date", ""))
            text = _format(sender, subject, date)
            if dry_run:
                log.info("inbox[dry] → %s | %s", sender, subject)
                continue
            if telegram.send_message(settings.telegram_owner_chat_id, text):
                m.store(msg_id, "+FLAGS", "\\Seen")
                notified += 1
            else:
                log.warning("inbox: уведомление о письме от %s не ушло — "
                            "Seen не ставим, попробуем в следующий прогон", sender)
                skipped += 1
        return {"seen": len(ids), "notified": notified, "skipped": skipped,
                "dry_run": dry_run}
