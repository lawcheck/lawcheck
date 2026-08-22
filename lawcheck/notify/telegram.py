"""Уведомления владельцу в Telegram (оплаты, лиды, заявки).

Best-effort: ошибки отправки никогда не ломают пользовательский поток —
только пишутся в лог. Вызывать желательно через BackgroundTasks, чтобы
не добавлять задержку запросу.
"""
import html
import logging

import httpx

from lawcheck.config import settings
from lawcheck.utils.contact import contact_url

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"


def esc(value) -> str:
    """Экранировать пользовательские данные для HTML-разметки сообщения.

    Сообщения уходят с parse_mode=HTML, поэтому `<` в чужой строке — это не
    «кривой текст», а 400 от Telegram. Ошибка отправки глушится в лог, так что
    уведомление просто пропадает: оплата прошла, а владелец о ней не узнал.
    Всё, что пришло от посетителя (email, текст вопроса, URL сайта, заголовки
    находок с чужих страниц), обязано пройти через esc().
    """
    return html.escape(str(value if value is not None else ""))


def contact_link(contact: str) -> str:
    """Контакт из заявки — кликабельной ссылкой, чтобы ответить в один тап.

    Заявок мало и отвечает на них владелец руками, поэтому вся ценность
    уведомления в скорости. Разбор строки — в `utils/contact.py`, здесь только
    HTML: url собран из проверенных символов, но всё равно идёт через esc() —
    `&` в валидном адресе (`a&b@x.ru`) Telegram в HTML-режиме не прощает, а
    ошибка отправки глушится в лог, и владелец о заявке не узнаёт.
    """
    contact = (contact or "").strip()
    if not contact:
        return "не оставлен"
    url = contact_url(contact)
    return f'<a href="{esc(url)}">{esc(contact)}</a>' if url else esc(contact)


def paid_alert(order) -> str:
    """Текст алерта владельцу об оплате. Источник визита — в том же сообщении:
    иначе он лежит в БД, и вопрос «реклама это или Инстаграм» опять решается
    руками (первый такой разбор шёл грепом по логам Caddy)."""
    # Название тарифа для человека: capitalize() превращает «docs» в «Docs».
    _titles = {"pro": "Pro", "docs": "Документы под сайт"}
    parts = [p for p in (order.entry_ref, order.entry_url) if p]
    src = " → ".join(parts) if parts else "прямой заход"
    return (f"💰 Оплачен заказ <b>{order.id[:8]}</b> — "
            f"{_titles.get(order.plan, order.plan.capitalize())} {order.amount} ₽.\n"
            f"Покупатель: <b>{esc(order.email) or 'email не указан'}</b>\n"
            f"Источник: {esc(src)}")


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
