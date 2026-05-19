"""F1 — Договор-оферта / Правила продажи / Пользовательское соглашение.

Применяется только если сайт классифицирован как e-commerce / продажа
услуг (см. _ecommerce.is_ecommerce_site). Для информационных сайтов
оферта может не требоваться.
"""
from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.zozpp._ecommerce import (
    OFERTA_TEXT_RE, OFERTA_URL_RE, find_doc_links, is_ecommerce_site,
)
from lawcheck.crawler.snapshot import SiteSnapshot

CHECK_ID = "F1"
TITLE = "Оферта / Правила продажи"
LAW_REF = "ст. 437, 494 ГК РФ; п. 9 Правил продажи (ПП РФ № 2463)"


class OfertaCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        is_ec, reason = is_ecommerce_site(snapshot)
        if not is_ec:
            return []

        hits = find_doc_links(snapshot, OFERTA_TEXT_RE, OFERTA_URL_RE)
        if not hits:
            return [Finding(
                check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                evidence=f"Сайт классифицирован как продающий ({reason}), но ссылка на "
                         f"оферту / правила продажи / пользовательское соглашение не найдена.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Разместите в подвале сайта ссылку на договор публичной оферты или "
                               "правила продажи. Условия публичной оферты считаются принятыми "
                               "пользователем в момент оформления заказа (ст. 438 ГК).",
            )]

        doc_url = hits[0][1]
        return [Finding(
            check_id=self.id, severity=Severity.OK, title=self.title,
            evidence=f"Ссылка на оферту/правила продажи найдена: {doc_url}",
            location=doc_url, law_reference=LAW_REF,
            extra={"doc_url": doc_url, "pages_with": len({p for p, _ in hits})},
        )]
