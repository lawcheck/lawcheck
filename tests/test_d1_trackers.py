from lawcheck.checks.base import Severity
from lawcheck.checks.cookies._tracker_matcher import (
    has_foreign_pd_trackers, has_pd_identifier_trackers, match_trackers,
)
from lawcheck.checks.cookies.inventory import TrackersInventoryCheck, severity_for
from lawcheck.crawler.snapshot import NetworkRequest, PageSnapshot, SiteSnapshot


def _snap(*request_urls: str) -> SiteSnapshot:
    reqs = [NetworkRequest(url=u, domain=u.split("/")[2] if "://" in u else u, resource_type="script") for u in request_urls]
    return SiteSnapshot(start_url="https://example.com/", pages=[
        PageSnapshot(url="https://example.com/", status=200, network=reqs),
    ])


def test_empty_snapshot_returns_ok():
    [finding] = TrackersInventoryCheck().run(_snap())
    assert finding.severity == Severity.OK


def test_matches_yandex_metrika():
    hits = match_trackers(_snap("https://mc.yandex.ru/metrika/tag.js"))
    assert len(hits) == 1
    assert hits[0].name == "Яндекс.Метрика"
    assert hits[0].jurisdiction == "ru"


def test_matches_ga4_via_path_pattern():
    hits = match_trackers(_snap("https://www.google-analytics.com/g/collect?v=2&tid=G-XXX"))
    assert any(h.name == "Google Analytics 4" for h in hits)


def test_matches_vk_pixel_with_path_pattern():
    # YAML содержит "vk.com/rtrg" — должно сматчиться по пути
    hits = match_trackers(_snap("https://vk.com/rtrg?p=VK-RTRG-123"))
    assert any(h.name == "VK Pixel (ВКонтакте)" for h in hits)


def test_dedup_same_tracker_across_pages():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        PageSnapshot(url="https://example.com/a", status=200, network=[
            NetworkRequest(url="https://mc.yandex.ru/metrika/tag.js", domain="mc.yandex.ru", resource_type="script"),
        ]),
        PageSnapshot(url="https://example.com/b", status=200, network=[
            NetworkRequest(url="https://mc.yandex.ru/watch/12345", domain="mc.yandex.ru", resource_type="image"),
        ]),
    ])
    hits = match_trackers(snap)
    assert len(hits) == 1


def test_severity_ga4_is_critical():
    hits = match_trackers(_snap("https://www.google-analytics.com/g/collect"))
    ga = next(h for h in hits if h.name == "Google Analytics 4")
    sev, _ = severity_for(ga)
    assert sev == Severity.CRITICAL


def test_severity_recaptcha_is_warning():
    # reCAPTCHA: foreign + sets_pd_identifiers + cross_border_risk=medium → WARNING
    hits = match_trackers(_snap("https://www.google.com/recaptcha/api.js"))
    rc = next(h for h in hits if "reCAPTCHA" in h.name)
    sev, _ = severity_for(rc)
    assert sev == Severity.WARNING


def test_severity_cdn_is_info():
    hits = match_trackers(_snap("https://cdn.jsdelivr.net/npm/foo.js"))
    cdn = next(h for h in hits if h.name == "jsDelivr")
    sev, _ = severity_for(cdn)
    assert sev == Severity.INFO


def test_severity_ru_metrika_is_info():
    hits = match_trackers(_snap("https://mc.yandex.ru/metrika/tag.js"))
    sev, _ = severity_for(hits[0])
    assert sev == Severity.INFO


def test_inventory_emits_summary_and_per_tracker():
    findings = TrackersInventoryCheck().run(_snap(
        "https://mc.yandex.ru/metrika/tag.js",
        "https://www.google-analytics.com/g/collect",
        "https://connect.facebook.net/en_US/fbevents.js",
    ))
    # 1 summary + 3 individual
    assert len(findings) == 4
    summary = findings[0]
    assert summary.severity == Severity.CRITICAL  # есть foreign high-risk
    assert "Найдено трекеров: 3" in summary.evidence


def test_helpers_pd_identifier_detection():
    hits = match_trackers(_snap(
        "https://www.google-analytics.com/g/collect",
        "https://cdn.jsdelivr.net/npm/foo.js",
    ))
    assert has_pd_identifier_trackers(hits) is True
    assert has_foreign_pd_trackers(hits) is True
