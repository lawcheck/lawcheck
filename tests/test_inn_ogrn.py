from lawcheck.utils.inn_ogrn import inn_kind, is_valid_inn, is_valid_ogrn, ogrn_kind


# Реальные публичные ИНН (взяты из открытых источников: ФНС, СМИ).

class TestINN:
    def test_legal_entity_10_digits_valid(self):
        assert is_valid_inn("7707083893")  # Сбербанк
        assert inn_kind("7707083893") == "legal"

    def test_legal_entity_invalid_checksum(self):
        # испорчена последняя цифра
        assert is_valid_inn("7707083894") is False
        assert inn_kind("7707083894") == ""

    def test_individual_12_digits_valid(self):
        # известный валидный 12-значный (контрольные цифры рассчитаны корректно)
        assert is_valid_inn("500100732259")
        assert inn_kind("500100732259") == "individual"

    def test_invalid_length(self):
        assert is_valid_inn("123") is False
        assert is_valid_inn("12345678901") is False  # 11 цифр

    def test_non_digits(self):
        assert is_valid_inn("77070838AB") is False

    def test_empty(self):
        assert is_valid_inn("") is False


class TestOGRN:
    def test_legal_entity_13_digits_valid(self):
        assert is_valid_ogrn("1027700132195")  # Сбербанк
        assert ogrn_kind("1027700132195") == "legal"

    def test_invalid_checksum(self):
        assert is_valid_ogrn("1027700132190") is False

    def test_invalid_length(self):
        assert is_valid_ogrn("12345") is False
        assert is_valid_ogrn("12345678901234") is False  # 14 цифр

    def test_individual_15_digits_valid(self):
        # ОГРНИП с корректной контрольной цифрой
        assert is_valid_ogrn("304500116000157")
        assert ogrn_kind("304500116000157") == "individual"

    def test_non_digits(self):
        assert is_valid_ogrn("10277001321XY") is False
