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
