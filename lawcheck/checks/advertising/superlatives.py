"""G1 — Запрещённые сравнительные формулировки в рекламе.

По ст. 5 ч. 3 п. 1 ФЗ «О рекламе» запрещается использование терминов
в превосходной степени («лучший», «самый», «единственный», «№1»,
«первый» и т. п.) без указания конкретного критерия сравнения и
документального подтверждения.

Это эвристика: мы ловим употребления и эмитим WARNING, а не CRITICAL —
финальная квалификация (есть ли подтверждение в виде ссылки на исследование)
требует ручной проверки.
"""
import re

from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.crawler.snapshot import SiteSnapshot
from lawcheck.utils.text import normalize_ru

CHECK_ID = "G1"
TITLE = "Превосходная степень в рекламных утверждениях"
LAW_REF = "ст. 5 ч. 3 п. 1 ФЗ № 38-ФЗ «О рекламе»"

# Слова в превосходной степени. Ищем целиком, без подтипа («самый-самый» считается).
_SUPERLATIVE_RE = re.compile(
    r"(?:"
    r"\bлучш(?:ий|ая|ее|ие)\b|"
    r"\bсам(?:ый|ая|ое|ые)\b|"
    r"\bпервый\s+в\s+(?:рф|россии|мире|стране)\b|"
    r"\bединственн(?:ый|ая|ое|ые)\b|"
    r"\bуникальн(?:ый|ая|ое|ые)\b|"
    r"№\s*1\b|\bномер\s+один\b|#\s*1\b"
    r")",
    re.I,
)

# Подтверждение «по данным…», «согласно исследованию…», ссылка на источник
_DISCLAIMER_RE = re.compile(
    r"(по\s+данным|по\s+результатам|согласно\s+(?:исследован|опрос|рейтинг)|"
    r"источник:|по\s+версии)",
    re.I,
)

MAX_EXAMPLES = 5


class SuperlativesCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        examples: list[tuple[str, str]] = []  # (страница, контекст)
        has_disclaimer = False

        for page in snapshot.pages:
            if page.error or page.status >= 400 or not page.text:
                continue
            text = normalize_ru(page.text)
            if _DISCLAIMER_RE.search(text):
                has_disclaimer = True
            for m in _SUPERLATIVE_RE.finditer(text):
                start = max(0, m.start() - 40)
                end = min(len(text), m.end() + 40)
                ctx = text[start:end].strip()
                examples.append((page.url, ctx))
                if len(examples) >= MAX_EXAMPLES:
                    break
            if len(examples) >= MAX_EXAMPLES:
                break

        if not examples:
            return [Finding(
                check_id=self.id, severity=Severity.OK, title=self.title,
                evidence="Превосходных степеней в рекламных утверждениях не обнаружено.",
                location=snapshot.start_url, law_reference=LAW_REF,
            )]

        sample = "; ".join(f"«…{ctx}…»" for _, ctx in examples[:3])
        sev = Severity.INFO if has_disclaimer else Severity.WARNING
        return [Finding(
            check_id=self.id, severity=sev, title=self.title,
            evidence=f"Найдено {len(examples)} употреблений превосходной степени. Примеры: {sample}" +
                     (" Рядом обнаружены формулировки-подтверждения." if has_disclaimer else
                      " Подтверждающих ссылок (по данным…/исследование…) рядом не обнаружено."),
            location=examples[0][0], law_reference=LAW_REF,
            recommendation="При использовании 'лучший', 'самый', '№1' и т. п. укажите конкретный "
                           "критерий сравнения и источник данных (исследование, рейтинг, опрос). "
                           "Иначе ФАС может квалифицировать рекламу как недостоверную.",
            extra={"examples": [{"page": u, "context": c} for u, c in examples]},
        )]
