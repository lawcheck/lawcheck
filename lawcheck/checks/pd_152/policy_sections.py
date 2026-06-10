"""A3 — Политика содержит обязательные разделы (ст. 14, 18.1 ч. 2 152-ФЗ).

Для каждого раздела из dictionaries/policy_sections.yaml считаем
вхождения strong и weak маркеров в нормализованном тексте Политики.
Раздел считается присутствующим, если найден хотя бы один strong-маркер
ИЛИ не менее двух weak-маркеров.

Возвращаем по одному Finding на каждый раздел — это даёт UI готовый
чек-лист. Раздел может пропускаться, только если предыдущая проверка (A1/A2)
не нашла валидной Политики — тогда A3 возвращает пустой список.
"""
from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.pd_152._policy_finder import find_policy_links, find_policy_page
from lawcheck.crawler.snapshot import SiteSnapshot
from lawcheck.dictionaries import loader
from lawcheck.utils.text import normalize_ru

CHECK_ID = "A3"
TITLE = "Обязательные разделы Политики обработки ПДн"
MIN_TEXT_LEN = 1500
STRONG_THRESHOLD = 1
WEAK_THRESHOLD = 2

_SEVERITY_MAP = {"critical": Severity.CRITICAL, "warning": Severity.WARNING, "info": Severity.INFO}


def _missing_severity(section: dict) -> Severity:
    return _SEVERITY_MAP.get(section.get("severity_if_missing", "critical"), Severity.CRITICAL)


def _count_hits(text: str, markers: list[str]) -> tuple[int, list[str]]:
    found: list[str] = []
    for m in markers or []:
        if m and m in text:
            found.append(m)
    return len(found), found


class PolicySectionsCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        links = find_policy_links(snapshot)
        if not links:
            return []
        policy_url = links[0][1]
        page = find_policy_page(snapshot, policy_url)
        if page is None or page.error or page.status >= 400:
            return []
        text = normalize_ru(page.text)
        if len(text) < MIN_TEXT_LEN:
            return []

        sections = loader.policy_sections()
        findings: list[Finding] = []
        for key, section in sections.items():
            strong_n, strong_hits = _count_hits(text, section.get("strong", []))
            weak_n, weak_hits = _count_hits(text, section.get("weak", []))
            present = strong_n >= STRONG_THRESHOLD or weak_n >= WEAK_THRESHOLD
            section_title = section.get("title", key)
            law_ref = section.get("law_ref", "152-ФЗ")

            if present:
                hits_sample = (strong_hits + weak_hits)[:2]
                findings.append(Finding(
                    check_id=f"{self.id}.{key}", severity=Severity.OK,
                    title=f"{TITLE}: {section_title}",
                    evidence=f"Раздел присутствует. Найдены формулировки: {', '.join(repr(h) for h in hits_sample)}.",
                    location=policy_url, law_reference=law_ref,
                    extra={"strong_hits": strong_n, "weak_hits": weak_n},
                ))
            else:
                findings.append(Finding(
                    check_id=f"{self.id}.{key}", severity=_missing_severity(section),
                    title=f"{TITLE}: {section_title}",
                    evidence=f"Раздел не найден в тексте Политики (искали по "
                             f"{len(section.get('strong', []))} точным и "
                             f"{len(section.get('weak', []))} косвенным формулировкам).",
                    location=policy_url, law_reference=law_ref,
                    recommendation=f"Добавьте в Политику раздел «{section_title}».",
                    extra={"strong_hits": 0, "weak_hits": weak_n},
                ))

        return findings
