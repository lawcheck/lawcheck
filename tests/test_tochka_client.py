"""Клиент эквайринга Точки: неожиданный ответ банка не должен тихо ломать оплату.

Шапка payments/tochka.py прямо говорит, что имена полей выверены по документации,
а боевой сверки ещё не было. Значит «банк ответил не так» — рабочий сценарий,
а не экзотика.
"""
import httpx
import pytest

from lawcheck.payments import tochka


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(tochka.settings, "tochka_jwt", "jwt-token")
    monkeypatch.setattr(tochka.settings, "tochka_customer_code", "3000")
    monkeypatch.setattr(tochka.settings, "tochka_merchant_id", "")
    monkeypatch.setattr(tochka.settings, "tochka_base_url", "https://bank.example")
    monkeypatch.setattr(tochka.settings, "site_base_url", "https://lawchek.ru")


def _client_returning(payload, status_code=200, text=""):
    """Подменяет httpx.Client так, что любой запрос отдаёт заданный ответ."""
    class FakeResponse:
        def __init__(self):
            self.status_code = status_code
            self.text = text or str(payload)

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("err", request=None, response=None)

        def json(self):
            if isinstance(payload, Exception):
                raise payload
            return payload

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return FakeResponse()

        def get(self, *a, **k):
            return FakeResponse()

    return FakeClient


# === create_payment: пустая ссылка недопустима ===

def test_create_payment_vozvrashchaet_ssylku(monkeypatch):
    monkeypatch.setattr(tochka, "_client", _client_returning(
        {"Data": {"operationId": "op-1", "paymentLink": "https://bank.example/pay/1"}}))
    link = tochka.create_payment(amount_rub=990, purpose="Pro", order_id="ord1")
    assert link.operation_id == "op-1"
    assert link.url == "https://bank.example/pay/1"


@pytest.mark.parametrize("payload", [
    {"Data": {}},                                        # полей нет вовсе
    {"Data": {"operationId": "op-1"}},                   # нет ссылки
    {"Data": {"paymentLink": "https://bank/pay"}},       # нет operationId
    {"Data": {"operationId": "", "paymentLink": ""}},    # пустые строки
    {"Result": "ok"},                                    # другая структура
    [],                                                  # вообще не объект
])
def test_create_payment_padaet_vmesto_pustoy_ssylki(monkeypatch, payload):
    """Молча вернуть пустой URL нельзя: клиент уедет в никуда, а заказ навсегда
    останется без operation_id — подтвердить оплату будет уже нечем."""
    monkeypatch.setattr(tochka, "_client", _client_returning(payload))
    with pytest.raises(tochka.TochkaBadResponse):
        tochka.create_payment(amount_rub=990, purpose="Pro", order_id="ord1")


# === is_paid: никогда не бросает ===

def test_is_paid_dlya_approved(monkeypatch):
    monkeypatch.setattr(tochka, "_client", _client_returning(
        {"Data": {"Operation": [{"status": "APPROVED"}]}}))
    assert tochka.is_paid("op-1") is True


def test_is_paid_chitaet_status_bez_vlozhennogo_operation(monkeypatch):
    monkeypatch.setattr(tochka, "_client", _client_returning(
        {"Data": {"status": "approved"}}))
    assert tochka.is_paid("op-1") is True


@pytest.mark.parametrize("payload", [
    {"Data": {"Operation": [{"status": "CREATED"}]}},
    {"Data": {"Operation": []}},
    {"Data": {}},
    {"Data": None},
    {"Result": "ok"},
    "строка вместо объекта",
    [],
])
def test_is_paid_ne_padaet_na_neozhidannom_otvete(monkeypatch, payload):
    """Раньше ловился только httpx.HTTPError, поэтому чужая структура давала 500
    клиенту, который только что заплатил, и вечные ретраи вебхука."""
    monkeypatch.setattr(tochka, "_client", _client_returning(payload))
    assert tochka.is_paid("op-1") is False


def test_is_paid_ne_padaet_na_ne_json(monkeypatch):
    monkeypatch.setattr(tochka, "_client",
                        _client_returning(ValueError("not json"), text="<html>502</html>"))
    assert tochka.is_paid("op-1") is False


def test_is_paid_ne_padaet_na_http_oshibke(monkeypatch):
    monkeypatch.setattr(tochka, "_client", _client_returning({}, status_code=503))
    assert tochka.is_paid("op-1") is False


def test_bez_nastroennogo_ekvayringa_brosaet(monkeypatch):
    monkeypatch.setattr(tochka.settings, "tochka_jwt", "")
    with pytest.raises(tochka.TochkaNotConfigured):
        tochka.create_payment(amount_rub=990, purpose="Pro", order_id="ord1")
    with pytest.raises(tochka.TochkaNotConfigured):
        tochka.get_operation_status("op-1")
