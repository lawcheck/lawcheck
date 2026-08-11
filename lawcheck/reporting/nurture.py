"""Nurture-цепочка из 8 писем: образовательный контент → оффер.

Лид оставил email на странице отчёта → попадает в nurture_subscribers.
Шаг 1 уходит сразу, далее шаг 2-8 с интервалом 7 дней.
Тон — помощь и экспертиза, не давление. Оффер только в письме 8.

Запуск батча — через CLI lawcheck.tools.send_nurture.
"""
from __future__ import annotations

import html
import logging

from lawcheck.config import settings
from lawcheck.db import repo
from lawcheck.db.models import NurtureSubscriber
from lawcheck.notify import mailer
from lawcheck.utils.contact import mask_contact

log = logging.getLogger(__name__)

TOTAL_STEPS = 8


def _base_url() -> str:
    return settings.site_base_url.rstrip("/")


def _utm(url: str, content: str) -> str:
    sep = "&" if "?" in url else "?"
    return (f"{url}{sep}utm_source=email&utm_medium=email"
            f"&utm_campaign=nurture&utm_content={content}")


def _unsub_url(token: str) -> str:
    return f"{_base_url()}/unsubscribe/{token}"


# --- Тексты писем ---

_EMAILS: list[dict] = [
    {
        "step": 1,
        "subject": "Ваш чек-лист по 152-ФЗ внутри: что проверять прямо сейчас",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Вы только что проверили свой сайт на соответствие 152-ФЗ. "
            "Но compliance — это не разовый проект.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Каждую неделю появляются новые трекеры, меняются политики, "
            "обновляются требования РКН. То, что сегодня чисто, завтра может "
            "стать нарушением.</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "LawCheck обеспечивает непрерывный мониторинг: еженедельное "
            "сканирование и мгновенные алерты о новых рисках.</p>"
        ),
        "cta": "Как работает мониторинг",
    },
    {
        "step": 2,
        "subject": "Скрытые трекеры: как они собирают данные даже при отказе",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Знаете ли вы, что cookie-плашка может врать? 73% сайтов с баннером "
            "согласия всё равно нарушают 152-ФЗ.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Скрытые трекеры обходят согласие через localStorage, fingerprinting "
            "и прямые запросы к third-party доменам — и вы об этом не узнаете, "
            "пока не придёт штраф.</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "LawCheck проверяет не наличие плашки, а её реальную работу: "
            "блокируются ли скрипты до согласия, не утекают ли данные в обход запрета.</p>"
        ),
        "cta": "Как мы находим скрытые трекеры",
    },
    {
        "step": 3,
        "subject": "Ваша политика конфиденциальности устарела в среднем на 63 дня",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Вы разработали политику конфиденциальности и чувствуете себя "
            "защищённым. Но после каждого обновления сайта — новой функции, "
            "плагина, рекламного трекера — она устаревает.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "РКН проверяет не наличие документа, а его соответствие реальной "
            "обработке данных. Расхождение — штраф.</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "LawCheck отслеживает актуальность документов при каждом изменении "
            "сайта и предупреждает о несоответствиях.</p>"
        ),
        "cta": "Отслеживать актуальность",
    },
    {
        "step": 4,
        "subject": "Ваш Google Analytics передаёт данные в США — это нарушает 152-ФЗ?",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Вы проверяете сайт, но забываете про маркетинговый стек. "
            "Google Analytics, Meta Pixel, ретаргетинговые сервисы — все они "
            "могут передавать данные пользователей за пределы РФ.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Даже сервисы с «российскими отделениями» часто хранят данные на "
            "зарубежных серверах. РКН штрафует за это независимо от ваших намерений.</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "LawCheck анализирует весь ваш стек: от IP-адресов получателей данных "
            "до гарантий локализации в договорах с третьими лицами.</p>"
        ),
        "cta": "Проверить свой стек",
    },
    {
        "step": 5,
        "subject": "Штраф за нарушение в рекламе: до 4% выручки — как избежать",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Пока вы проверяете основной сайт, ваши рекламные кампании могут "
            "нарушать закон.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Ретаргетинговые пиксели срабатывают до получения согласия? "
            "UTM-метки передают данные в запрещённые юрисдикции? "
            "Лендинги соответствуют тем же требованиям, что и основной сайт?</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "LawCheck проверяет не только статичный сайт, но и динамические "
            "элементы маркетинга — от таргетинга до email-рассылок.</p>"
        ),
        "cta": "Аудит маркетингового стека",
    },
    {
        "step": 6,
        "subject": "Обновление плагина нарушило ваше согласие — как предотвратить",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Вы настроили соответствие 152-ФЗ, а через неделю «безопасное» "
            "обновление плагина всё сломало.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Автоматические обновления меняют порядок загрузки скриптов, "
            "добавляют новые трекеры, сдвигают элементы согласия — и вы узнаёте "
            "об этом только когда приходит штраф.</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "LawCheck создаёт базовый уровень соответствия и мониторит изменения: "
            "вы получаете алерт до того, как проблема станет нарушением.</p>"
        ),
        "cta": "Настроить контроль изменений",
    },
    {
        "step": 7,
        "subject": "Как интернет-магазин снизил риски штрафов на 90% за 3 месяца",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Оборот 500 млн руб/год, WooCommerce, десятки маркетинговых интеграций. "
            "Каждые 4-6 недель — предупреждение от РКН.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "После подключения LawCheck: еженедельное сканирование, мгновенные "
            "алерты, пошаговые инструкции по устранению. Результат за 3 месяца: "
            "ноль предупреждений, экономия 1,2 млн руб/год, сокращение времени "
            "на проверки с 10 до 2 часов в месяц.</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Это не единичный случай — это стандартный результат непрерывного мониторинга.</p>"
        ),
        "cta": "Читать полный кейс",
    },
    {
        "step": 8,
        "subject": "Первый месяц мониторинга за 1 рубль — только 7 дней",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Вы прошли весь путь: узнали о скрытых трекерах, устаревших политиках, "
            "рисках передачи данных и проблемах с обновлениями. "
            "Вы понимаете — нужен непрерывный мониторинг.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Попробуйте первый месяц за 1 рубль вместо 2 990 руб. "
            "Еженедельное сканирование 47 точек соответствия, мгновенные алерты, "
            "инструкции по устранению. Никаких обязательств — можно отменить в любой момент.</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "<strong style='color:#00053D'>Предложение действует 7 дней.</strong></p>"
        ),
        "cta": "Активировать за 1 рубль",
    },
]


def _render_email(step: int, unsub_token: str) -> tuple[str, str, str]:
    """Возвращает (subject, html_body, text_body) для данного шага."""
    data = _EMAILS[step - 1]
    subject = data["subject"]
    content = f"email{step}"
    cta_url = _utm(f"{_base_url()}/", content) if step < 8 else _utm(f"{_base_url()}/pricing", content)
    unsub = _unsub_url(unsub_token)

    # --- HTML ---
    html_body = f"""\
<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F0F4F8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F0F4F8;padding:40px 0">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)">
  <tr><td style="padding:32px 40px 24px">
    <table cellpadding="0" cellspacing="0"><tr>
      <td style="width:36px;height:36px;background:#0B5CFF;border-radius:10px;text-align:center;line-height:36px">
        <span style="color:#fff;font-size:18px;font-weight:700">&#9881;</span>
      </td>
      <td style="padding-left:12px">
        <span style="font-size:20px;font-weight:700;color:#00053D;letter-spacing:-0.3px">LawCheck</span>
        <br><span style="font-size:12px;color:#94A3B8">проверка сайтов на 152-ФЗ</span>
      </td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:0 40px 12px">
    <h2 style="margin:0 0 20px;font-size:20px;color:#00053D;font-weight:700">{html.escape(subject)}</h2>
    {data['body']}
  </td></tr>
  <tr><td style="padding:0 40px 28px">
    <a href="{cta_url}" style="display:inline-block;background:#0B5CFF;color:#fff;text-decoration:none;padding:14px 32px;border-radius:12px;font-size:15px;font-weight:600">{data['cta']}</a>
  </td></tr>
  <tr><td style="border-top:1px solid #E2E8F0;padding:20px 40px 32px;font-size:12px;color:#94A3B8;line-height:1.6">
    - Максим Подольский, LawCheck<br>
    Вы получили это письмо, потому что подписались на проверку сайта через LawCheck.
    <a href="{unsub}" style="color:#94A3B8">Отписаться</a>.
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""

    # --- text/plain ---
    import re
    text_body = re.sub(r"<[^>]+>", "", data["body"]).strip()
    text_body = f"{subject}\n\n{text_body}\n\n{data['cta']}: {cta_url}\n\n-- Максим Подольский, LawCheck\nОтписаться: {unsub}"

    return subject, html_body, text_body


def send_one(sub: NurtureSubscriber) -> bool:
    """Отправить текущий шаг подписчику. True — если ушло."""
    subject, html_body, text_body = _render_email(sub.step, sub.unsub_token)
    ok = mailer.send_email(sub.email, subject, html_body, text_body)
    if ok:
        repo.nurture_advance(sub.id)
    else:
        log.warning("nurture: письмо шаг %d на %s не ушло",
                    sub.step, mask_contact(sub.email))
    return ok


def run(limit: int = 50, dry_run: bool = False) -> dict:
    """Батч: разослать письма текущего шага всем подходящим подписчикам."""
    subscribers = repo.nurture_to_send(limit)
    sent = skipped = 0
    for sub in subscribers:
        if dry_run:
            log.info("nurture[dry] → %s | шаг %d",
                     mask_contact(sub.email), sub.step)
            continue
        if send_one(sub):
            sent += 1
        else:
            skipped += 1
    return {"candidates": len(subscribers), "sent": sent,
            "skipped": skipped, "dry_run": dry_run}
