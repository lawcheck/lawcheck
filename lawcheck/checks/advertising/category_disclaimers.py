"""G2 — Обязательные дисклеймеры для определённых категорий товаров.

Если на сайте продаются / упоминаются товары специфических категорий,
закон требует размещения предупреждений:

- БАД (ст. 25 ФЗ № 38-ФЗ + п. 1 ст. 24): «БАД не является лекарственным
  средством», «Имеются противопоказания, проконсультируйтесь со
  специалистом».
- Медицинские услуги (п. 7 ст. 24): «Имеются противопоказания. Необходима
  консультация специалиста».
- Финансовые услуги / кредиты / займы (ст. 28): полная стоимость
  и предупреждения о рисках.

Проверяем: если категория упомянута и нет соответствующего дисклеймера —
WARNING. Полноценная квалификация всё равно требует юриста.
"""
from dataclasses import dataclass

from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.crawler.snapshot import SiteSnapshot
from lawcheck.utils.text import normalize_ru

CHECK_ID = "G2"
TITLE = "Обязательные дисклеймеры по категориям товаров"
LAW_REF = "ст. 24, 25, 28 ФЗ № 38-ФЗ «О рекламе»"


@dataclass
class CategoryRule:
    sub_id: str  # короткий ASCII-id для check_id вида G2.<sub_id>
    name: str
    # Триггер — категория упомянута на сайте (нужен >= 1 strong)
    trigger_strong: list[str]
    # Что должно быть рядом в качестве дисклеймера (нужен >= 1 strong)
    disclaimer_strong: list[str]
    recommendation: str


RULES = [
    CategoryRule(
        sub_id="bad",
        name="БАД",
        trigger_strong=[
            "бад ", "биологически активная добавка", "биодобавк",
            "витаминный комплекс", "пищевая добавка",
        ],
        disclaimer_strong=[
            "не является лекарственным средством",
            "не является лекарством",
            "не является лекарственным препаратом",
        ],
        recommendation="Добавьте обязательную фразу «БАД не является лекарственным средством» "
                       "рядом с любым упоминанием БАД.",
    ),
    CategoryRule(
        sub_id="medical",
        name="медицинские услуги или лекарства",
        trigger_strong=[
            "медицинские услуги", "клиника", "прием врача", "стоматолог",
            "лекарственн", "препарат от",
        ],
        disclaimer_strong=[
            "имеются противопоказания",
            "необходима консультация специалиста",
            "проконсультируйтесь со специалистом",
            "проконсультируйтесь с врачом",
        ],
        recommendation="Добавьте предупреждение «Имеются противопоказания. Необходима консультация "
                       "специалиста».",
    ),
    CategoryRule(
        sub_id="finance",
        name="финансовые услуги, кредиты или займы",
        trigger_strong=[
            "получить кредит", "оформить кредит", "взять займ",
            "потребительский кредит", "микрозайм", "займ онлайн",
            "ипотеч", "автокредит",
        ],
        disclaimer_strong=[
            "полная стоимость", "пск ", "процентная ставка", "годовых",
        ],
        recommendation="При рекламе кредитных продуктов укажите полную стоимость кредита (ПСК), "
                       "процентную ставку и иные существенные условия (ст. 28 ФЗ «О рекламе»).",
    ),
]


def _any_marker(text: str, markers: list[str]) -> str | None:
    for m in markers:
        if m in text:
            return m
    return None


class CategoryDisclaimersCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        # Собираем нормализованный текст со всех страниц одним блоком
        all_text = normalize_ru(" ".join(
            p.text for p in snapshot.pages
            if not p.error and p.status < 400 and p.text
        ))
        if not all_text:
            return []

        findings: list[Finding] = []
        for rule in RULES:
            trigger = _any_marker(all_text, rule.trigger_strong)
            if not trigger:
                continue  # категория не упомянута — правило не применимо

            disclaimer = _any_marker(all_text, rule.disclaimer_strong)
            if disclaimer:
                findings.append(Finding(
                    check_id=f"{self.id}.{rule.sub_id}", severity=Severity.OK, title=f"{TITLE}: {rule.name}",
                    evidence=f"Категория '{rule.name}' упомянута ('{trigger.strip()}') и присутствует "
                             f"соответствующий дисклеймер ('{disclaimer.strip()}').",
                    location=snapshot.start_url, law_reference=LAW_REF,
                ))
            else:
                findings.append(Finding(
                    check_id=f"{self.id}.{rule.sub_id}", severity=Severity.WARNING, title=f"{TITLE}: {rule.name}",
                    evidence=f"Категория '{rule.name}' упомянута ('{trigger.strip()}'), но ни один из "
                             f"требуемых дисклеймеров на сайте не найден.",
                    location=snapshot.start_url, law_reference=LAW_REF,
                    recommendation=rule.recommendation,
                ))
        return findings
