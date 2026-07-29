"""Контакт из заявки — ссылкой, по которой можно ответить в один тап.

Поле в чат-виджете свободное: там оказывается ник, почта или телефон. Разбираем
по виду строки: что не распознали — не ссылка, а текст. URL собираем только из
символов, прошедших регулярку, чтобы чужая строка не попала внутрь href.
"""
import re

from lawcheck.utils.email import valid_email

_TG_NICK = re.compile(r"(?:@|(?:https?://)?t\.me/)([A-Za-z0-9_]{4,32})")
_PHONE = re.compile(r"\+?[\d][\d\s()\-]{9,}")


def contact_url(contact: str) -> str | None:
    """Ссылка для ответа на контакт или None, если вид строки не распознан."""
    contact = (contact or "").strip()
    if not contact:
        return None
    nick = _TG_NICK.fullmatch(contact)
    if nick:
        return f"https://t.me/{nick.group(1)}"
    if valid_email(contact):
        return f"mailto:{contact}"
    if _PHONE.fullmatch(contact):
        return "tel:" + re.sub(r"[^+\d]", "", contact)
    return None
