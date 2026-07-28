"""Юниты уведомлений в Telegram (notify/telegram.py)."""
from unittest import mock

from lawcheck.notify import telegram


def test_not_configured_is_noop(monkeypatch):
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "")
    monkeypatch.setattr(telegram.settings, "telegram_owner_chat_id", "")
    assert telegram.is_configured() is False
    with mock.patch("httpx.post") as p:
        telegram.notify_owner("привет")  # не должно ходить в сеть
        p.assert_not_called()


def test_configured_sends_message(monkeypatch):
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "T")
    monkeypatch.setattr(telegram.settings, "telegram_owner_chat_id", "42")
    assert telegram.is_configured() is True
    with mock.patch("httpx.post") as p:
        telegram.notify_owner("важное")
        p.assert_called_once()
        kwargs = p.call_args.kwargs
        assert kwargs["json"]["chat_id"] == "42"
        assert kwargs["json"]["text"] == "важное"


def test_esc_ekraniruet_polzovatelskie_dannye():
    assert telegram.esc("a<b@x.ru") == "a&lt;b@x.ru"
    assert telegram.esc("<b>жирный</b>") == "&lt;b&gt;жирный&lt;/b&gt;"
    assert telegram.esc(None) == ""
    assert telegram.esc(42) == "42"


def test_email_s_uglovoy_skobkoy_ne_lomaet_razmetku(monkeypatch):
    """Сообщения уходят с parse_mode=HTML: `<` в чужой строке даёт 400, ошибка
    глушится в лог — и уведомление об оплате просто исчезает."""
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "T")
    monkeypatch.setattr(telegram.settings, "telegram_owner_chat_id", "42")
    zlodey = "<script>alert(1)</script>@x.ru"
    with mock.patch("httpx.post") as p:
        telegram.notify_owner(f"Покупатель: <b>{telegram.esc(zlodey)}</b>")
        text = p.call_args.kwargs["json"]["text"]
    # Разметка сообщения цела, чужие теги обезврежены.
    assert text.startswith("Покупатель: <b>") and text.endswith("</b>")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_send_error_swallowed(monkeypatch):
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "T")
    monkeypatch.setattr(telegram.settings, "telegram_owner_chat_id", "42")
    with mock.patch("httpx.post", side_effect=RuntimeError("network down")):
        telegram.notify_owner("упадёт молча")  # не должно бросать
