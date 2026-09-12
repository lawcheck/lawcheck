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


def test_kazhdyy_adres_probuetsya_odin_raz(monkeypatch):
    """Дубликаты из DNS не должны съедать бюджет повторными пробами."""
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
    assert sorted(probed) == sorted(set(probed)), "каждый адрес ровно один раз"
    assert probed.count("149.154.166.110") == 1


def test_pobezhdaet_pervyy_po_poryadku_a_ne_po_skorosti(monkeypatch):
    """Пробы идут параллельно, но адрес из DNS важнее запасного."""
    _fake_dns(monkeypatch, ["1.2.3.4"])

    class S:
        def close(self): pass

    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout=None: S())
    assert net._pick_telegram_ip() == "1.2.3.4"


def test_perebor_stoit_odnu_probu_a_ne_summu(monkeypatch):
    """Ради этого всё и затевалось: длина списка больше не цена перебора."""
    import time as _t

    _fake_dns(monkeypatch, ["149.154.166.110"])

    def slow_dead(addr, timeout=None):
        _t.sleep(0.3)
        raise OSError("blocked")

    monkeypatch.setattr(socket, "create_connection", slow_dead)
    started = _t.monotonic()
    assert net._pick_telegram_ip() is None
    spent = _t.monotonic() - started
    # Последовательно это стоило бы 0.3 × число кандидатов.
    assert spent < 0.3 * len(net._telegram_candidates()) / 2


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
    probes_after_first = len(calls)
    second = net._pick_telegram_ip()
    assert first == second
    assert len(calls) == probes_after_first, "второй вызов обязан брать адрес из кеша"


# === Повтор при сетевой ошибке ===

def test_povtor_pri_setevoy_oshibke(monkeypatch):
    """DPI роняет часть соединений после рукопожатия — один повтор спасает."""
    import httpx

    from lawcheck.notify import telegram

    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "tok")
    calls: list[int] = []
    resets: list[int] = []
    monkeypatch.setattr(net, "reset_telegram_ip", lambda: resets.append(1))

    class OK:
        def raise_for_status(self): pass

    def flaky(url, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("timed out")
        return OK()

    monkeypatch.setattr(httpx, "post", flaky)
    assert telegram.send_message("42", "текст") is True
    assert len(calls) == 2
    assert len(resets) == 1, "адрес обязан сбрасываться перед повтором"


def test_dve_neudachi_podryad_vozvrashchayut_false(monkeypatch):
    import httpx

    from lawcheck.notify import telegram

    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "tok")
    monkeypatch.setattr(net, "reset_telegram_ip", lambda: None)
    calls: list[int] = []

    def always_fail(url, **kw):
        calls.append(1)
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "post", always_fail)
    assert telegram.send_message("42", "текст") is False
    assert len(calls) == telegram._ATTEMPTS, "фиксированное число попыток, без цикла"


def test_oshibka_razmetki_ne_povtoryaetsya(monkeypatch):
    """400 от Telegram — не сетевая ошибка, повторять её бессмысленно."""
    import httpx

    from lawcheck.notify import telegram

    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "tok")
    calls: list[int] = []

    class Bad:
        def raise_for_status(self):
            raise httpx.HTTPStatusError("400", request=None, response=None)

    def bad_markup(url, **kw):
        calls.append(1)
        return Bad()

    monkeypatch.setattr(httpx, "post", bad_markup)
    assert telegram.send_message("42", "<b>кривая") is False
    assert len(calls) == 1


def test_hudshiy_sluchay_ogranichen():
    """Общий `timeout=8` давал 34 с на проде: фазы отсчитывали лимит заново."""
    from lawcheck.notify import telegram

    t = telegram._TIMEOUT
    # Пробы параллельны, поэтому перебор стоит ОДНУ пробу, а не сумму по списку.
    worst = telegram._ATTEMPTS * (t.connect + net._TG_PROBE_TIMEOUT_SEC)
    assert t.connect <= 5, "подключение упирается в DPI — ждать долго бессмысленно"
    assert worst < 20
