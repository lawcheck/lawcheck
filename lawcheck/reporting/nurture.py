"""Nurture-цепочка из 8 писем: образовательный контент → оффер.

Подписка приходит с двух форм: «Получить PDF» на отчёте и «прислать образец»
у лид-магнита. Человек с магнита сайт НЕ проверял — тексты писем обязаны
читаться обоими (ревью 19.09.2026 нашло письмо, начинавшееся с «вы только что
проверили сайт», оно уходило и запросившим образец согласия).
Шаг 1 уходит сразу, далее шаг 2-8 с интервалом 7 дней.
Тон — помощь и экспертиза, не давление. Оффер только в письмах 7–8, оба —
про реально существующий Pro за 990 ₽ (никаких акций «1 ₽ вместо 2 990 ₽»:
такого тарифа нет, и письмо продавало призрак).

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
            "Вы оставили email на LawCheck — получили отчёт по сайту или образец "
            "документа. Но соответствие 152-ФЗ – это не разовый проект.</p>"
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
            "Cookie-плашка на сайте может врать: баннер висит, а трекеры "
            "всё равно грузятся до согласия — и это нарушение 152-ФЗ.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Скрытые трекеры обходят согласие через localStorage, fingerprinting "
            "и прямые запросы к third-party доменам – и вы об этом не узнаете, "
            "пока не придёт штраф.</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "LawCheck проверяет не наличие плашки, а её реальную работу: "
            "блокируются ли скрипты до согласия, не утекают ли данные в обход запрета.</p>"
        ),
        "cta": "Как мы находим скрытые трекеры",
    },
    {
        "step": 3,
        "subject": "Политика конфиденциальности устаревает быстрее, чем кажется",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Вы разработали политику конфиденциальности и чувствуете себя "
            "защищённым. Но после каждого обновления сайта – новой функции, "
            "плагина, рекламного трекера – она расходится с реальностью.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "РКН проверяет не наличие документа, а его соответствие реальной "
            "обработке данных. Расхождение – штраф.</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "LawCheck отслеживает актуальность документов при каждом изменении "
            "сайта и предупреждает о несоответствиях.</p>"
        ),
        "cta": "Отслеживать актуальность",
    },
    {
        "step": 4,
        "subject": "Ваш Google Analytics передаёт данные в США – это нарушает 152-ФЗ?",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Вы проверяете сайт, но забываете про маркетинговый стек. "
            "Google Analytics, Meta Pixel, ретаргетинговые сервисы – все они "
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
        "subject": "Реклама на сайте тоже собирает данные — и за это штрафуют",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Пока вы проверяете основной сайт, рекламные кампании могут "
            "нарушать закон отдельно от него.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Ретаргетинговые пиксели срабатывают до получения согласия – это "
            "ст. 13.11 КоАП. Реклама без маркировки и передачи erid в ОРД – "
            "ст. 14.3 КоАП, до 500 000 ₽ на юрлицо. Лендинги обязаны "
            "соответствовать тем же требованиям, что и основной сайт.</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "LawCheck проверяет не только статичный сайт, но и динамические "
            "элементы маркетинга – от таргетинга до email-рассылок.</p>"
        ),
        "cta": "Аудит маркетингового стека",
    },
    {
        "step": 6,
        "subject": "Обновление плагина нарушило ваше согласие – как предотвратить",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Вы настроили соответствие 152-ФЗ, а через неделю «безопасное» "
            "обновление плагина всё сломало.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Автоматические обновления меняют порядок загрузки скриптов, "
            "добавляют новые трекеры, сдвигают элементы согласия – и вы узнаёте "
            "об этом только когда приходит штраф.</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "LawCheck создаёт базовый уровень соответствия и мониторит изменения: "
            "вы получаете алерт до того, как проблема станет нарушением.</p>"
        ),
        "cta": "Настроить контроль изменений",
    },
    {
        "step": 7,
        "subject": "Как устроен еженедельный мониторинг сайта",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Раз в неделю LawCheck заново проходит ваш сайт: политика и согласия, "
            "формы, cookie-баннер, трекеры, реквизиты, реклама, требования к "
            "интернет-магазину – все пункты проверки.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Свежий результат сравнивается с предыдущим. Появился новый трекер, "
            "пропал раздел политики, сломался баннер после обновления темы – "
            "вы получите уведомление с описанием изменения, а не через месяц "
            "письмо от РКН.</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Мониторинг входит в Pro: вместе с готовыми текстами исправлений "
            "и шаблонами документов.</p>"
        ),
        "cta": "Посмотреть, что входит в Pro",
    },
    {
        "step": 8,
        "subject": "Закройте найденное: Pro за 990 ₽ – тексты исправлений под ваш сайт",
        "body": (
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Вы прошли весь путь: скрытые трекеры, устаревшие политики, риски "
            "передачи данных, проблемы с обновлениями. Осталось закрыть "
            "найденное на вашем сайте.</p>"
            "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Pro за 990 ₽ – оплата разовая, без автопродления: все тексты "
            "«Как исправить» под находки вашего отчёта, шаблоны Политики, "
            "согласий и уведомления в РКН, еженедельный мониторинг с "
            "уведомлениями об изменениях. Чек по 54-ФЗ на почту.</p>"
            "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"
            "Документы под ваш сайт руками юриста (с проверкой форм и "
            "PDF-заключением) – отдельный пакет на странице тарифов.</p>"
        ),
        "cta": "Открыть исправления за 990 ₽",
    },
]


def _render_email(step: int, unsub_token: str) -> tuple[str, str, str]:
    """Возвращает (subject, html_body, text_body) для данного шага."""
    data = _EMAILS[step - 1]
    subject = data["subject"]
    content = f"email{step}"
    # Шаги 1–6 образовательные — ведут на сайт, шаг 7–8 про тариф — на /pricing.
    cta_url = _utm(f"{_base_url()}/", content) if step < 7 else _utm(f"{_base_url()}/pricing", content)
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
    Вы получили это письмо, потому что оставили email на LawCheck —
    для отчёта по сайту или для образца документа.
    <a href="{unsub}" style="color:#94A3B8">Отписаться</a>.
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""

    # --- text/plain ---
    import re
    text_body = re.sub(r"<[^>]+>", "", data["body"]).strip()
    text_body = f"{subject}\n\n{text_body}\n\n{data['cta']}: {cta_url}\n\n-- Максим Подольский, LawCheck\nВы получили это письмо, потому что оставили email на LawCheck.\nОтписаться: {unsub}"

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
    """Батч: разослать письма текущего шага всем подходящим подписчикам.

    Оплативших отписываем здесь, а не в вебхуке оплаты: вебхук знает только
    `Order.email`, а подписка могла прийти на другой адрес того же человека
    (магнит + покупка). Проверка по списку оплаченных email закрывает обе
    записи сразу — иначе клиент ещё семь недель получает письма «активируйте».
    """
    subscribers = repo.nurture_to_send(limit)
    sent = skipped = paid = 0
    to_send = []
    for sub in subscribers:
        if repo.nurture_remove_paid(sub.email):
            paid += 1
            continue
        to_send.append(sub)
    for sub in to_send:
        if dry_run:
            log.info("nurture[dry] → %s | шаг %d",
                     mask_contact(sub.email), sub.step)
            continue
        if send_one(sub):
            sent += 1
        else:
            skipped += 1
    return {"candidates": len(subscribers), "sent": sent,
            "skipped": skipped, "paid_skipped": paid, "dry_run": dry_run}
