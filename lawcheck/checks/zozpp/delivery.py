"""F2 — Информация о доставке.

По п. 9 Правил продажи (ПП РФ № 2463) и ст. 10 ЗОЗПП продавец обязан
проинформировать потребителя о сроках и способах доставки.
"""
from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.zozpp._ecommerce import (
    DELIVERY_TEXT_RE, DELIVERY_URL_RE, find_doc_links, is_ecommerce_site,
)
from lawcheck.crawler.snapshot import SiteSnapshot

CHECK_ID = "F2"
TITLE = "Информация о доставке"
LAW_REF = "ст. 10 ЗОЗПП; п. 9 Правил продажи (ПП РФ № 2463)"


class DeliveryCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        is_ec, _ = is_ecommerce_site(snapshot)
        if not is_ec:
            return []

        hits = find_doc_links(snapshot, DELIVERY_TEXT_RE, DELIVERY_URL_RE)
        if not hits:
            return [Finding(
                check_id=self.id, severity=Severity.WARNING, title=self.title,
                evidence="На сайте не найдена страница с информацией о доставке.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Разместите страницу «Доставка» со сроками, способами и стоимостью.",
            )]
        return [Finding(
            check_id=self.id, severity=Severity.OK, title=self.title,
            evidence=f"Найдена страница доставки: {hits[0][1]}",
            location=hits[0][1], law_reference=LAW_REF,
        )]
