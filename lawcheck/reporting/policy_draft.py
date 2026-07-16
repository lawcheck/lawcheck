"""Авто-черновик документов под конкретный сайт по фактам скана.

Собирает из находок (их `extra`) факты — оператор (ИНН/ОГРН/имя), URL, категории
собираемых ПДн, сторонние сервисы (RU/зарубежные) — и рендерит:
  - черновик Политики обработки ПДн (структура по 152-ФЗ, факты подставлены,
    владельческие поля помечены [ЗАПОЛНИТЕ: …]);
  - текст согласия у форм под фактические поля сайта.

Это «почти готово», а не болванка: остаётся вписать несколько полей, которые
знает только владелец (цели, сроки хранения, ответственный, контакты).
"""
from __future__ import annotations

import html
from urllib.parse import urlparse

_CATEGORY_RU = {
    "email": "адрес электронной почты",
    "phone": "номер телефона",
    "full_name": "фамилия, имя, отчество",
    "address": "почтовый адрес",
    "birthdate": "дата рождения",
    "passport": "паспортные данные",
    "inn_snils": "ИНН/СНИЛС",
}
_CATEGORY_ORDER = list(_CATEGORY_RU)

_BLANK = '<span class="blank">[ЗАПОЛНИТЕ: {}]</span>'


def _esc(v) -> str:
    return html.escape(str(v or ""))


def extract_facts(scan) -> dict:
    """Структурные факты сайта из находок скана (для авто-черновиков)."""
    facts = {
        "url": scan.url,
        "domain": urlparse(scan.url).netloc.removeprefix("www."),
        "operator_name": None, "inn": None, "ogrn": None,
        "categories": set(),
        "trackers_foreign": [], "trackers_ru": [],
    }
    for f in scan.findings:
        ex = f.extra or {}
        cid = f.check_id
        if cid == "E1.inn" and ex.get("inn"):
            facts["inn"] = ex["inn"]
        elif cid == "E1.ogrn" and ex.get("ogrn"):
            facts["ogrn"] = ex["ogrn"]
        elif cid == "E1.name" and ex.get("names"):
            facts["operator_name"] = ex["names"][0]
        elif cid == "B1" and ex.get("categories"):
            facts["categories"].update(ex["categories"])
        elif cid.startswith("D1.") and "." in cid:
            name = cid.split(".", 1)[1]
            # critical у трекера = зарубежный сервис (трансграничная передача)
            (facts["trackers_foreign"] if f.severity == "critical"
             else facts["trackers_ru"]).append(name)
    facts["categories"] = [c for c in _CATEGORY_ORDER if c in facts["categories"]]
    return facts


def _categories_ru(cats: list[str]) -> str:
    labels = [_CATEGORY_RU.get(c, c) for c in cats]
    return ", ".join(labels) if labels else ""


def _operator_line(facts: dict) -> str:
    name = _esc(facts["operator_name"]) or _BLANK.format("наименование: ООО «…» / ИП …")
    parts = [name]
    parts.append(f"ИНН {_esc(facts['inn'])}" if facts["inn"] else _BLANK.format("ИНН"))
    if facts["ogrn"]:
        parts.append(f"ОГРН/ОГРНИП {_esc(facts['ogrn'])}")
    else:
        parts.append(_BLANK.format("ОГРН/ОГРНИП"))
    return ", ".join(parts)


def render(scan) -> str:
    """Полный самодостаточный HTML: Политика ПДн + текст согласия под сайт."""
    facts = extract_facts(scan)
    op = _operator_line(facts)
    domain = _esc(facts["domain"])

    cats = facts["categories"]
    cats_ru = _categories_ru(cats)
    # к собранным через формы добавляем cookie/IP, если есть трекеры
    tech = " и технические данные (cookie, IP-адрес)" if (
        facts["trackers_foreign"] or facts["trackers_ru"]) else ""
    cats_line = (cats_ru + tech) if cats_ru else (
        _BLANK.format("категории ПДн — сверьтесь с отчётом, блок «Формы»") + tech)

    # Трансграничная передача
    if facts["trackers_foreign"]:
        foreign = ", ".join(_esc(t) for t in facts["trackers_foreign"])
        cross = (f"Оператор использует иностранные сервисы: <strong>{foreign}</strong>. "
                 "Передача данных этим сервисам является трансграничной. Требуется "
                 "уведомление в Роскомнадзор о трансграничной передаче, а для стран без "
                 "адекватной защиты — отдельное согласие субъекта (ст. 12 152-ФЗ).")
    else:
        cross = ("Трансграничная передача персональных данных не осуществляется. "
                 f"{_BLANK.format('если используете иностранные сервисы — перечислите их')}")

    consent_fields = cats_ru or _BLANK.format("перечислите поля формы")

    return _PAGE.format(
        domain=domain, url=_esc(facts["url"]), op=op,
        cats_line=cats_line, cross=cross, consent_fields=consent_fields,
        purposes=_BLANK.format("цели: обработка заявок с сайта; связь с клиентом; "
                               "исполнение договора — перечислите свои"),
        terms=_BLANK.format("срок хранения: до достижения целей / N лет / до отзыва согласия"),
        contacts=_BLANK.format("email и почтовый адрес оператора для обращений субъектов"),
        resp=_BLANK.format("ФИО ответственного за организацию обработки ПДн"),
    )


_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Черновик документов · {domain}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; color: #1a1a1a;
    max-width: 780px; margin: 0 auto; padding: 32px 20px; line-height: 1.55; }}
  h1 {{ font-size: 22px; }} h2 {{ font-size: 17px; margin: 26px 0 8px; }}
  .lead {{ color: #555; font-size: 14px; background: #f0f7f2; border-radius: 8px; padding: 12px 14px; }}
  ol {{ padding-left: 22px; }} ol li {{ margin: 7px 0; }}
  .blank {{ background: #fff3cd; border-radius: 4px; padding: 0 4px; color: #7a5b00; font-weight: 600; }}
  .consent {{ background: #f7f7f7; border-radius: 8px; padding: 14px 16px; margin: 10px 0; }}
  .consent blockquote {{ margin: 8px 0; padding-left: 12px; border-left: 3px solid #ccc; }}
  footer {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid #eee; color: #999; font-size: 12px; }}
</style></head><body>
<h1>Черновик документов для {domain}</h1>
<div class="lead">Собрано автоматически по данным проверки сайта LawCheck: подставлены
факты, которые мы обнаружили. <b>Жёлтым</b> помечено то, что знаете только вы —
впишите. Это черновик; перед публикацией сверьте с юристом. За персональным
заключением под ваш сайт напишите на juristlawer@gmail.com — входит в тариф.</div>

<h2>Политика обработки персональных данных</h2>
<ol>
  <li><strong>Общие положения.</strong> Настоящая Политика принята {op} (далее — Оператор)
    в соответствии со ст. 18.1 152-ФЗ и определяет порядок обработки персональных данных
    посетителей сайта {url}.</li>
  <li><strong>Цели обработки.</strong> {purposes}.</li>
  <li><strong>Правовые основания.</strong> Согласие субъекта (ст. 6 ч. 1 п. 1 152-ФЗ),
    исполнение договора (ст. 6 ч. 1 п. 5), а также иные основания при их наличии.</li>
  <li><strong>Категории субъектов.</strong> Посетители сайта {url}, клиенты и
    представители контрагентов Оператора.</li>
  <li><strong>Категории персональных данных.</strong> Оператор обрабатывает: {cats_line}.</li>
  <li><strong>Способы обработки.</strong> Обработка осуществляется с использованием средств
    автоматизации и без таковых; сбор — через формы на сайте и подключённые сервисы.</li>
  <li><strong>Сроки обработки и хранения.</strong> {terms}.</li>
  <li><strong>Порядок уничтожения.</strong> По достижении целей обработки либо при отзыве
    согласия данные уничтожаются способом, исключающим восстановление.</li>
  <li><strong>Права субъекта.</strong> Субъект вправе получать сведения об обработке, требовать
    уточнения/уничтожения данных, отозвать согласие, обжаловать действия Оператора в
    Роскомнадзор и суд (ст. 14 152-ФЗ).</li>
  <li><strong>Меры безопасности.</strong> Оператор принимает правовые, организационные и
    технические меры защиты ПДн (ст. 19 152-ФЗ): ограничение доступа, защищённое соединение
    (HTTPS) и иные.</li>
  <li><strong>Трансграничная передача.</strong> {cross}</li>
  <li><strong>Контакты Оператора.</strong> По вопросам обработки ПДн: {contacts}.
    Ответственный за организацию обработки: {resp}. Срок ответа — 10 рабочих дней.</li>
</ol>

<h2>Текст согласия для форм сайта</h2>
<div class="consent">
  <p>Разместите у обязательного чекбокса (НЕ отмечен по умолчанию) рядом с каждой формой,
    собирающей ПДн:</p>
  <blockquote>☐ Я даю согласие {op} на обработку моих персональных данных
    ({consent_fields}) в целях {purposes} в соответствии с
    <u>Политикой обработки персональных данных</u>.</blockquote>
  <p>Слова «Политикой обработки персональных данных» — ссылка на вашу Политику.
    Если планируете рассылки — добавьте <b>отдельный</b> второй чекбокс на согласие
    получать рекламно-информационные сообщения (нельзя объединять с первым).</p>
</div>

<footer>Черновик подготовлен автоматически сервисом LawCheck по данным проверки сайта и не
является юридической консультацией. Перед публикацией проверьте с юристом.</footer>
</body></html>"""
