"""Транзакционная почта: подтверждение email, сброс пароля.

Провайдер-независимо — обычный SMTP, параметры из настроек (host/port/user/
password/from). Дев без настроенного SMTP: письмо печатается в лог (console-
бэкенд), чтобы в разработке видеть ссылку с токеном без реального сервера.

В отличие от telegram-алертов, отправку НЕ глушим молча: `send_email` возвращает
bool, и вызывающий код решает, показать ли пользователю ошибку («не смогли
отправить письмо, попробуйте ещё раз»). Исключения наружу не пробрасываем.
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from lawcheck.config import settings

log = logging.getLogger(__name__)


def is_configured() -> bool:
    """True — если задан SMTP-хост (иначе работает console-бэкенд в лог)."""
    return bool(settings.smtp_host)


def _build_message(to: str, subject: str, html_body: str, text_body: str | None) -> EmailMessage:
    msg = EmailMessage()
    # smtp_from может быть как "a@b.ru", так и "Имя <a@b.ru>" — сохраняем display-name.
    name, addr = parseaddr(settings.smtp_from)
    msg["From"] = formataddr((name, addr)) if name else (addr or settings.smtp_from)
    msg["To"] = to
    msg["Subject"] = subject
    # text/plain — обязательная альтернатива для клиентов без HTML и для антиспама.
    msg.set_content(text_body or _html_to_text(html_body))
    msg.add_alternative(html_body, subtype="html")
    return msg


def _html_to_text(html: str) -> str:
    """Грубый fallback text/plain из HTML (для писем, где передан только html)."""
    import re
    text = re.sub(r"<\s*br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</\s*p\s*>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def send_email(to: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
    """Отправить письмо. True — если ушло (или залогировано console-бэкендом).
    Ошибки сети/SMTP не пробрасываем — логируем и возвращаем False."""
    if not to:
        return False

    if not is_configured():
        # Dev-бэкенд: не отправляем по сети, печатаем в лог — ссылку с токеном
        # видно в логах приложения. Возвращаем True, чтобы поток регистрации
        # проходил в разработке без реального SMTP.
        log.warning("mailer[console] → %s | %s\n%s", to, subject,
                    text_body or _html_to_text(html_body))
        return True

    msg = _build_message(to, subject, html_body, text_body)

    try:
        if settings.smtp_port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=ctx) as s:
                _login_and_send(s, msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
                if settings.smtp_starttls:
                    s.starttls(context=ssl.create_default_context())
                _login_and_send(s, msg)
        return True
    except Exception as e:
        log.warning("mailer: письмо на %s не отправлено: %s", to, e)
        return False


def _login_and_send(s: smtplib.SMTP, msg: EmailMessage) -> None:
    if settings.smtp_user:
        s.login(settings.smtp_user, settings.smtp_password)
    s.send_message(msg)
