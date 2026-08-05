"""I1 — проверка канала: HTTPS и действительность сертификата.

Раньше бейдж «HTTPS» в отчёте означал только то, что строка адреса начинается
на https:// — а схему подставляли мы сами, и краулер ходил с
ignore_https_errors=True. Сайт с протухшим сертификатом получал зелёную метку.
"""
import pytest

from lawcheck.checks.security import tls
from lawcheck.crawler.snapshot import PageSnapshot, SiteSnapshot


def _snapshot(url: str) -> SiteSnapshot:
    snap = SiteSnapshot(start_url=url)
    snap.pages.append(PageSnapshot(url=url, status=200, title="", text="", html=""))
    return snap


def _run(monkeypatch, url: str, probe: tls.TlsProbe, redirects=True):
    monkeypatch.setattr(tls, "probe_tls", lambda host, port=443: probe)
    monkeypatch.setattr(tls, "http_redirects_to_https", lambda host: redirects)
    return tls.TlsCheck().run(_snapshot(url))


def test_http_eto_kritichno(monkeypatch):
    findings = _run(monkeypatch, "http://example.com/", tls.TlsProbe(ok=True))
    assert [f.severity for f in findings] == ["critical"]
    assert findings[0].extra["https"] is False


def test_ispravnyy_sertifikat_daet_ok(monkeypatch):
    findings = _run(monkeypatch, "https://example.com/",
                    tls.TlsProbe(ok=True, days_left=200))
    assert [f.severity for f in findings] == ["ok"]
    assert findings[0].extra["cert_valid"] is True


@pytest.mark.parametrize("reason", [
    "срок действия сертификата истёк",
    "сертификат выписан на другое имя",
    "сертификат самоподписанный — браузер ему не доверяет",
])
def test_bityy_sertifikat_pri_https_eto_kritichno(monkeypatch, reason):
    findings = _run(monkeypatch, "https://example.com/",
                    tls.TlsProbe(ok=False, reason=reason))
    assert findings[0].severity == "critical"
    assert reason in findings[0].evidence
    assert findings[0].extra["cert_valid"] is False


def test_nedostupnyy_host_eto_info_a_ne_prigovor(monkeypatch):
    """Сайт, не ответивший в момент проверки, не виноват — как в C2."""
    findings = _run(monkeypatch, "https://example.com/",
                    tls.TlsProbe(error="TimeoutError: timed out"))
    assert findings[0].severity == "info"
    assert findings[0].extra["cert_checked"] is False


def test_skoroe_istechenie_preduprezhdaet(monkeypatch):
    findings = _run(monkeypatch, "https://example.com/",
                    tls.TlsProbe(ok=True, days_left=5))
    severities = [f.severity for f in findings]
    assert "warning" in severities and "ok" in severities
    warn = next(f for f in findings if f.severity == "warning")
    assert warn.check_id == "I1.expiry"


def test_http_bez_redirekta_preduprezhdaet(monkeypatch):
    findings = _run(monkeypatch, "https://example.com/",
                    tls.TlsProbe(ok=True, days_left=100), redirects=False)
    ids = {f.check_id for f in findings}
    assert "I1.redirect" in ids


def test_perevod_prichin_openssl():
    assert tls._ru_reason("certificate has expired") == "срок действия сертификата истёк"
    assert "другое имя" in tls._ru_reason("Hostname mismatch, certificate is not valid")
    assert tls._ru_reason("что-то новое") == "что-то новое"


def test_beydzh_v_otchyote_ot_proverki_a_ne_ot_stroki():
    """Ключевая регрессия: https:// в адресе сам по себе бейдж не даёт."""
    from lawcheck.web.report import _channel_secure

    class _F:
        def __init__(self, check_id, extra):
            self.check_id, self.extra = check_id, extra

    class _S:
        def __init__(self, url, findings):
            self.url, self.findings = url, findings

    assert _channel_secure(_S("https://x.ru/", [_F("I1", {"cert_valid": True})])) is True
    assert _channel_secure(_S("https://x.ru/", [_F("I1", {"cert_valid": False})])) is False
    # Скан без I1 (снят до появления проверки) — бейджа нет.
    assert _channel_secure(_S("https://x.ru/", [_F("A1", {})])) is False
