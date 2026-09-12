"""Перебор адресов Telegram должен укладываться в таймаут вызывающего.

Резолвер отдаёт один и тот же адрес по числу записей — на проде это три
одинаковые строки с заблокированным адресом. При пробе в 4 с перебор тратил
12 с только на дубликаты, а у httpx на подключение 10 с: первая отправка
после каждого протухания кеша (10 минут) падала с ConnectTimeout. Отсюда и
бралось «уведомления то доходят, то нет».
"""
import socket

import pytest

from lawcheck import net


@pytest.fixture(autouse=True)
def clean_cache():
    net._tg_ip_cache = None
    net._tg_ip_cached_at = 0.0
    yield
    net._tg_ip_cache = None
    net._tg_ip_cached_at = 0.0


def _fake_dns(monkeypatch, addresses):
    monkeypatch.setattr(
        net, "_orig_getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                                     for ip in addresses])


def test_dubli_iz_dns_otseivayutsya(monkeypatch):
    _fake_dns(monkeypatch, ["149.154.166.110"] * 3)
    candidates = net._telegram_candidates()
    assert candidates.count("149.154.166.110") == 1
    assert len(candidates) == len(set(candidates))


def test_adres_iz_dns_probuetsya_pervym(monkeypatch):
    _fake_dns(monkeypatch, ["1.2.3.4"])
    assert net._telegram_candidates()[0] == "1.2.3.4"


def test_zapasnye_idut_posle_dns(monkeypatch):
    _fake_dns(monkeypatch, ["1.2.3.4"])
    assert net._telegram_candidates()[1:] == net._TELEGRAM_FALLBACK_IPS


def test_upavshiy_rezolver_ne_lomaet_perebor(monkeypatch):
    def boom(*a, **k):
        raise OSError("DNS недоступен")
    monkeypatch.setattr(net, "_orig_getaddrinfo", boom)
    assert net._telegram_candidates() == net._TELEGRAM_FALLBACK_IPS


def test_perebor_ne_probuet_odin_adres_dvazhdy(monkeypatch):
    """Главное: на дубликаты таймаут больше не тратится."""
    _fake_dns(monkeypatch, ["149.154.166.110"] * 3)
    probed: list[str] = []

    def fake_conn(addr, timeout=None):
        probed.append(addr[0])
        if addr[0] == "149.154.167.220":
            class S:
                def close(self): pass
            return S()
        raise OSError("blocked")

    monkeypatch.setattr(socket, "create_connection", fake_conn)
    assert net._pick_telegram_ip() == "149.154.167.220"
    assert probed == ["149.154.166.110", "149.154.167.220"]


def test_hudshiy_sluchay_ukladyvaetsya_v_taymaut_httpx(monkeypatch):
    """Даже если не отвечает ни один адрес, перебор короче 10 с у httpx."""
    _fake_dns(monkeypatch, ["149.154.166.110"] * 3)
    worst = len(net._telegram_candidates()) * net._TG_PROBE_TIMEOUT_SEC
    assert worst < 10


def test_naydennyy_adres_kesniruetsya(monkeypatch):
    _fake_dns(monkeypatch, [])
    calls: list[str] = []

    def fake_conn(addr, timeout=None):
        calls.append(addr[0])

        class S:
            def close(self): pass
        return S()

    monkeypatch.setattr(socket, "create_connection", fake_conn)
    first = net._pick_telegram_ip()
    second = net._pick_telegram_ip()
    assert first == second
    assert len(calls) == 1, "второй вызов обязан брать адрес из кеша"
