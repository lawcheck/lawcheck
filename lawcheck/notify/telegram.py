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
    return (f"💰 Оплачен заказ <b>{order.id[:8]}</b> – "
            f"{_titles.get(order.plan, order.plan.capitalize())} {order.amount} ₽.\n"
            f"Покупатель: <b>{esc(order.email) or 'email не указан'}</b>\n"
            f"Источник: {esc(src)}")


def is_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_owner_chat_id)


def reachable_ips(timeout: float = 4.0) -> list[str]:
    """Какие адреса Telegram отвечают на 443 прямо сейчас — для диагностики.

    Список кандидатов берём из net.py, а не заводим свой: выбором рабочего
    адреса занимается там же `_pick_telegram_ip`, и две копии списка разъехались
    бы при первом же изменении.
    """
    import socket

    from lawcheck.net import _TELEGRAM_FALLBACK_IPS
    alive = []
    for ip in _TELEGRAM_FALLBACK_IPS:
        try:
            with socket.create_connection((ip, 443), timeout=timeout):
                alive.append(ip)
        except OSError:
            continue
    return alive


def check_api() -> tuple[bool, str]:
    """Жив ли канал: getMe к Bot API. Возвращает (ок, подробность).

    Проверять надо именно вызовом API, а не TCP-соединением: адрес может
    терминировать TLS и при этом не обслуживать бота.
    """
    if not settings.telegram_bot_token:
        return False, "TELEGRAM_BOT_TOKEN не задан"
    try:
        data = _request_with_retry("getMe").json()
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"
    if not data.get("ok"):
        return False, f"Bot API вернул ok=false: {str(data)[:200]}"
    return True, str((data.get("result") or {}).get("username") or "бот без username")


# Сколько раз пробуем запрос к Bot API. DPI из РФ-ДЦ роняет часть соединений
# уже после успешного TCP-рукопожатия: проба адреса проходит, а запрос по нему
# виснет до таймаута. Замер на проде — примерно один прогон из пяти. Повтор со
# сбросом выбранного адреса превращает это в редкость; без него каждое пятое
# уведомление просто пропадало бы, и заметить это нечем.
_ATTEMPTS = 2


def _request_with_retry(method: str, **kwargs) -> httpx.Response:
    """POST к Bot API с повтором по сетевой ошибке и сменой адреса."""
    from lawcheck import net

    last: Exception | None = None
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            r = httpx.post(_API.format(token=settings.telegram_bot_token, method=method),
                           timeout=8, **kwargs)
            r.raise_for_status()
            return r
        except httpx.TransportError as e:
            last = e
            log.warning("telegram: попытка %s/%s не удалась (%s)", attempt, _ATTEMPTS, e)
            # Держаться за адрес, по которому только что не прошло, незачем:
            # следующий перебор может выбрать другой.
            net.reset_telegram_ip()
    raise last if last is not None else RuntimeError("telegram: неизвестная ошибка")


def send_message(chat_id: str, text: str) -> bool:
    """Отправить сообщение в произвольный чат (HTML). Best-effort: ошибки не
    пробрасываем. True — если ушло (для разовых проверок)."""
    if not settings.telegram_bot_token or not chat_id:
        return False
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        _request_with_retry("sendMessage", json=payload)
        return True
    except Exception as e:
        log.warning("telegram: сообщение в %s не отправлено: %s", chat_id, e)
        return False


# Защита от кольца: mailer при сбое SMTP сам зовёт notify_owner. Без флага
# «Telegram лежит + SMTP лежит» дало бы бесконечную рекурсию между модулями.
_in_email_fallback = False


def _owner_email_fallback(text: str) -> None:
    """Продублировать алерт на почту, когда Telegram не ответил.

    Telegram остаётся основным каналом — почта нужна ровно на случай, когда он
    молчит. Молчание вероятнее обычного: РФ-дата-центры режут часть диапазонов
    Telegram, и канал держится на переборе адресов в net.py. А ошибка отправки
    глушится в лог, поэтому пропавшее уведомление ничем себя не выдаёт.
    """
    global _in_email_fallback
    if _in_email_fallback:
        return
    from lawcheck.notify import mailer
    from lawcheck.web.operator import OPERATOR
    to = OPERATOR.get("email", "")
    if not to or not mailer.is_configured():
        return
    _in_email_fallback = True
    try:
        mailer.send_email(to, "LawCheck: алерт не ушёл в Telegram", text)
    except Exception:
        log.exception("алерт владельцу не доставлен ни в Telegram, ни на почту")
    finally:
        _in_email_fallback = False


def notify_owner(text: str) -> None:
    """Отправить владельцу сообщение (HTML-разметка), с запасным каналом."""
    if not settings.telegram_owner_chat_id:
        return
    if send_message(settings.telegram_owner_chat_id, text):
        return
    _owner_email_fallback(text)
