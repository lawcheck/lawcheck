"""Валидация email — один вариант на весь проект.

Была размазана по трём местам в виде `"@" in email and "." in ...` и пропускала
в том числе перевод строки внутри адреса, а адрес уходит в заголовок To: письма.
"""
import pytest

from lawcheck.utils.email import normalize_email, valid_email


@pytest.mark.parametrize("raw", [
    "user@example.ru",
    "User.Name+tag@sub.example.co.uk",
    "a@b.io",
    "  Spaced@Example.RU  ",
    "very.common@example.com",
])
def test_normalnye_adresa_prohodyat(raw):
    assert valid_email(raw) is True


@pytest.mark.parametrize("raw", [
    "a@b.ru\nBcc: victim@example.com",   # инъекция заголовка письма
    "a@b.ru\r\nSubject: спам",
    "a\tb@example.ru",
    "a b@example.ru",                     # пробел внутри
    "@@@.x",
    "no-at-sign.ru",
    "user@",
    "@example.ru",
    "user@localhost",                     # домен без зоны
    "user@example",                       # то же
    "user@.example.ru",
    "user@example..ru",
    "user..name@example.ru",
    "",
    "   ",
])
def test_musor_otsekaetsya(raw):
    assert valid_email(raw) is False


def test_slishkom_dlinnyy_adres_otsekaetsya():
    assert valid_email("a" * 250 + "@example.ru") is False


def test_normalizaciya_privodit_k_kanonichnomu_vidu():
    assert normalize_email("  User@Example.RU ") == "user@example.ru"
    assert normalize_email(None) == ""


def test_staraya_proverka_propuskala_perevod_stroki():
    """Фиксируем ровно ту дыру, из-за которой всё затевалось."""
    zlodey = "a@b.ru\nBcc: victim@example.com"
    # Старая логика: "@" есть, точка в последнем сегменте есть → пропускала.
    assert "@" in zlodey and "." in zlodey.split("@")[-1]
    assert valid_email(zlodey) is False
