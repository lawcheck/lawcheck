"""D2 — Cookie-баннер с возможностью отказа.

Логика для MVP (без двойной загрузки страницы):
  - нет баннера + есть PD-трекеры             → CRITICAL
  - баннер без кнопки отказа + PD-трекеры     → CRITICAL
  - баннер с кнопкой отказа, но PD-трекеры
    уже стрельнули до клика                   → WARNING
  - баннер с кнопкой отказа + только OK-трекеры → OK
  - нет баннера + нет PD-трекеров             → OK (нечего отклонять)
"""
from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.cookies._tracker_matcher import (
    has_pd_identifier_trackers, match_trackers,
)
from lawcheck.crawler.snapshot import CookieBanner, SiteSnapshot

CHECK_ID = "D2"
TITLE = "Cookie-баннер с возможностью отказа"
LAW_REF = "ст. 18.1 ч. 1 152-ФЗ; позиция РКН по использованию cookies-идентификаторов"


def find_banner(snapshot: SiteSnapshot) -> tuple[CookieBanner | None, str]:
    """Первый встретившийся cookie-баннер и URL страницы, где он найден."""
    for page in snapshot.pages:
        if page.cookie_banner is not None:
            return page.cookie_banner, page.url
    return None, snapshot.start_url


class CookieBannerCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        banner, banner_url = find_banner(snapshot)
        hits = match_trackers(snapshot)
        has_pd = has_pd_identifier_trackers(hits)

        if banner is None and not has_pd:
            return [Finding(
                check_id=self.id, severity=Severity.OK, title=self.title,
                evidence="Сайт не использует трекеры, требующие согласия. Cookie-баннер не нужен.",
                location=snapshot.start_url, law_reference=LAW_REF,
            )]

        if banner is None and has_pd:
            tracker_names = ", ".join(sorted({h.name for h in hits if h.sets_pd_identifiers})[:5])
            return [Finding(
                check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                evidence=f"Cookie-баннер на сайте не найден, но загружаются трекеры со ставящимися "
                         f"идентификаторами: {tracker_names}. Идентификаторы относятся к ПДн (ст. 3) — "
                         f"требуется получение согласия пользователя.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Добавьте cookie-баннер с кнопками «Принять» и «Отклонить» (или «Только необходимые») "
                               "и не загружайте маркетинговые/аналитические скрипты до получения согласия.",
            )]

        if banner is not None and not banner.has_decline_option and has_pd:
            return [Finding(
                check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                evidence=f"Cookie-баннер найден ({banner_url}), но среди кнопок нет варианта отказа. "
                         f"Кнопки: {', '.join(banner.buttons[:5])}.",
                location=banner_url, law_reference=LAW_REF,
                recommendation="Добавьте кнопку «Отклонить» или «Только необходимые». Согласие должно быть "
                               "результатом активного выбора, а не единственно возможным действием.",
                extra={"buttons": banner.buttons},
            )]

        if banner is not None and banner.has_decline_option and has_pd:
            return [Finding(
                check_id=self.id, severity=Severity.WARNING, title=self.title,
                evidence=f"Cookie-баннер с возможностью отказа найден, но трекеры с идентификаторами "
                         f"загружаются до взаимодействия пользователя с баннером.",
                location=banner_url, law_reference=LAW_REF,
                recommendation="Откладывайте загрузку маркетинговых/аналитических скриптов до клика по «Принять».",
                extra={"buttons": banner.buttons},
            )]

        # banner есть, кнопка отказа есть, PD-трекеров нет
        return [Finding(
            check_id=self.id, severity=Severity.OK, title=self.title,
            evidence=f"Cookie-баннер реализован корректно: есть выбор отказа, "
                     f"трекеры до согласия не запускаются.",
            location=banner_url, law_reference=LAW_REF,
        )]
