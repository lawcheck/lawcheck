import pytest

from lawcheck.web.routes import _feed_domain_blocked


@pytest.mark.parametrize("domain", [
    "pornhub.com",
    "ru.xvideos.com",
    "xnxx.com",
    "best-casino-online.ru",
    "1xbet.com",
    "fast-loan.ru",
])
def test_blocked_domains(domain):
    assert _feed_domain_blocked(domain) is True


@pytest.mark.parametrize("domain", [
    "example.com",
    "mystore.ru",
    "ppu-system.com",
    "lawcheck.ru",
])
def test_allowed_domains(domain):
    assert _feed_domain_blocked(domain) is False


@pytest.mark.parametrize("domain", [
    "beton-zavod.ru",   # «bet» внутри слова «beton»
    "alphabet.ru",      # «bet» в хвосте слова
    "sexton.ru",        # «sex» внутри фамилии
    "loanda-tur.ru",    # «loan» внутри названия
])
def test_korotkie_slova_ne_lovyatsya_podstrokoy(domain):
    """Подстрочный матч выкидывал из ленты обычные коммерческие сайты —
    ровно ту аудиторию, ради которой лента и существует."""
    assert _feed_domain_blocked(domain) is False


@pytest.mark.parametrize("domain", [
    "sex-shop.ru",
    "bet-city.com",
    "dengi.loan.ru",
])
def test_korotkie_slova_lovyatsya_kak_slovo(domain):
    assert _feed_domain_blocked(domain) is True
