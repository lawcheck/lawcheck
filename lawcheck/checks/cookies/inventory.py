"""D1 — Инвентаризация сторонних трекеров.

Для каждого найденного трекера эмитим Finding с severity, выводимой из:
- юрисдикции (ru / foreign),
- ставит ли он PD-идентификаторы,
- риска трансграничной передачи.

Дополнительно — итоговый Finding со сводкой по сайту.
"""
from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.cookies._tracker_matcher import TrackerHit, match_trackers
from lawcheck.crawler.snapshot import SiteSnapshot

CHECK_ID = "D1"
TITLE = "Инвентаризация сторонних трекеров"
LAW_REF_CROSS_BORDER = "ст. 12 152-ФЗ (трансграничная передача)"
LAW_REF_PD = "ст. 3 п. 1, ст. 18.1 152-ФЗ (cookies как ПДн)"


def severity_for(hit: TrackerHit) -> tuple[Severity, str]:
    """Возвращает (severity, law_ref) для конкретного трекера."""
    if hit.jurisdiction == "foreign" and hit.sets_pd_identifiers and hit.cross_border_risk == "high":
        return Severity.CRITICAL, LAW_REF_CROSS_BORDER
    if hit.jurisdiction == "foreign" and hit.sets_pd_identifiers and hit.cross_border_risk == "medium":
        return Severity.WARNING, LAW_REF_CROSS_BORDER
    if hit.jurisdiction == "foreign" and not hit.sets_pd_identifiers:
        return Severity.INFO, LAW_REF_CROSS_BORDER  # CDN, tagmanager без идентификаторов
    if hit.jurisdiction == "ru" and hit.sets_pd_identifiers:
        return Severity.INFO, LAW_REF_PD
    return Severity.INFO, LAW_REF_PD


class TrackersInventoryCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        hits = match_trackers(snapshot)
        if not hits:
            return [Finding(
                check_id=self.id, severity=Severity.OK, title=self.title,
                evidence=f"На {len(snapshot.pages)} проверенных страницах сторонние трекеры из справочника не обнаружены.",
                location=snapshot.start_url, law_reference=LAW_REF_PD,
            )]

        findings: list[Finding] = []

        # Сводка
        foreign_high = sum(1 for h in hits if h.jurisdiction == "foreign" and h.cross_border_risk == "high")
        foreign_total = sum(1 for h in hits if h.jurisdiction == "foreign")
        ru_total = sum(1 for h in hits if h.jurisdiction == "ru")
        summary_severity = Severity.CRITICAL if foreign_high else (Severity.WARNING if foreign_total else Severity.INFO)
        findings.append(Finding(
            check_id=self.id, severity=summary_severity, title=self.title,
            evidence=f"Найдено трекеров: {len(hits)} (зарубежных: {foreign_total}, "
                     f"российских: {ru_total}, высокого риска по трансграничке: {foreign_high}).",
            location=snapshot.start_url, law_reference=LAW_REF_CROSS_BORDER if foreign_total else LAW_REF_PD,
            extra={"foreign_total": foreign_total, "ru_total": ru_total, "foreign_high": foreign_high},
        ))

        # По каждому трекеру
        for hit in sorted(hits, key=lambda h: (h.jurisdiction != "foreign", h.name)):
            severity, law_ref = severity_for(hit)
            evidence = (
                f"{hit.name} (категория: {hit.category}, юрисдикция: {hit.jurisdiction}"
                + (f", риск трансграничной передачи: {hit.cross_border_risk}" if hit.cross_border_risk else "")
                + (", ставит идентификаторы" if hit.sets_pd_identifiers else "")
                + f"). Пример URL: {hit.matched_urls[0]}"
            )
            recommendation = ""
            if severity == Severity.CRITICAL:
                recommendation = (
                    "Подайте в Роскомнадзор уведомление о трансграничной передаче ПДн (форма на pd.rkn.gov.ru) "
                    "ИЛИ замените зарубежный сервис аналогом с инфраструктурой в РФ "
                    "(Яндекс.Метрика, Top.Mail.Ru вместо Google Analytics; Яндекс SmartCaptcha вместо reCAPTCHA)."
                )
            elif severity == Severity.WARNING:
                recommendation = "Убедитесь, что подано уведомление РКН о трансграничной передаче, и упомяните сервис в Политике."
            findings.append(Finding(
                check_id=f"{self.id}.{hit.name}", severity=severity, title=self.title,
                evidence=evidence, location=hit.matched_urls[0], law_reference=law_ref,
                recommendation=recommendation,
                extra={
                    "category": hit.category, "jurisdiction": hit.jurisdiction,
                    "cross_border_risk": hit.cross_border_risk,
                    "sets_pd_identifiers": hit.sets_pd_identifiers,
                    "match_count": len(hit.matched_urls),
                },
            ))

        return findings
