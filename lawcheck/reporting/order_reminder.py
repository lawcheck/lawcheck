"""Письмо о брошенной оплате: дожимаем узкое место воронки «заказ → деньги».

Человек нажал «Оплатить», получил ссылку банка и не заплатил. Через сутки шлём
ОДНО письмо: что заказано, на сколько, свежая ссылка на оплату. Серии нет.

Рамка по ст. 18 ФЗ «О рекламе»: это сервисное сообщение по сделке, которую
инициировал сам человек, поэтому согласия на рекламу не требуется — но ровно
до тех пор, пока в письме нет продажи. Никаких «а ещё у нас есть Business» и
персональных предложений: как только появится оффер, письмо станет рекламой,
а согласия при оформлении заказа никто не давал. Описание того, что уже
заказано, рекламой не является — его оставляем, человек должен помнить предмет.

Запуск батча — через CLI-инструмент `lawcheck.tools.send_order_reminders`.
"""
from __future__ import annotations

import html
import logging
from urllib.parse import urlencode

from lawcheck.config import settings
from lawcheck.db import repo
from lawcheck.db.models import Order
from lawcheck.notify import mailer

log = logging.getLogger(__name__)


def _with_utm(url: str, campaign: str = "order_reminder") -> str:
    """UTM-метки — чтобы в Метрике отделить возвраты по напоминанию от рекламы."""
    params = urlencode({"utm_source": "email", "utm_medium": "email",
                        "utm_campaign": campaign})
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{params}"


def build_context(order: Order) -> dict:
    base = settings.site_base_url.rstrip("/")
    return {
        "plan": order.plan,
        "plan_title": "Pro" if order.plan == "pro" else order.plan.capitalize(),
        "amount": order.amount,
        "created": order.created_at.strftime("%d.%m") if order.created_at else "",
        # Ссылка ведёт на перевыпуск: сохранённая в заказе ссылка банка к этому
        # моменту, скорее всего, протухла — см. web/routes.py::pay_retry.
        "pay_url": _with_utm(f"{base}/pay/retry/{order.id}"),
        "report_url": _with_utm(f"{base}/report/{order.scan_id}") if order.scan_id else "",
    }


def render(ctx: dict) -> tuple[str, str, str]:
    """Тема, HTML и текстовая версия письма."""
    e = html.escape
    title = ctx["plan_title"]
    amount = ctx["amount"]
    subject = f"Ваш заказ LawCheck {title} — ссылка для оплаты"

    text_lines = [
        (f"{ctx['created']} вы оформили" if ctx["created"] else "Вы оформили")
        + f" заказ на LawCheck {title} за {amount} ₽, но оплата так и не прошла.",
        "",
        "Ссылка банка живёт недолго, поэтому вот новая — она откроет оплату "
        "картой или через СБП:",
        ctx["pay_url"],
        "",
        f"После оплаты на {title} открываются готовые тексты исправлений под ваш "
        "сайт, шаблоны Политики обработки ПДн, согласий и уведомления в РКН, "
        "еженедельный мониторинг и PDF-заключение.",
    ]
    if ctx["report_url"]:
        text_lines += ["", f"Ваш отчёт: {ctx['report_url']}"]
    text_lines += [
        "",
        "Если передумали — делать ничего не нужно, это разовое напоминание, "
        "больше писем по этому заказу не будет. Что-то не сработало или есть "
        "вопрос — просто ответьте на письмо, разберусь.",
        "",
        "— Максим Подольский, LawCheck",
    ]
    text_body = "\n".join(text_lines)

    parts = [
        "<p>" + (f"{e(ctx['created'])} вы оформили" if ctx["created"] else "Вы оформили")
        + f" заказ на <b>LawCheck {e(title)}</b> за <b>{amount} ₽</b>, "
        "но оплата так и не прошла.</p>",
        "<p>Ссылка банка живёт недолго, поэтому вот новая — она откроет оплату "
        "картой или через СБП:</p>",
        f'<p><a href="{e(ctx["pay_url"])}" style="display:inline-block;'
        'background:#1a1a1a;color:#fff;text-decoration:none;padding:12px 22px;'
        f'border-radius:6px">Оплатить {amount} ₽</a></p>',
        f"<p>После оплаты на {e(title)} открываются готовые тексты исправлений "
        "под ваш сайт, шаблоны Политики обработки ПДн, согласий и уведомления "
        "в РКН, еженедельный мониторинг и PDF-заключение.</p>",
    ]
    if ctx["report_url"]:
        parts.append(f'<p><a href="{e(ctx["report_url"])}">Открыть ваш отчёт →</a></p>')
    parts.append(
        "<p>Если передумали — делать ничего не нужно, это разовое напоминание, "
        "больше писем по этому заказу не будет. Что-то не сработало или есть "
        "вопрос — просто ответьте на письмо, разберусь.</p>"
        '<p style="color:#888;font-size:13px">— Максим Подольский, LawCheck · '
        "проверка сайтов на 152-ФЗ и смежные законы<br>"
        "Вы получили письмо, потому что оформили заказ на lawchek.ru.</p>")
    return subject, "".join(parts), text_body


def send_one(order: Order) -> bool:
    """Собрать и отправить напоминание; при успехе — отметить `reminded_at`."""
    subject, html_body, text_body = render(build_context(order))
    ok = mailer.send_email(order.email, subject, html_body, text_body)
    if ok:
        repo.mark_order_reminded(order.id)
    else:
        log.warning("order_reminder: письмо по заказу %s не ушло — reminded_at не ставим",
                    order.id)
    return ok


def run(limit: int = 20, delay_hours: int = 6, max_age_days: int = 14,
        dry_run: bool = False) -> dict:
    """Батч: разослать напоминания по брошенным оплатам. dry_run — только
    показать, кому и что, не отправляя и не отмечая. Возвращает сводку."""
    orders = repo.orders_to_remind(delay_hours, max_age_days, limit)
    sent = skipped = 0
    for order in orders:
        if dry_run:
            subject, _, _ = render(build_context(order))
            log.info("order_reminder[dry] → %s | %s ₽ | %s",
                     order.email, order.amount, subject)
            continue
        if send_one(order):
            sent += 1
        else:
            skipped += 1
    return {"candidates": len(orders), "sent": sent, "skipped": skipped,
            "dry_run": dry_run}
