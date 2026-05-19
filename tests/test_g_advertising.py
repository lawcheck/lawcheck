from lawcheck.checks.advertising.category_disclaimers import CategoryDisclaimersCheck
from lawcheck.checks.advertising.ord_marking import OrdMarkingCheck, _erid_from_url
from lawcheck.checks.advertising.superlatives import SuperlativesCheck
from lawcheck.checks.base import Severity
from lawcheck.crawler.snapshot import Link, NetworkRequest, PageSnapshot, SiteSnapshot


def _snap(text: str = "", links: list[tuple[str, str]] | None = None,
          network: list[str] | None = None) -> SiteSnapshot:
    page = PageSnapshot(
        url="https://x.ru/", status=200, text=text,
        links=[Link(url=u, text=t) for u, t in (links or [])],
        network=[NetworkRequest(url=u, domain="", resource_type="") for u in (network or [])],
    )
    return SiteSnapshot(start_url="https://x.ru/", pages=[page])


# === G1 superlatives ===

def test_g1_no_superlatives_ok():
    [f] = SuperlativesCheck().run(_snap(text="Обычный текст без превосходных степеней"))
    assert f.severity == Severity.OK


def test_g1_finds_samyj_and_warns():
    [f] = SuperlativesCheck().run(_snap(text="Наш сервис — самый удобный на рынке"))
    assert f.severity == Severity.WARNING
    assert "сам" in f.evidence.lower()


def test_g1_disclaimer_downgrades_to_info():
    [f] = SuperlativesCheck().run(_snap(
        text="Лучший банк страны по данным исследования НАФИ 2024"
    ))
    assert f.severity == Severity.INFO


def test_g1_no1_pattern_caught():
    [f] = SuperlativesCheck().run(_snap(text="Мы № 1 в России!"))
    assert f.severity == Severity.WARNING


# === G2 category disclaimers ===

def test_g2_no_findings_when_no_categories_mentioned():
    assert CategoryDisclaimersCheck().run(_snap(text="Обычный сайт без специальных категорий")) == []


def test_g2_bad_without_disclaimer_warns():
    findings = CategoryDisclaimersCheck().run(_snap(
        text="Наша БАД для иммунитета — биологически активная добавка"
    ))
    by_id = {f.check_id: f for f in findings}
    assert "G2.bad" in by_id
    assert by_id["G2.bad"].severity == Severity.WARNING


def test_g2_bad_with_disclaimer_ok():
    findings = CategoryDisclaimersCheck().run(_snap(
        text="Наша БАД для иммунитета. Внимание: не является лекарственным средством."
    ))
    by_id = {f.check_id: f for f in findings}
    assert by_id["G2.bad"].severity == Severity.OK


def test_g2_credit_without_psk_warns():
    findings = CategoryDisclaimersCheck().run(_snap(
        text="Получить кредит онлайн за 5 минут"
    ))
    by_id = {f.check_id: f for f in findings}
    assert by_id["G2.finance"].severity == Severity.WARNING


def test_g2_medical_with_disclaimer_ok():
    findings = CategoryDisclaimersCheck().run(_snap(
        text="Клиника предлагает приём врача. Имеются противопоказания, необходима консультация специалиста."
    ))
    by_id = {f.check_id: f for f in findings}
    assert by_id["G2.medical"].severity == Severity.OK


# === G3 ОРД ===

def test_g3_erid_extraction_from_url():
    assert _erid_from_url("https://x.ru/?erid=ABC123XYZ") == "ABC123XYZ"
    assert _erid_from_url("https://x.ru/?utm=1&erid=Token_42-99") == "Token_42-99"
    assert _erid_from_url("https://x.ru/?erid=ab") is None  # слишком короткий
    assert _erid_from_url("https://x.ru/") is None


def test_g3_no_ads_no_erid_no_finding():
    assert OrdMarkingCheck().run(_snap(text="нет рекламы")) == []


def test_g3_erid_found_info():
    [f] = OrdMarkingCheck().run(_snap(
        links=[("https://promo.ru/?erid=Kra2BUVbk", "перейти")],
    ))
    assert f.severity == Severity.INFO
    assert "1" in f.evidence  # один токен


def test_g3_ads_trackers_without_erid_warning():
    [f] = OrdMarkingCheck().run(_snap(
        network=["https://an.yandex.ru/banner.js"],  # яндекс.директ из trackers.yaml
    ))
    assert f.severity == Severity.WARNING
    assert "erid" in f.evidence.lower()
