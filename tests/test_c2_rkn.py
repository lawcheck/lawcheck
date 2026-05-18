import pytest

from lawcheck.checks.base import Severity
from lawcheck.checks.requisites import rkn_match
from lawcheck.checks.requisites.rkn_match import RknOperatorCheck
from lawcheck.crawler.snapshot import (
    Form, FormField, NetworkRequest, PageSnapshot, SiteSnapshot,
)
from lawcheck.external.rkn_operators import RknLookupResult, RknOperator, _parse_html

INN_VALID = "7707083893"


def _snap(*, text: str = f"ИНН {INN_VALID}", forms=None, network=None) -> SiteSnapshot:
    page = PageSnapshot(
        url="https://example.com/", status=200, text=text,
        forms=forms or [], network=network or [],
    )
    return SiteSnapshot(start_url="https://example.com/", pages=[page])


def _pd_form() -> Form:
    return Form(action="/x", method="post", page_url="https://example.com/",
                fields=[FormField(name="email", type="email")])


@pytest.fixture
def patched_rkn(monkeypatch):
    def factory(result: RknLookupResult):
        monkeypatch.setattr(rkn_match, "lookup_by_inn", lambda inn: result)
    return factory


# === parser ===

def test_parser_recognizes_not_found_when_no_result_row():
    # Реальный пустой ответ РКН не содержит явного текста, но и нет class='clmn1'.
    html = "<html><body><table><tr><th>Организация</th></tr></table></body></html>"
    res = _parse_html("123", html)
    assert res.not_found is True
    assert res.operator is None


def test_parser_extracts_operator_with_dashed_id():
    html = """
    <table>
      <tr class='clmn1'>
        <td><nobr>77-25-249268</nobr></td>
        <td>
          <a href="?id=77-25-249268">ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ХАБР"</a>
          <br/>ИНН: 7705756279
        </td>
      </tr>
    </table>
    """
    res = _parse_html(INN_VALID, html)
    assert res.operator is not None
    assert res.operator.registry_id == "77-25-249268"
    assert 'ХАБР' in res.operator.name


def test_parser_returns_unparseable_when_marker_present_but_block_malformed():
    # есть class='clmn1', но структура внутри сломана — отдаём error
    html = "<tr class='clmn1'><td>совсем не та структура</td></tr>"
    res = _parse_html("123", html)
    assert res.operator is None
    assert res.not_found is False
    assert res.error == "unparseable_response"


# === C2 check ===

def test_c2_no_finding_without_inn(patched_rkn):
    patched_rkn(RknLookupResult(operator=None, not_found=True))
    assert RknOperatorCheck().run(_snap(text="без реквизитов")) == []


def test_c2_operator_found_is_ok(patched_rkn):
    patched_rkn(RknLookupResult(operator=RknOperator(
        inn=INN_VALID, registry_id="77-12-000001", name="ПАО СБЕРБАНК",
    )))
    [f] = RknOperatorCheck().run(_snap())
    assert f.severity == Severity.OK
    assert "СБЕРБАНК" in f.evidence


def test_c2_timeout_emits_info_not_critical(patched_rkn):
    patched_rkn(RknLookupResult(operator=None, error="timeout"))
    [f] = RknOperatorCheck().run(_snap(forms=[_pd_form()]))
    # даже если есть PD-форма, при ошибке сети не клеймим оператора — INFO
    assert f.severity == Severity.INFO
    assert "вручную" in f.recommendation


def test_c2_not_found_but_pd_required_is_critical(patched_rkn):
    patched_rkn(RknLookupResult(operator=None, not_found=True))
    [f] = RknOperatorCheck().run(_snap(forms=[_pd_form()]))
    assert f.severity == Severity.CRITICAL
    assert "не найден в реестре" in f.evidence


def test_c2_not_found_and_no_pd_processing_is_info(patched_rkn):
    patched_rkn(RknLookupResult(operator=None, not_found=True))
    [f] = RknOperatorCheck().run(_snap())  # без PD-форм и трекеров
    assert f.severity == Severity.INFO
    assert "может не требоваться" in f.evidence


def test_c2_pd_trackers_alone_trigger_registration_requirement(patched_rkn):
    patched_rkn(RknLookupResult(operator=None, not_found=True))
    network = [NetworkRequest(url="https://mc.yandex.ru/metrika/tag.js",
                              domain="mc.yandex.ru", resource_type="script")]
    [f] = RknOperatorCheck().run(_snap(network=network))
    assert f.severity == Severity.CRITICAL
