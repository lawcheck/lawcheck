from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.pd_152._policy_finder import find_policy_links
from lawcheck.crawler.snapshot import SiteSnapshot

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
                evidence="Сайт недоступен — не удалось загрузить ни одной страницы.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Проверьте доступность сайта.",
            )]

        valid_pages = [p for p in snapshot.pages if not p.error and p.status < 400]
        if not valid_pages:
            return [Finding(
                check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                evidence="Все страницы сайта вернули ошибку — проверка невозможна.",
                location=snapshot.start_url, law_reference=LAW_REF,
            )]

        pages_with = find_policy_links(snapshot)
        pages_with_set = {p for p, _ in pages_with}
        pages_without = [p.url for p in valid_pages if p.url not in pages_with_set]

        if not pages_with:
            return [Finding(
                check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                evidence=f"На {len(valid_pages)} проверенных страницах не найдено ссылки на Политику обработки ПДн.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Разместите ссылку на Политику обработки персональных данных в подвале сайта так, "
                               "чтобы она была доступна с любой страницы.",
            )]

        policy_url = pages_with[0][1]
        if pages_without:
            sample = ", ".join(pages_without[:5])
            extra = f" (и ещё {len(pages_without) - 5})" if len(pages_without) > 5 else ""
            return [Finding(
                check_id=self.id, severity=Severity.WARNING, title=self.title,
                evidence=f"Политика найдена ({policy_url}), но ссылка отсутствует на {len(pages_without)} "
                         f"страницах: {sample}{extra}",
                location=policy_url, law_reference=LAW_REF,
                recommendation="Перенесите ссылку в общий footer, чтобы она присутствовала на всех страницах.",
                extra={"policy_url": policy_url, "pages_without": pages_without},
            )]

        return [Finding(
            check_id=self.id, severity=Severity.OK, title=self.title,
            evidence=f"Ссылка на Политику обработки ПДн найдена на всех проверенных страницах: {policy_url}",
            location=policy_url, law_reference=LAW_REF,
            extra={"policy_url": policy_url},
        )]
