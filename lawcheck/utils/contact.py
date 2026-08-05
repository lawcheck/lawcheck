"""Контакт из заявки — ссылкой, по которой можно ответить в один тап.

Поле в чат-виджете свободное: там оказывается ник, почта или телефон. Разбираем
по виду строки: что не распознали — не ссылка, а текст. URL собираем только из
символов, прошедших регулярку, чтобы чужая строка не попала внутрь href.
"""
import re

from lawcheck.utils.email import valid_email

_TG_NICK = re.compile(r"(?:@|(?:https?://)?t\.me/)([A-Za-z0-9_]{4,32})")
_PHONE = re.compile(r"\+?[\d][\d\s()\-]{9,}")


def mask_contact(contact: str) -> str:
    """Контакт для лога: вид и домен видно, самого адреса нет.

    Логи контейнера читает не только владелец (docker logs, выгрузки, будущий
    сбор логов), а email и телефон из заявки — это персональные данные. Сервису,
    который проверяет чужие сайты на 152-ФЗ, держать их в открытом виде в
    собственных логах странно вдвойне. Полный контакт лежит в БД и виден в
    /inbox — там он и нужен.
    """
    contact = (contact or "").strip()
    if not contact:
        return "—"
    if "@" in contact:
        local, _, domain = contact.partition("@")
        return f"{local[:1]}***@{domain}"
    return f"{contact[:2]}***"


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
