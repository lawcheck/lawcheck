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
# Порог «это заглушка, а не документ». Обоснование, а не круглое число с потолка:
# минимальная Политика по ст. 18.1 ч. 2 152-ФЗ обязана раскрыть цели обработки,
# категории ПДн и субъектов, способы, сроки и порядок обращения субъекта. Даже
# предельно сухо это ~1,5–2 тыс. знаков; всё, что короче, на практике оказывается
# страницей «Политика конфиденциальности» с одним абзацем или PDF без текстового
# слоя. Порог намеренно занижен: пропустить сомнительный документ дешевле, чем
# обвинить нормальный (см. lawcheck/checks/CLAUDE.md).
MIN_TEXT_LEN = 1500


class PolicyValidityCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        links = find_policy_links(snapshot)
        if not links:
            # Без ссылки на Политику проверять нечего — это уже зафиксировано в A1.
            return []

        # Ссылок на Политику может быть несколько: устаревшая в футере и живая
        # на отдельной странице. Берём ЛУЧШИЙ доступный документ, а не первый
        # попавшийся — иначе вывод зависит от порядка обхода.
        candidates = []
        for _, url in links:
            page = find_policy_page(snapshot, url)
            if page is not None:
                candidates.append((url, page))
        if candidates:
            policy_url, page = max(
                candidates,
                key=lambda cp: (not cp[1].error and cp[1].status < 400, len(cp[1].text or "")),
            )
        else:
            policy_url, page = links[0][1], None

        if page is None:
            if snapshot.budget_reached:
                # Мы не дошли до документа в рамках лимита страниц. Это наше
                # ограничение, а не нарушение на стороне сайта: выдать здесь
                # предупреждение — значит написать клиенту претензию за то,
                # чего мы не проверяли. Молчим, A1 (наличие ссылки) уже сказано.
                return []
            return [Finding(
                check_id=self.id, severity=Severity.WARNING, title=self.title,
                evidence=f"Не удалось загрузить документ Политики ({policy_url}) — "
                         f"он не попал в число проверенных страниц.",
                location=policy_url, law_reference=LAW_REF,
                recommendation="Убедитесь, что Политика открывается по прямой ссылке "
                               "и не закрыта авторизацией.",
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
