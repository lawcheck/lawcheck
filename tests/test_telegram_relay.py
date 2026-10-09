"""Ретранслятор Bot API за пределами РФ: первая попытка через него, запасной путь — напрямую.

С VPS отвечает один прямой адрес Telegram, и тот теряет соединения. Отказ
ретранслятора при этом не должен ничего ломать: прямой путь остаётся.
"""
import httpx
import pytest

from lawcheck.notify import telegram

_RELAY = "https://10.0.0.1"


class _Resp:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)


@pytest.fixture
def urls(monkeypatch):
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "tok")
    monkeypatch.setattr(telegram.settings, "telegram_relay_url", _RELAY)
    return []


def test_bez_nastroyki_retranslyator_ne_trogaetsya(urls, monkeypatch):
    monkeypatch.setattr(telegram.settings, "telegram_relay_url", "")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: urls.append(url) or _Resp())
    assert telegram.send_message("42", "текст") is True
    assert urls == ["https://api.telegram.org/bottok/sendMessage"]


def test_pervaya_popytka_idyot_cherez_retranslyator(urls, monkeypatch):
    seen: dict = {}

    def post(url, **kw):
        urls.append(url)
        seen.update(kw)
        return _Resp()

    monkeypatch.setattr(httpx, "post", post)
    assert telegram.send_message("42", "текст") is True
    assert urls == [f"{_RELAY}/bottok/sendMessage"]
    # Сертификат самоподписанный: без закреплённой копии токен ушёл бы кому угодно.
    assert seen["verify"].verify_mode.name == "CERT_REQUIRED"
    assert seen["verify"].check_hostname is True


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout("timed out"), _Resp(502), _Resp(403)])
def test_otkaz_retranslyatora_uvodit_na_pryamoy_put(urls, monkeypatch, failure):
    def post(url, **kw):
        urls.append(url)
        if url.startswith(_RELAY):
            if isinstance(failure, Exception):
                raise failure
            return failure
        return _Resp()

    monkeypatch.setattr(httpx, "post", post)
    assert telegram.send_message("42", "текст") is True
    assert urls == [f"{_RELAY}/bottok/sendMessage",
                    "https://api.telegram.org/bottok/sendMessage"]


def test_otkaz_samogo_telegrama_ne_povtoryaetsya_napryamuyu(urls, monkeypatch):
    """400 от Bot API через ретранслятор — ответ Telegram, а не сбой канала."""
    monkeypatch.setattr(httpx, "post", lambda url, **kw: urls.append(url) or _Resp(400))
    assert telegram.send_message("42", "<b>кривая разметка") is False
    assert urls == [f"{_RELAY}/bottok/sendMessage"]
