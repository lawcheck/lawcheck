"""Черновик уведомления в Роскомнадзор об обработке ПДн под конкретный сайт.

Повторяет структуру полей электронной формы уведомления (ст. 22 ч. 3 152-ФЗ):
подставляем факты, которые сканер обнаружил на сайте (реквизиты, категории ПДн,
трансграничная передача), владельческие поля помечаем [ЗАПОЛНИТЕ: …].
Плюс пошаговая инструкция подачи через pd.rkn.gov.ru / Госуслуги.

Тот же принцип «почти готово», что и у policy_draft: остаётся вписать то,
что знает только владелец.
"""
from __future__ import annotations

import html

from lawcheck.reporting.policy_draft import _CATEGORY_RU, extract_facts

_BLANK = '<span class="blank">[ЗАПОЛНИТЕ: {}]</span>'


def _esc(v) -> str:
    return html.escape(str(v or ""))


def _rkn_status(scan) -> str:
    """Статус по реестру операторов из находки C2: ok / problem / unknown."""
    for f in scan.findings:
        if f.check_id == "C2":
            return "ok" if f.severity == "ok" else "problem"
    return "unknown"


def render(scan) -> str:
    facts = extract_facts(scan)
    domain = _esc(facts["domain"])

    name = _esc(facts["operator_name"]) or _BLANK.format("наименование: ООО «…» / ИП …")
    inn = _esc(facts["inn"]) if facts["inn"] else _BLANK.format("ИНН")
    ogrn = _esc(facts["ogrn"]) if facts["ogrn"] else _BLANK.format("ОГРН/ОГРНИП")

    cats = [_CATEGORY_RU.get(c, c) for c in facts["categories"]]
    tech = "cookie, IP-адрес, данные о посещениях" if (
        facts["trackers_foreign"] or facts["trackers_ru"]) else ""
    if cats:
        cats_line = ", ".join(cats) + (f"; технические данные: {tech}" if tech else "")
    elif tech:
        cats_line = (_BLANK.format("категории ПДн из форм — сверьтесь с отчётом, блок «Формы»")
                     + f"; технические данные: {tech}")
    else:
        cats_line = _BLANK.format("категории ПДн — сверьтесь с отчётом, блок «Формы»")

    if facts["trackers_foreign"]:
        foreign = ", ".join(_esc(t) for t in facts["trackers_foreign"])
        cross = (f"<strong>Осуществляется.</strong> На сайте обнаружены иностранные сервисы: "
                 f"<strong>{foreign}</strong> — передача данных им является трансграничной. "
                 "В форме укажите «трансграничная передача осуществляется» и перечислите "
                 f"страны {_BLANK.format('страны по месту серверов сервисов, чаще США')}. "
                 "Для стран, не обеспечивающих адекватную защиту, требуется отдельное "
                 "согласие субъектов (ст. 12 152-ФЗ).")
    else:
        cross = ("Иностранные сервисы на сайте не обнаружены — если не передаёте данные "
                 "за рубеж другими способами, укажите «трансграничная передача не "
                 "осуществляется».")

    status = _rkn_status(scan)
    if status == "ok":
        status_note = ("Наш скан показал, что оператор <strong>уже есть в реестре РКН</strong>. "
                       "Используйте черновик, чтобы сверить актуальность сведений: при "
                       "изменениях (новые цели, трансграничная передача, смена реквизитов) "
                       "оператор обязан подать <strong>информационное письмо об изменениях</strong> "
                       "— не позднее 15-го числа месяца, следующего за месяцем изменений "
                       "(ст. 22 ч. 7 152-ФЗ).")
    elif status == "problem":
        status_note = ("Наш скан <strong>не нашёл оператора в реестре РКН</strong> (или не смог "
                       "проверить). Если уведомление не подавалось — подайте его до начала "
                       "обработки: за работу без уведомления с 30.05.2025 юрлицам и ИП "
                       "(ИП отвечают как юрлица) грозит штраф 100–300 тыс ₽ "
                       "(ч. 10 ст. 13.11 КоАП).")
    else:
        status_note = ("Проверьте себя в реестре операторов: "
                       '<a href="https://lawchek.ru/reestr-rkn">lawchek.ru/reestr-rkn</a>.')

    return _PAGE.format(
        domain=domain, url=_esc(facts["url"]),
        name=name, inn=inn, ogrn=ogrn,
        address=_BLANK.format("юридический адрес оператора"),
        cats_line=cats_line,
        subjects="посетители сайта, клиенты (покупатели/заказчики), представители контрагентов"
                 f" {_BLANK.format('дополните: работники, соискатели — если ведёте их учёт')}",
        purposes=_BLANK.format("цели — дословно из раздела 2 вашей Политики: обработка заявок "
                               "с сайта; связь с клиентом; исполнение договора"),
        legal_basis="ст. 6 ч. 1 п. 1 (согласие субъекта), ст. 6 ч. 1 п. 5 (исполнение "
                    "договора) 152-ФЗ; устав организации"
                    f" {_BLANK.format('сверьте со своей Политикой')}",
        actions="сбор, запись, систематизация, накопление, хранение, уточнение, извлечение, "
                "использование, передача (предоставление, доступ), блокирование, удаление, "
                "уничтожение — с использованием средств автоматизации",
        security="назначен ответственный за организацию обработки ПДн; изданы локальные акты "
                 "(политика, положение об обработке ПДн); ограничен доступ к данным; "
                 "используется защищённое соединение (HTTPS)"
                 f" {_BLANK.format('дополните фактическими мерами; шифровальные средства — да/нет')}",
        resp=_BLANK.format("ФИО ответственного, телефон, email, почтовый адрес"),
        start_date=_BLANK.format("дата начала обработки — обычно дата запуска сайта/деятельности"),
        term=_BLANK.format("условие прекращения: достижение целей обработки / ликвидация оператора"),
        cross=cross,
        db_location=("Российская Федерация "
                     + _BLANK.format("город и наименование хостинг-провайдера, где размещена БД; "
                                     "базы с ПДн граждан РФ обязаны находиться в РФ — ст. 18 ч. 5")),
        status_note=status_note,
    )


_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Черновик уведомления в РКН · {domain}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; color: #1a1a1a;
    max-width: 780px; margin: 0 auto; padding: 32px 20px; line-height: 1.55; }}
  h1 {{ font-size: 22px; }} h2 {{ font-size: 17px; margin: 26px 0 8px; }}
  .lead {{ color: #555; font-size: 14px; background: #f0f7f2; border-radius: 8px; padding: 12px 14px; }}
  .status {{ font-size: 14px; background: #fef7ec; border-radius: 8px; padding: 12px 14px; margin: 14px 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 14px 0; }}
  th, td {{ border: 1px solid #e3e3e3; padding: 9px 12px; text-align: left; vertical-align: top;
    font-size: 14px; }}
  th {{ background: #f7f7f7; width: 38%; font-weight: 600; }}
  .blank {{ background: #fff3cd; border-radius: 4px; padding: 0 4px; color: #7a5b00; font-weight: 600; }}
  ol.steps li {{ margin: 8px 0; }}
  footer {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid #eee; color: #999; font-size: 12px; }}
</style></head><body>
<h1>Уведомление в Роскомнадзор об обработке ПДн — черновик для {domain}</h1>
<div class="lead">Поля ниже повторяют электронную форму уведомления на
<b>pd.rkn.gov.ru</b> (ст. 22 152-ФЗ). Мы подставили факты, обнаруженные при
проверке сайта; <b>жёлтым</b> помечено то, что знаете только вы. Откройте форму
РКН рядом и переносите построчно.</div>
<div class="status">{status_note}</div>

<h2>Поля формы уведомления</h2>
<table>
  <tr><th>Наименование оператора</th><td>{name}</td></tr>
  <tr><th>ИНН</th><td>{inn}</td></tr>
  <tr><th>ОГРН/ОГРНИП</th><td>{ogrn}</td></tr>
  <tr><th>Адрес оператора</th><td>{address}</td></tr>
  <tr><th>Цель обработки</th><td>{purposes}</td></tr>
  <tr><th>Правовое основание</th><td>{legal_basis}</td></tr>
  <tr><th>Категории персональных данных</th><td>{cats_line}</td></tr>
  <tr><th>Категории субъектов</th><td>{subjects}</td></tr>
  <tr><th>Перечень действий с ПДн, способы обработки</th><td>{actions}</td></tr>
  <tr><th>Меры обеспечения безопасности (ст. 18.1, 19)</th><td>{security}</td></tr>
  <tr><th>Ответственный за организацию обработки</th><td>{resp}</td></tr>
  <tr><th>Дата начала обработки</th><td>{start_date}</td></tr>
  <tr><th>Срок или условие прекращения обработки</th><td>{term}</td></tr>
  <tr><th>Трансграничная передача</th><td>{cross}</td></tr>
  <tr><th>Местонахождение баз данных с ПДн граждан РФ</th><td>{db_location}</td></tr>
</table>

<h2>Как подать: 4 шага</h2>
<ol class="steps">
  <li>Откройте <b>pd.rkn.gov.ru → «Реестр операторов» → «Подать уведомление»</b>
    и войдите через Госуслуги (ЕСИА) от имени организации/ИП.</li>
  <li>Заполните форму по таблице выше. Формулировки целей и категорий должны
    <b>дословно совпадать</b> с вашей Политикой обработки ПДн — расхождения
    замечают при проверках.</li>
  <li>Отправьте уведомление электронно (при входе через ЕСИА бумажный дубликат
    не требуется).</li>
  <li>Через несколько рабочих дней проверьте появление записи в реестре:
    <a href="https://lawchek.ru/reestr-rkn">lawchek.ru/reestr-rkn</a> — по ИНН.
    Наш еженедельный мониторинг тоже это увидит (проверка C2).</li>
</ol>

<footer>Черновик подготовлен автоматически сервисом LawCheck по данным проверки
сайта {url} и не является юридической консультацией. Перед подачей сверьте с
Политикой обработки ПДн и, при сомнениях, с юристом.</footer>
</body></html>"""
