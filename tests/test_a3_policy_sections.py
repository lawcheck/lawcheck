from lawcheck.checks.base import Severity
from lawcheck.checks.pd_152.policy_sections import (
    MIN_TEXT_LEN, PolicySectionsCheck,
)
from lawcheck.crawler.snapshot import Link, NetworkRequest, PageSnapshot, SiteSnapshot
from lawcheck.dictionaries import loader

POLICY_URL = "https://example.com/privacy"


def _snap(policy_text: str, network: list[str] | None = None) -> SiteSnapshot:
    return SiteSnapshot(start_url="https://example.com/", pages=[
        PageSnapshot(url="https://example.com/", status=200,
                     links=[Link(url=POLICY_URL, text="Политика конфиденциальности")],
                     network=[NetworkRequest(url=u, domain="", resource_type="")
                              for u in (network or [])]),
        PageSnapshot(url=POLICY_URL, status=200, text=policy_text),
    ])


def _good_policy_text() -> str:
    """Текст, в котором заведомо есть strong-маркер каждого раздела."""
    sections = loader.policy_sections()
    chunks = []
    for sec in sections.values():
        if sec.get("strong"):
            chunks.append(sec["strong"][0])
    body = ". ".join(chunks)
    # добиваем до MIN_TEXT_LEN
    return (body + " ") * (MIN_TEXT_LEN // max(len(body), 1) + 2)


def test_returns_empty_when_no_policy_link():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        PageSnapshot(url="https://example.com/", status=200, links=[]),
    ])
    assert PolicySectionsCheck().run(snap) == []


def test_returns_empty_when_policy_too_short():
    assert PolicySectionsCheck().run(_snap("Короткая заглушка")) == []


def test_all_sections_ok_on_good_policy():
    findings = PolicySectionsCheck().run(_snap(_good_policy_text()))
    assert findings, "ожидался хотя бы один Finding"
    assert all(f.severity == Severity.OK for f in findings), \
        [f"{f.check_id}={f.severity}" for f in findings if f.severity != Severity.OK]


def test_missing_section_critical():
    # Текст, в котором нет ни одного маркера раздела purposes (цели обработки)
    text = ("оператор персональных данных " * 200)
    findings = PolicySectionsCheck().run(_snap(text))
    by_id = {f.check_id: f for f in findings}
    assert by_id["A3.purposes"].severity == Severity.CRITICAL


def test_optional_section_warning_or_info():
    # security_measures помечен severity_if_missing: warning
    text = "оператор персональных данных " * 200
    findings = PolicySectionsCheck().run(_snap(text))
    by_id = {f.check_id: f for f in findings}
    assert by_id["A3.security_measures"].severity == Severity.WARNING


def test_cross_border_missing_ok_when_no_foreign_trackers():
    # Раздела о трансграничке нет, но и передачи за рубеж нет → OK (не обязателен)
    text = "оператор персональных данных " * 200
    findings = PolicySectionsCheck().run(_snap(text))
    by_id = {f.check_id: f for f in findings}
    assert by_id["A3.cross_border"].severity == Severity.OK
    assert "не обязателен" in by_id["A3.cross_border"].evidence


def test_cross_border_missing_warning_when_foreign_pd_trackers():
    # Google Analytics (foreign + PD-идентификаторы) → раздел обязателен → WARNING
    text = "оператор персональных данных " * 200
    findings = PolicySectionsCheck().run(_snap(
        text, network=["https://www.google-analytics.com/collect?v=1"]))
    by_id = {f.check_id: f for f in findings}
    f = by_id["A3.cross_border"]
    assert f.severity == Severity.WARNING
    assert "Google Analytics" in f.evidence


def test_two_weak_markers_make_section_present():
    sections = loader.policy_sections()
    weak = sections["purposes"]["weak"][:2]
    assert len(weak) >= 2, "тест требует хотя бы 2 weak-маркера"
    # текст без strong, но с двумя weak
    text = " ".join(weak) * 50
    findings = PolicySectionsCheck().run(_snap(text + " " * MIN_TEXT_LEN))
    by_id = {f.check_id: f for f in findings}
    assert by_id["A3.purposes"].severity == Severity.OK
