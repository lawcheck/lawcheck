"""G3 — Маркировка интернет-рекламы (ОРД-токен erid).

С 01.09.2022 любой рекламный креатив в интернете обязан содержать:
  - токен из реестра ОРД в параметре `erid=...`
  - текстовую пометку «Реклама»
  - указание рекламодателя

Эвристика:
- Считаем все URL (ссылки + сетевые запросы) на сайте, у которых есть
  GET-параметр `erid=`.
- Параллельно через D1-словарь определяем, есть ли на сайте трекеры
  категории `ads`.
- Если ads-трекеры есть, а ни одного `erid=` не найдено — WARNING:
  возможно, реклама на сайте не маркирована.
- Если ads-трекеров нет и erid'ов тоже нет — пропускаем (рекламы нет).
- Если erid'ы найдены — INFO с количеством.
"""
import re
from urllib.parse import parse_qs, urlparse

from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.cookies._tracker_matcher import match_trackers
from lawcheck.crawler.snapshot import SiteSnapshot

CHECK_ID = "G3"
TITLE = "Маркировка интернет-рекламы (токен ОРД)"
LAW_REF = "ст. 18.1 ФЗ № 38-ФЗ «О рекламе» (с 01.09.2022)"

# erid состоит из букв и цифр, обычно >=8 символов
_ERID_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-]{6,}$")


def _erid_from_url(url: str) -> str | None:
    try:
        qs = parse_qs(urlparse(url).query)
    except Exception:
        return None
    for value in qs.get("erid", []):
        if value and _ERID_VALUE_RE.match(value):
            return value
    return None


class OrdMarkingCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        erids: set[str] = set()
        for page in snapshot.pages:
            if page.error or page.status >= 400:
                continue
            for link in page.links:
                v = _erid_from_url(link.url)
                if v:
                    erids.add(v)
            for req in page.network:
                v = _erid_from_url(req.url)
                if v:
                    erids.add(v)

        # Есть ли реклама вообще?
        hits = match_trackers(snapshot)
        ads_trackers = [h for h in hits if h.category == "ads"]

        if erids:
            return [Finding(
                check_id=self.id, severity=Severity.INFO, title=self.title,
                evidence=f"Обнаружено {len(erids)} уникальных токенов ОРД (erid=...). "
                         f"Это признак маркировки рекламы по требованию ФЗ «О рекламе».",
                location=snapshot.start_url, law_reference=LAW_REF,
                extra={"erid_count": len(erids), "examples": list(erids)[:5]},
            )]

        if ads_trackers:
            names = sorted({h.name for h in ads_trackers})
            return [Finding(
                check_id=self.id, severity=Severity.WARNING, title=self.title,
                evidence=f"На сайте обнаружены рекламные системы ({', '.join(names[:5])}), "
                         f"но ни одного токена ОРД (erid=...) не найдено. Если на сайте размещается "
                         f"реклама, она должна быть промаркирована.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Получите токены ОРД через одного из операторов рекламных данных "
                               "(Яндекс ОРД, ВК-Реклама, OZON ОРД и др.) и добавьте erid в URL "
                               "рекламных креативов + пометку «Реклама» рядом с ними.",
            )]

        return []  # рекламы нет — проверять нечего
