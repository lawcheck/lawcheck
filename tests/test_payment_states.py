"""Холд не равен деньгам: AUTHORIZED больше не считается оплатой.

Раньше `_PAID_STATUSES` включал AUTHORIZED, то есть доступ выдавался, когда сумма
только захолдирована на карте, а списания не было и холд мог не завершиться.
"""
import pytest

from lawcheck.payments import tochka
from tests.test_tochka_client import _client_returning  # общий фейковый клиент


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(tochka.settings, "tochka_jwt", "jwt")
    monkeypatch.setattr(tochka.settings, "tochka_customer_code", "3000")
    monkeypatch.setattr(tochka.settings, "tochka_base_url", "https://bank.example")


def _state_for(monkeypatch, status: str) -> str:
    monkeypatch.setattr(tochka, "_client",
                        _client_returning({"Data": {"Operation": [{"status": status}]}}))
    return tochka.payment_state("op-1")


def test_approved_eto_oplata(monkeypatch):
    assert _state_for(monkeypatch, "APPROVED") == "paid"
    monkeypatch.setattr(tochka, "_client",
                        _client_returning({"Data": {"Operation": [{"status": "APPROVED"}]}}))
    assert tochka.is_paid("op-1") is True


def test_authorized_eto_ne_oplata_a_ozhidanie(monkeypatch):
    """Ключевая правка: холд не открывает доступ."""
    assert _state_for(monkeypatch, "AUTHORIZED") == "pending"
    monkeypatch.setattr(tochka, "_client",
                        _client_returning({"Data": {"Operation": [{"status": "AUTHORIZED"}]}}))
    assert tochka.is_paid("op-1") is False


@pytest.mark.parametrize("status", ["CREATED", "PENDING", "IN_PROGRESS"])
def test_promezhutochnye_statusy_eto_ozhidanie(monkeypatch, status):
    assert _state_for(monkeypatch, status) == "pending"


@pytest.mark.parametrize("status", ["DECLINED", "REVERSED", "CANCELLED", ""])
def test_otkaznye_statusy_eto_unknown(monkeypatch, status):
    assert _state_for(monkeypatch, status) == "unknown"


def test_oshibka_banka_eto_unknown_a_ne_padenie(monkeypatch):
    monkeypatch.setattr(tochka, "_client", _client_returning({}, status_code=503))
    assert tochka.payment_state("op-1") == "unknown"
    assert tochka.is_paid("op-1") is False


def test_status_chitaetsya_odnim_zaprosom(monkeypatch):
    """payment_state не должен ходить в банк дважды: он на пути клиента."""
    calls = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            calls.append(1)

            class R:
                status_code = 200
                text = ""

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"Data": {"Operation": [{"status": "APPROVED"}]}}
            return R()

    monkeypatch.setattr(tochka, "_client", lambda: FakeClient())
    assert tochka.is_paid("op-1") is True
    assert len(calls) == 1
