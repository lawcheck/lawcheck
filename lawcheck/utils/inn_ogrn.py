"""Валидация контрольных сумм ИНН и ОГРН/ОГРНИП.

Алгоритмы стандартные, описаны в Приказе МНС от 03.03.2004 № БГ-3-09/178
и в Постановлении Правительства РФ № 438 (для ОГРН).
"""
import re

_DIGITS = re.compile(r"^\d+$")

# Коэффициенты для ИНН 10 знаков (юр.лица)
_INN10_COEFS = (2, 4, 10, 3, 5, 9, 4, 6, 8)
# Коэффициенты для ИНН 12 знаков (ИП/физлица) — две контрольные цифры
_INN12_COEFS_11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_COEFS_12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


def is_valid_inn(inn: str) -> bool:
    if not inn or not _DIGITS.match(inn):
        return False
    if len(inn) == 10:
        s = sum(int(inn[i]) * _INN10_COEFS[i] for i in range(9))
        return s % 11 % 10 == int(inn[9])
    if len(inn) == 12:
        n11 = sum(int(inn[i]) * _INN12_COEFS_11[i] for i in range(10)) % 11 % 10
        n12 = sum(int(inn[i]) * _INN12_COEFS_12[i] for i in range(11)) % 11 % 10
        return n11 == int(inn[10]) and n12 == int(inn[11])
    return False


def inn_kind(inn: str) -> str:
    """'legal' для 10-значного, 'individual' для 12-значного, '' если невалиден."""
    if not is_valid_inn(inn):
        return ""
    return "legal" if len(inn) == 10 else "individual"


def is_valid_ogrn(ogrn: str) -> bool:
    """ОГРН (13 знаков) и ОГРНИП (15 знаков)."""
    if not ogrn or not _DIGITS.match(ogrn):
        return False
    if len(ogrn) == 13:
        return int(ogrn[:12]) % 11 % 10 == int(ogrn[12])
    if len(ogrn) == 15:
        return int(ogrn[:14]) % 13 % 10 == int(ogrn[14])
    return False


def ogrn_kind(ogrn: str) -> str:
    """'legal' для 13-значного, 'individual' для 15-значного."""
    if not is_valid_ogrn(ogrn):
        return ""
    return "legal" if len(ogrn) == 13 else "individual"
