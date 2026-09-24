"""Nurture-цепочка из 8 писем: образовательный контент → оффер.

Подписка приходит с двух форм: «Получить PDF» на отчёте и «прислать образец»
у лид-магнита. Человек с магнита сайт НЕ проверял — тексты писем обязаны
читаться обоими (ревью 19.09.2026 нашло письмо, начинавшееся с «вы только что
проверили сайт», оно уходило и запросившим образец согласия).
Шаг 1 уходит сразу, далее шаг 2-8 с интервалом 7 дней.
Тон — помощь и экспертиза, не давление. Оффер только в письмах 7–8 и только
реальные тарифы с /pricing: Pro 990 ₽/мес и «Документы под сайт» 8 000 ₽
(никаких акций «1 ₽ вместо 2 990 ₽»: такого тарифа нет, письмо продавало призрак).

Запуск батча — через CLI lawcheck.tools.send_nurture.
"""
from __future__ import annotations

import html
import logging
import re
from urllib.parse import urlparse

from lawcheck.config import settings
from lawcheck.db import repo
from lawcheck.db.models import NurtureSubscriber, Scan
from lawcheck.notify import mailer
from lawcheck.reporting import gating
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
#
# Каждое письмо обещает только то, что сканер реально делает (ревью 24.09.2026
# нашло «ловим fingerprinting», «читаем договоры с третьими лицами»,
# «мгновенные алерты» — ничего из этого нет). Нормы и штрафы сверены с
# pravo.gov.ru 24.09.2026 (ч. 2 ст. 13.11 КоАП, ч. 3 ст. 12 152-ФЗ); ст. 14.3 —
# из статьи блога про маркировку. Отдельного штрафа за трансграничную передачу
# в 13.11 нет — не придумывать.
# CTA ведёт туда, что обещает подпись кнопки: статья блога, генератор или скан.

_P = "<p style='margin:0 0 16px;font-size:15px;color:#1E293B;line-height:1.6'>"
_P_LAST = "<p style='margin:0;font-size:15px;color:#1E293B;line-height:1.6'>"

_EMAILS: list[dict] = [
    {
        "step": 1,
        "subject": "5 пунктов 152-ФЗ, которые стоит проверить на сайте первыми",
        "body": (
            f"{_P}Вы оставили email на LawCheck – для отчёта по сайту или "
            "образца документа. Раз в неделю будем присылать по одной теме: "
            "что Роскомнадзор смотрит на сайтах и как это закрыть.</p>"
            f"{_P}Начните с пяти пунктов:</p>"
            "<ol style='margin:0 0 16px;padding:0 0 0 20px;font-size:15px;"
            "color:#1E293B;line-height:1.6'>"
            "<li>Политика обработки ПДн открывается с каждой страницы, где есть форма.</li>"
            "<li>Галочку согласия в форме человек ставит сам, заранее она не стоит.</li>"
            "<li>Метрика и другие счётчики не грузятся до ответа на cookie-баннер.</li>"
            "<li>На сайте указаны реквизиты оператора: название, ИНН или ОГРН.</li>"
            "<li>Оператор есть в реестре Роскомнадзора – уведомление подано.</li>"
            "</ol>"
            f"{_P_LAST}Все пять LawCheck проверяет автоматически. Бесплатная "
            "проверка покажет, какие пункты уже в порядке, а какие нет.</p>"
        ),
        "cta": "Проверить сайт бесплатно",
        "cta_path": "/",
    },
    {
        "step": 2,
        "subject": "Cookie-баннер висит, а Метрика уже загрузилась",
        "body": (
            f"{_P}Самое частое нарушение из тех, что мы находим: баннер на месте, "
            "а счётчики стартуют сразу при открытии страницы. Человек ещё не "
            "нажал ни «Принять», ни «Отказаться».</p>"
            f"{_P}Cookie-идентификаторы Роскомнадзор считает персональными данными. "
            "Обработка без согласия – ч. 2 ст. 13.11 КоАП, юрлицу до 700 000 ₽.</p>"
            f"{_P_LAST}LawCheck открывает сайт как новый посетитель и записывает, "
            "какие счётчики сработали до клика по баннеру. Если такие есть, "
            "в отчёте будет их список.</p>"
        ),
        "cta": "Как настроить баннер по закону",
        "cta_path": "/blog/cookie-banner-po-zakonu",
    },
    {
        "step": 3,
        "subject": "Политика на сайте есть, но описывает не ваш сайт",
        "body": (
            f"{_P}Политику обработки ПДн часто берут готовой. Потом на сайте "
            "появляется новая форма, чат или счётчик, а политика об этом молчит.</p>"
            f"{_P}Роскомнадзор сверяет документ с тем, что сайт делает на самом деле. "
            "Данные уходят зарубежному сервису, а раздела о трансграничной "
            "передаче в политике нет – это уже нарушение.</p>"
            f"{_P_LAST}LawCheck делает такую сверку: находит формы и счётчики и "
            "проверяет, есть ли в политике нужные разделы. Если политики нет "
            "совсем, черновик можно собрать бесплатно в нашем генераторе.</p>"
        ),
        "cta": "Собрать политику бесплатно",
        "cta_path": "/politika-obrabotki-pd",
    },
    {
        "step": 4,
        "subject": "Зарубежный счётчик на сайте – это трансграничная передача данных",
        "body": (
            f"{_P}Google Analytics, пиксели соцсетей, чат-виджеты с серверами "
            "за рубежом – каждый из них отправляет данные посетителей за "
            "пределы России.</p>"
            f"{_P}До начала такой передачи оператор обязан уведомить "
            "Роскомнадзор, отдельно от обычного уведомления об обработке ПДн – "
            "ч. 3 ст. 12 152-ФЗ. А политика должна эту передачу описывать.</p>"
            f"{_P_LAST}LawCheck составляет список всех сторонних счётчиков на "
            "сайте и отмечает зарубежные. Отдельно проверяет, есть ли в "
            "политике раздел о трансграничной передаче.</p>"
        ),
        "cta": "Какие счётчики считаются зарубежными",
        "cta_path": "/blog/zarubezhnye-schetchiki-i-trekery-152-fz",
    },
    {
        "step": 5,
        "subject": "Реклама на сайте без токена erid – штраф за каждый креатив",
        "body": (
            f"{_P}Если на сайте крутятся блоки РСЯ или AdSense, сайт становится "
            "площадкой для рекламы. С 1 сентября 2022 года каждый рекламный "
            "креатив в интернете несёт токен erid и пометку «Реклама».</p>"
            f"{_P}Нет токена – ст. 14.3 КоАП, юрлицу от 200 000 до 500 000 ₽. "
            "Штраф считают по каждому креативу отдельно.</p>"
            f"{_P_LAST}LawCheck находит на сайте рекламные сети и проверяет, есть "
            "ли у креативов токен. Кто отвечает за маркировку – вы, рекламная "
            "сеть или агентство – разобрали в статье.</p>"
        ),
        "cta": "Кто и как маркирует рекламу",
        "cta_path": "/blog/markirovka-internet-reklamy",
    },
    {
        "step": 6,
        "subject": "Сайт был в порядке, пока не обновили тему",
        "body": (
            f"{_P}Соответствие 152-ФЗ ломается и без вашего участия. Обновление "
            "темы меняет порядок загрузки скриптов, подрядчик ставит новый "
            "счётчик, форма теряет галочку согласия.</p>"
            f"{_P}Узнать об этом можно двумя способами: проверить сайт заново "
            "или дождаться, пока заметит Роскомнадзор.</p>"
            f"{_P_LAST}Повторная проверка в LawCheck бесплатная. Запустите её после "
            "любого заметного обновления сайта.</p>"
        ),
        "cta": "Проверить сайт ещё раз",
        "cta_path": "/",
    },
    {
        "step": 7,
        "subject": "Как устроен еженедельный мониторинг сайта",
        "body": (
            f"{_P}Раз в неделю LawCheck заново проходит ваш сайт: политика и согласия, "
            "формы, cookie-баннер, трекеры, реквизиты, реклама, требования к "
            "интернет-магазину – все пункты проверки.</p>"
            f"{_P}Свежий результат сравнивается с предыдущим. Появился новый трекер, "
            "пропал раздел политики, сломался баннер после обновления темы – "
            "в Telegram придёт сообщение: что изменилось и ссылка на новый отчёт.</p>"
            f"{_P_LAST}Мониторинг входит в Pro вместе с готовыми текстами исправлений "
            "и шаблонами документов. Для подключения нужно подтвердить, что "
            "сайт ваш: DNS-записью или meta-тегом.</p>"
        ),
        "cta": "Посмотреть, что входит в Pro",
        "cta_path": "/pricing",
    },
    {
        "step": 8,
        "subject": "Закрыть найденное: самим за 990 ₽ или руками юриста за 8 000 ₽",
        "body": (
            f"{_P}За семь писем мы разобрали главное: баннер, политику, "
            "зарубежные счётчики, рекламу и поломки после обновлений. Осталось "
            "закрыть то, что нашлось на вашем сайте. Вариантов два.</p>"
            f"{_P}<b>Pro, 990 ₽ за месяц</b>, без автопродления. Правки вносите "
            "сами: открываются все тексты «Как исправить» под находки отчёта, "
            "шаблоны Политики, согласий и уведомления в РКН, еженедельный "
            "мониторинг.</p>"
            f"{_P_LAST}<b>Документы под сайт, 8 000 ₽ разово.</b> Работу делает "
            "юрист: читает ваш отчёт, собирает документы под ваши формы, "
            "подписывает PDF-заключение и пишет список правок для верстальщика. "
            "Срок – 5 рабочих дней.</p>"
        ),
        "cta": "Сравнить варианты",
        "cta_path": "/pricing",
    },
]


def _personal_intro(scan: Scan) -> str:
    """Абзац про сайт подписчика для письма-оффера. Пусто, если говорить не о чем."""
    problems = [f for f in scan.findings if f.severity != "ok"]
    if scan.status != "done" or not problems:
        return ""
    host = urlparse(scan.url).netloc or scan.url
    host = host.removeprefix("www.")
    n = len(problems)
    line = (f"На {html.escape(host)} проверка нашла {n} "
            f"{gating.plural(n, 'нарушение', 'нарушения', 'нарушений')}.")
    locked = gating.locked_fix_count(scan.findings)
    if locked:
        line += (f" Под замком – {locked} "
                 f"{gating.plural(locked, 'готовый текст', 'готовых текста', 'готовых текстов')}"
                 " «Как исправить».")
    return f"{_P}{line}</p>"


def _render_email(step: int, unsub_token: str,
                  scan: Scan | None = None) -> tuple[str, str, str]:
    """Возвращает (subject, html_body, text_body) для данного шага.

    `scan` — отчёт подписчика, если он пришёл с отчёта. Используется только
    в письме-оффере: цифры по его сайту и ссылка на /pricing?scan=, где
    страница сама выберет главный вариант под этот отчёт.
    """
    data = _EMAILS[step - 1]
    subject = data["subject"]
    content = f"email{step}"
    cta_path = data["cta_path"]
    if scan is not None and step == TOTAL_STEPS and (intro := _personal_intro(scan)):
        data = {**data, "body": intro + data["body"]}
        cta_path = f"{cta_path}?scan={scan.id}"
    cta_url = _utm(f"{_base_url()}{cta_path}", content)
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
    – Максим Подольский, LawCheck<br>
    Вы получили это письмо, потому что оставили email на LawCheck —
    для отчёта по сайту или для образца документа.
    <a href="{unsub}" style="color:#94A3B8">Отписаться</a>.
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""

    # --- text/plain ---
    # Абзацы и пункты списка — отдельными строками, иначе текстовая версия
    # склеивает всё письмо в одну строку.
    text_body = data["body"].replace("</p>", "\n\n").replace("<li>", "– ").replace("</li>", "\n").replace("</ol>", "\n")
    text_body = re.sub(r"<[^>]+>", "", text_body).strip()
    text_body = f"{subject}\n\n{text_body}\n\n{data['cta']}: {cta_url}\n\n– Максим Подольский, LawCheck\nВы получили это письмо, потому что оставили email на LawCheck.\nОтписаться: {unsub}"

    return subject, html_body, text_body


def send_one(sub: NurtureSubscriber) -> bool:
    """Отправить текущий шаг подписчику. True — если ушло."""
    scan = None
    if sub.step == TOTAL_STEPS:
        scan_id = repo.latest_report_scan_id(sub.email)
        scan = repo.get_scan(scan_id) if scan_id else None
    subject, html_body, text_body = _render_email(sub.step, sub.unsub_token, scan)
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
