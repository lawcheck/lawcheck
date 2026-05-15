from lawcheck.checks.base import Severity
from lawcheck.checks.pd_152.policy_presence import PolicyPresenceCheck
from lawcheck.crawler.snapshot import Link, PageSnapshot, SiteSnapshot


def _page(url: str, links: list[tuple[str, str]]) -> PageSnapshot:
    return PageSnapshot(
        url=url, status=200, links=[Link(url=u, text=t) for u, t in links]
    )


def test_policy_link_found_everywhere():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _page("https://example.com/", [("https://example.com/privacy", "Политика конфиденциальности")]),
        _page("https://example.com/about", [("https://example.com/privacy", "Политика конфиденциальности")]),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.OK


def test_policy_link_missing_on_some_pages():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _page("https://example.com/", [("https://example.com/privacy", "Политика обработки персональных данных")]),
        _page("https://example.com/contacts", [("https://example.com/about", "О нас")]),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.WARNING
    assert "contacts" in f.evidence


def test_policy_link_completely_missing():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _page("https://example.com/", [("https://example.com/about", "О нас")]),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.CRITICAL


def test_policy_detected_by_url_when_text_is_useless():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _page("https://example.com/", [("https://example.com/privacy-policy", "Подробнее")]),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.OK


def test_short_form_konfidencialnost_detected():
    snap = SiteSnapshot(start_url="https://habr.com/", pages=[
        _page("https://habr.com/", [
            ("https://account.habr.com/info/confidential/", "Конфиденциальность"),
        ]),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.OK


def test_no_pages_means_critical():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.CRITICAL
