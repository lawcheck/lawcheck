"""Уведомления владельцу в Telegram (оплаты, лиды, заявки).

Best-effort: ошибки отправки никогда не ломают пользовательский поток —
только пишутся в лог. Вызывать желательно через BackgroundTasks, чтобы
не добавлять задержку запросу.
"""
import logging

import httpx

from lawcheck.config import settings

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"


def is_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_owner_chat_id)


def send_message(chat_id: str, text: str) -> bool:
    """Отправить сообщение в произвольный чат (HTML). Best-effort: ошибки не
    пробрасываем. True — если ушло (для разовых проверок)."""
    if not settings.telegram_bot_token or not chat_id:
        return False
    try:
        r = httpx.post(
            _API.format(token=settings.telegram_bot_token, method="sendMessage"),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=8,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.warning("telegram: сообщение в %s не отправлено: %s", chat_id, e)
        return False


def notify_owner(text: str) -> None:
    """Отправить владельцу сообщение (HTML-разметка)."""
    if not settings.telegram_owner_chat_id:
        return
    send_message(settings.telegram_owner_chat_id, text)
