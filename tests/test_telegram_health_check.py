"""Проверка канала уведомлений по расписанию.

Канал дважды умирал молча: ошибка отправки глушится в лог, а событий, на
которых это вскрылось бы, может не быть неделями. Ручку дёргает хостовый cron
раз в сутки; когда канал не отвечает, алерт уходит через notify_owner и
доезжает почтой, потому что Telegram в этот момент как раз и недоступен.
"""
import pytest
from fastapi.testclient import TestClient

from lawcheck.api.main import app
from lawcheck.config import settings
from lawcheck.notify import telegram

_URL = "/internal/health/telegram"
_KEY = {"X-Internal-Key": "s3cret-key"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "internal_key", "s3cret-key")
    with TestClient(app) as c:
        yield c


def test_ruchka_zakryta_bez_klyucha(client):
    assert client.post(_URL).status_code == 403
    assert client.post(_URL, headers={"X-Internal-Key": "s3cret-ke"}).status_code == 403


def test_zhivoy_kanal_ne_shlyot_alert(client, monkeypatch):
    alerts: list[str] = []
    monkeypatch.setattr(telegram, "check_api", lambda: (True, "LawCheckMonitor_bot"))
    monkeypatch.setattr(telegram, "notify_owner", lambda text: alerts.append(text))

    r = client.post(_URL, headers=_KEY)

    assert r.status_code == 200
    assert r.json() == {"ok": True, "bot": "LawCheckMonitor_bot"}
    assert alerts == []


def test_myortvyy_kanal_shlyot_alert_s_zhivymi_adresami(client, monkeypatch):
    alerts: list[str] = []
    monkeypatch.setattr(telegram, "check_api", lambda: (False, "ConnectError: timed out"))
    monkeypatch.setattr(telegram, "reachable_ips", lambda *a, **k: ["149.154.167.220"])
    monkeypatch.setattr(telegram, "notify_owner", lambda text: alerts.append(text))

    r = client.post(_URL, headers=_KEY)

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["reachable_ips"] == ["149.154.167.220"]
    # В алерте должен быть и диагноз, и адрес, на который менять пин: без него
    # чинить придётся перебором руками.
    assert len(alerts) == 1
    assert "ConnectError" in alerts[0]
    assert "149.154.167.220" in alerts[0]
    assert "extra_hosts" in alerts[0]


def test_kogda_ne_otvechaet_ni_odin_adres(client, monkeypatch):
    alerts: list[str] = []
    monkeypatch.setattr(telegram, "check_api", lambda: (False, "ConnectError: timed out"))
    monkeypatch.setattr(telegram, "reachable_ips", lambda *a, **k: [])
    monkeypatch.setattr(telegram, "notify_owner", lambda text: alerts.append(text))

    r = client.post(_URL, headers=_KEY)

    assert r.json()["reachable_ips"] == []
    assert "ни один из известных" in alerts[0]


def test_check_api_bez_tokena_ne_hodit_v_set(monkeypatch):
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "")
    ok, detail = telegram.check_api()
    assert ok is False
    assert "TELEGRAM_BOT_TOKEN" in detail


def test_spisok_adresov_beryotsya_iz_net(monkeypatch):
    """Свой список кандидатов разъехался бы с тем, по которому идёт выбор."""
    from lawcheck import net

    probed: list[str] = []

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_conn(addr, timeout=None):
        probed.append(addr[0])
        raise OSError("blocked")

    monkeypatch.setattr("socket.create_connection", fake_conn)
    telegram.reachable_ips()

    assert probed == net._TELEGRAM_FALLBACK_IPS
