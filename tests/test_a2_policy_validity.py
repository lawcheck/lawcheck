from lawcheck.checks.base import Severity
from lawcheck.checks.pd_152.policy_validity import MIN_TEXT_LEN, PolicyValidityCheck
from lawcheck.crawler.snapshot import Link, PageSnapshot, SiteSnapshot

POLICY_URL = "https://example.com/privacy"


def _home(policy_link_text: str = "Политика конфиденциальности") -> PageSnapshot:
    return PageSnapshot(
        url="https://example.com/", status=200,
        links=[Link(url=POLICY_URL, text=policy_link_text)],
    )


def _policy_page(*, status: int = 200, text: str = "x" * (MIN_TEXT_LEN + 1), error: str = "") -> PageSnapshot:
    return PageSnapshot(url=POLICY_URL, status=status, text=text, error=error)


def test_no_finding_when_no_policy_link_at_all():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        PageSnapshot(url="https://example.com/", status=200, links=[]),
    ])
    assert PolicyValidityCheck().run(snap) == []


def test_warning_when_crawler_did_not_visit_policy():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[_home()])
    [f] = PolicyValidityCheck().run(snap)
    assert f.severity == Severity.WARNING
    assert "не успел" in f.evidence


def test_critical_on_404():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _home(), _policy_page(status=404, text=""),
    ])
    [f] = PolicyValidityCheck().run(snap)
    assert f.severity == Severity.CRITICAL
    assert "404" in f.evidence


def test_critical_on_fetch_error():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _home(), _policy_page(status=0, text="", error="timeout"),
    ])
    [f] = PolicyValidityCheck().run(snap)
    assert f.severity == Severity.CRITICAL


def test_warning_when_text_too_short():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _home(), _policy_page(text="Короткая заглушка"),
    ])
    [f] = PolicyValidityCheck().run(snap)
    assert f.severity == Severity.WARNING


def test_ok_when_policy_loaded_and_long_enough():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[_home(), _policy_page()])
    [f] = PolicyValidityCheck().run(snap)
    assert f.severity == Severity.OK
