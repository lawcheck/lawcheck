import re

from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.crawler.snapshot import SiteSnapshot
from lawcheck.utils.text import normalize_ru

# Признаки ссылки на Политику обработки ПДн — в тексте якоря или в URL
_POLICY_RE = re.compile(
    r"(политик[аи][^.]{0,40}(персональн|конфиденциальн|приватн))"
    r"|(privacy[-_ ]?polic)"
    r"|(обработк[аеи]\s+персональн)"
    r"|(personal[-_ ]?data)"
    r"|(\bконфиденциальност[ьи]\b)"
    r"|(\bприватност[ьи]\b)"
    r"|(\bprivacy\b)",
    re.I,
)
_POLICY_URL_RE = re.compile(
    r"(privacy|polic[yi]|persdata|persdannye|personal[-_]?data|политик|персональн|konfidencial|confidential|privat)",
    re.I,
)

LAW_REF = "ст. 18.1 ч. 2 152-ФЗ"
TITLE = "Наличие ссылки на Политику обработки ПДн"
CHECK_ID = "A1"


class PolicyPresenceCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        if not snapshot.pages:
            return [Finding(
                check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                evidence="Сайт недоступен — не удалось загрузить ни одной страницы",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Проверьте доступность сайта.",
            )]

        policy_url: str | None = None
        pages_without: list[str] = []

        for page in snapshot.pages:
            if page.error or page.status >= 400:
                continue
            page_has = False
            for link in page.links:
                if _POLICY_RE.search(normalize_ru(link.text)) or _POLICY_URL_RE.search(link.url):
                    page_has = True
                    if not policy_url:
                        policy_url = link.url
                    break
            if not page_has:
                pages_without.append(page.url)

        if not policy_url:
            return [Finding(
                check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                evidence=f"На {len(snapshot.pages)} проверенных страницах не найдено ссылки на Политику обработки ПДн.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Разместите ссылку на Политику обработки персональных данных в подвале сайта так, "
                               "чтобы она была доступна с любой страницы.",
            )]

        if pages_without:
            sample = ", ".join(pages_without[:5])
            extra = f" (и ещё {len(pages_without) - 5})" if len(pages_without) > 5 else ""
            return [Finding(
                check_id=self.id, severity=Severity.WARNING, title=self.title,
                evidence=f"Политика найдена ({policy_url}), но ссылка отсутствует на {len(pages_without)} "
                         f"страницах: {sample}{extra}",
                location=policy_url, law_reference=LAW_REF,
                recommendation="Перенесите ссылку в общий footer, чтобы она присутствовала на всех страницах сайта.",
                extra={"policy_url": policy_url, "pages_without": pages_without},
            )]

        return [Finding(
            check_id=self.id, severity=Severity.OK, title=self.title,
            evidence=f"Ссылка на Политику обработки ПДн найдена на всех проверенных страницах: {policy_url}",
            location=policy_url, law_reference=LAW_REF,
            extra={"policy_url": policy_url},
        )]
