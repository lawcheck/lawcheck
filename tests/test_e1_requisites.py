from lawcheck.checks.base import Severity
from lawcheck.checks.requisites.extract import extract
from lawcheck.checks.requisites.presence import RequisitesPresenceCheck
from lawcheck.crawler.snapshot import PageSnapshot, SiteSnapshot

# реальные валидные значения — Сбербанк
INN_VALID = "7707083893"
OGRN_VALID = "1027700132195"

# второй валидный ИНН (для теста "несколько разных") — Газпром
INN_VALID_2 = "7736050003"

# валидный, но испорченный → невалидный
INN_INVALID = "7707083894"


def _snap(*page_texts: str) -> SiteSnapshot:
    pages = []
    for i, text in enumerate(page_texts):
        pages.append(PageSnapshot(url=f"https://example.com/p{i}", status=200, text=text))
    return SiteSnapshot(start_url="https://example.com/", pages=pages)


# === extract() ===

def test_extract_finds_inn_with_label():
    req = extract(_snap(f"Контакты. ИНН {INN_VALID}, ОГРН {OGRN_VALID}"))
    assert [h.value for h in req.inn] == [INN_VALID]
    assert [h.value for h in req.ogrn] == [OGRN_VALID]


def test_extract_ignores_naked_numbers_without_label():
    # 13-значное число без префикса ОГРН — не должно вытаскиваться
    req = extract(_snap(f"Артикул товара 1234567890123, заказ {INN_VALID}, телефон +7 999"))
    assert req.ogrn == []
    assert req.inn == []  # рядом нет слова ИНН


def test_extract_handles_punctuation_variants():
    req = extract(_snap(f"ИНН: {INN_VALID}; ОГРН №{OGRN_VALID}"))
    assert [h.value for h in req.inn] == [INN_VALID]
    assert [h.value for h in req.ogrn] == [OGRN_VALID]


def test_extract_extracts_legal_form_name():
    req = extract(_snap('Оператор: ООО «Ромашка», ИНН 7707083893'))
    assert any("Ромашка" in h.value for h in req.legal_names)


def test_extract_deduplicates_via_unique_lists():
    req = extract(_snap(f"ИНН {INN_VALID}", f"ИНН {INN_VALID}"))
    assert req.unique_inns == [INN_VALID]


# === E1 RequisitesPresenceCheck ===

def test_e1_no_inn_critical():
    findings = RequisitesPresenceCheck().run(_snap("О компании. Контакты: г. Москва."))
    by_id = {f.check_id: f for f in findings}
    assert by_id["E1.inn"].severity == Severity.CRITICAL
    assert by_id["E1.ogrn"].severity == Severity.WARNING


def test_e1_valid_inn_and_ogrn_ok():
    findings = RequisitesPresenceCheck().run(_snap(f"ИНН {INN_VALID}, ОГРН {OGRN_VALID}"))
    by_id = {f.check_id: f for f in findings}
    assert by_id["E1.inn"].severity == Severity.OK
    assert by_id["E1.ogrn"].severity == Severity.OK


def test_e1_invalid_inn_checksum_warning():
    findings = RequisitesPresenceCheck().run(_snap(f"ИНН {INN_INVALID}"))
    by_id = {f.check_id: f for f in findings}
    assert by_id["E1.inn"].severity == Severity.WARNING
    assert "контрольной суммы" in by_id["E1.inn"].evidence


def test_e1_conflicting_inns_warning():
    findings = RequisitesPresenceCheck().run(_snap(
        f"Подвал: ИНН {INN_VALID}",
        f"Политика: ИНН {INN_VALID_2}",
    ))
    by_id = {f.check_id: f for f in findings}
    assert by_id["E1.inn"].severity == Severity.WARNING
    assert "несколько разных" in by_id["E1.inn"].evidence


def test_e1_same_inn_on_multiple_pages_is_ok():
    findings = RequisitesPresenceCheck().run(_snap(
        f"Подвал: ИНН {INN_VALID}",
        f"Контакты: ИНН {INN_VALID}",
        f"Политика: ИНН {INN_VALID}",
    ))
    by_id = {f.check_id: f for f in findings}
    assert by_id["E1.inn"].severity == Severity.OK
    assert "3 страницах" in by_id["E1.inn"].evidence


def test_e1_legal_name_info_emitted():
    findings = RequisitesPresenceCheck().run(_snap(
        f'ООО «Тестовая компания». ИНН {INN_VALID}, ОГРН {OGRN_VALID}'
    ))
    by_id = {f.check_id: f for f in findings}
    assert "E1.name" in by_id
    assert by_id["E1.name"].severity == Severity.INFO
