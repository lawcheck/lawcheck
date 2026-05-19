"""F3 — Возврат и обмен.

Ст. 26.1 ЗОЗПП — при дистанционной продаже потребитель вправе отказаться
от товара в течение 7 дней (если информация о возврате на сайте есть).
Если информация о возврате не предоставлена — срок продлевается до 3 месяцев.
"""
from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.zozpp._ecommerce import (
    RETURN_TEXT_RE, RETURN_URL_RE, find_doc_links, is_ecommerce_site,
)
from lawcheck.crawler.snapshot import SiteSnapshot

CHECK_ID = "F3"
TITLE = "Условия возврата и обмена"
LAW_REF = "ст. 26.1 ЗОЗПП"


class ReturnsCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        is_ec, _ = is_ecommerce_site(snapshot)
        if not is_ec:
            return []

        hits = find_doc_links(snapshot, RETURN_TEXT_RE, RETURN_URL_RE)
        if not hits:
            return [Finding(
                check_id=self.id, severity=Severity.WARNING, title=self.title,
                evidence="Не найдена страница с условиями возврата и обмена.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Разместите условия возврата. По ст. 26.1 ЗОЗПП отсутствие "
                               "такой информации даёт покупателю право вернуть товар в течение "
                               "3 месяцев вместо стандартных 7 дней.",
            )]
        return [Finding(
            check_id=self.id, severity=Severity.OK, title=self.title,
            evidence=f"Найдена страница возврата: {hits[0][1]}",
            location=hits[0][1], law_reference=LAW_REF,
        )]
