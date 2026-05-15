"""A2 — Документ Политики реально доступен и содержит текст.

Проверяем, что страница, на которую ведёт ссылка из A1:
- была успешно загружена краулером (статус < 400);
- содержит достаточно текста (отсекает заглушки «404»/«Coming soon»/пустой PDF).
"""
from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.pd_152._policy_finder import find_policy_links, find_policy_page
from lawcheck.crawler.snapshot import SiteSnapshot

LAW_REF = "ст. 18.1 ч. 2 152-ФЗ"
TITLE = "Политика обработки ПДн доступна и содержит текст"
CHECK_ID = "A2"
MIN_TEXT_LEN = 1500


class PolicyValidityCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        links = find_policy_links(snapshot)
        if not links:
            # Без ссылки на Политику проверять нечего — это уже зафиксировано в A1.
            return []

        policy_url = links[0][1]
        page = find_policy_page(snapshot, policy_url)

        if page is None:
            return [Finding(
                check_id=self.id, severity=Severity.WARNING, title=self.title,
                evidence=f"Краулер не успел загрузить страницу Политики ({policy_url}) "
                         f"в пределах max_pages={len(snapshot.pages)}.",
                location=policy_url, law_reference=LAW_REF,
                recommendation="Увеличьте лимит страниц для проверки или убедитесь, что Политика "
                               "доступна по прямой ссылке.",
            )]

        if page.error or page.status == 0:
            return [Finding(
                check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                evidence=f"Страница Политики недоступна: {page.error or 'таймаут'}.",
                location=policy_url, law_reference=LAW_REF,
                recommendation="Восстановите доступ к документу Политики.",
            )]

        if page.status >= 400:
            return [Finding(
                check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                evidence=f"Страница Политики возвращает HTTP {page.status}.",
                location=policy_url, law_reference=LAW_REF,
                recommendation="Исправьте ссылку на Политику или восстановите страницу.",
            )]

        text_len = len(page.text or "")
        if text_len < MIN_TEXT_LEN:
            return [Finding(
                check_id=self.id, severity=Severity.WARNING, title=self.title,
                evidence=f"Документ Политики слишком короткий ({text_len} символов) — "
                         f"возможно, это заглушка или ссылка ведёт на PDF без текстового слоя.",
                location=policy_url, law_reference=LAW_REF,
                recommendation="Разместите полный текст Политики обработки ПДн в виде HTML-страницы.",
                extra={"text_len": text_len},
            )]

        return [Finding(
            check_id=self.id, severity=Severity.OK, title=self.title,
            evidence=f"Документ Политики доступен (HTTP {page.status}, {text_len} символов).",
            location=policy_url, law_reference=LAW_REF,
            extra={"text_len": text_len},
        )]
