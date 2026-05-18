import pytest

from lawcheck.checks.base import Severity
from lawcheck.checks.requisites import egrul_match
from lawcheck.checks.requisites.egrul_match import EgrulMatchCheck
from lawcheck.crawler.snapshot import PageSnapshot, SiteSnapshot
from lawcheck.external.egrul import EgrulLookupResult, EgrulRecord

INN_VALID = "7707083893"  # Сбербанк


def _snap(text: str = f"ИНН {INN_VALID}") -> SiteSnapshot:
    return SiteSnapshot(start_url="https://example.com/", pages=[
        PageSnapshot(url="https://example.com/", status=200, text=text),
    ])


@pytest.fixture
def patched_egrul(monkeypatch):
    def factory(result: EgrulLookupResult):
        monkeypatch.setattr(egrul_match, "lookup_by_inn", lambda inn: result)
    return factory


def test_no_finding_without_inn(patched_egrul):
    patched_egrul(EgrulLookupResult(record=None, error="not_found"))
    assert EgrulMatchCheck().run(_snap("без реквизитов")) == []


def test_egrul_timeout_emits_info(patched_egrul):
    patched_egrul(EgrulLookupResult(record=None, error="timeout"))
    [f] = EgrulMatchCheck().run(_snap())
    assert f.severity == Severity.INFO
    assert "не ответил" in f.evidence


def test_egrul_captcha_emits_info(patched_egrul):
    patched_egrul(EgrulLookupResult(record=None, error="captcha_required"))
    [f] = EgrulMatchCheck().run(_snap())
    assert f.severity == Severity.INFO
    assert "капчу" in f.evidence


def test_egrul_not_found_is_critical(patched_egrul):
    patched_egrul(EgrulLookupResult(record=None, error="not_found"))
    [f] = EgrulMatchCheck().run(_snap())
    assert f.severity == Severity.CRITICAL


def test_egrul_found_with_matching_name_is_ok(patched_egrul):
    patched_egrul(EgrulLookupResult(record=EgrulRecord(
        inn=INN_VALID, ogrn="1027700132195", short_name="ПАО СБЕРБАНК",
        full_name='ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО "СБЕРБАНК РОССИИ"',
        kind="ul", region="Г.Москва", registered_at="16.08.2002",
    )))
    [f] = EgrulMatchCheck().run(_snap(f"ПАО «Сбербанк». ИНН {INN_VALID}"))
    assert f.severity == Severity.OK


def test_egrul_found_but_name_mismatch_is_warning(patched_egrul):
    patched_egrul(EgrulLookupResult(record=EgrulRecord(
        inn=INN_VALID, ogrn="1027700132195", short_name="ПАО СБЕРБАНК",
        full_name="ПАО СБЕРБАНК РОССИИ", kind="ul", region="Г.Москва", registered_at="16.08.2002",
    )))
    [f] = EgrulMatchCheck().run(_snap(f"ООО «Совсем другая компания». ИНН {INN_VALID}"))
    assert f.severity == Severity.WARNING
    assert "не совпадает" in f.evidence
