"""Алерт владельцу не должен пропадать, когда Telegram молчит.

12.09.2026 выяснилось, что api.telegram.org у российского хостера режется по
отдельным IP: DNS отдавал заблокированный адрес, и уведомления уходили в
никуда — ошибка отправки глушится в лог. Telegram остаётся основным каналом,
почта включается только когда он не ответил.
"""
from lawcheck.notify import mailer, telegram


def test_email_not_used_when_telegram_works(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(telegram.settings, "telegram_owner_chat_id", "42")
    monkeypatch.setattr(telegram, "send_message", lambda chat, text: True)
    monkeypatch.setattr(mailer, "send_email", lambda *a, **k: sent.append(a) or True)

    telegram.notify_owner("оплата прошла")

    assert sent == []


def test_email_used_when_telegram_fails(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(telegram.settings, "telegram_owner_chat_id", "42")
    monkeypatch.setattr(telegram, "send_message", lambda chat, text: False)
    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_email", lambda *a, **k: sent.append(a) or True)

    telegram.notify_owner("касса не выписала ссылку")

    assert len(sent) == 1
    to, subject, body = sent[0]
    assert "@" in to
    assert "касса не выписала ссылку" in body


def test_no_recursion_when_both_channels_down(monkeypatch):
    """mailer при сбое SMTP сам зовёт notify_owner — кольцо должно рваться."""
    calls: list[int] = []

    monkeypatch.setattr(telegram.settings, "telegram_owner_chat_id", "42")
    monkeypatch.setattr(telegram, "send_message", lambda chat, text: False)
    monkeypatch.setattr(mailer, "is_configured", lambda: True)

    def failing_send_email(*a, **k):
        calls.append(1)
        # ровно то, что делает mailer, когда SMTP недоступен
        telegram.notify_owner("🔴 SMTP недоступен")
        return False

    monkeypatch.setattr(mailer, "send_email", failing_send_email)

    telegram.notify_owner("касса не выписала ссылку")

    assert len(calls) == 1


def test_no_email_when_owner_chat_not_configured(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(telegram.settings, "telegram_owner_chat_id", "")
    monkeypatch.setattr(mailer, "send_email", lambda *a, **k: sent.append(a) or True)

    telegram.notify_owner("что угодно")

    assert sent == []
