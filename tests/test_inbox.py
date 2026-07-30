"""Разбор входящих: вывод IMAP-хоста, формат алерта, дедуп через \\Seen."""
import pytest

from lawcheck.config import settings
from lawcheck.notify import inbox, telegram


@pytest.fixture(autouse=True)
def mail_settings(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.timeweb.ru")
    monkeypatch.setattr(settings, "imap_host", "")
    monkeypatch.setattr(settings, "smtp_user", "noreply@lawchek.ru")
    monkeypatch.setattr(settings, "smtp_password", "secret")
    monkeypatch.setattr(settings, "telegram_owner_chat_id", "42")


def test_host_derived_from_smtp():
    assert inbox._host() == "imap.timeweb.ru"


def test_explicit_imap_host_wins(monkeypatch):
    monkeypatch.setattr(settings, "imap_host", "mail.example.com")
    assert inbox._host() == "mail.example.com"


def test_not_configured_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "smtp_password", "")
    assert inbox.is_configured() is False


def test_bounce_detected_by_sender():
    assert inbox._is_bounce("Mail Delivery System <Mailer-Daemon@timeweb.ru>")
    assert not inbox._is_bounce("Елена <elena319@list.ru>")


def test_bounce_and_reply_labelled_differently():
    bounce = inbox._format("Mailer-Daemon@timeweb.ru", "delivery failed", "")
    reply = inbox._format("Елена <elena319@list.ru>", "Re: отчёт", "")
    assert "не доставлено" in bounce
    assert "Ответ на письмо" in reply


def test_subject_is_escaped():
    """Чужая тема с `<` уходит в Telegram с parse_mode=HTML: без esc() это 400
    и потерянное уведомление."""
    text = inbox._format("a@b.ru", "<b>жирная тема</b>", "")
    assert "<b>" not in text
    assert "&lt;b&gt;" in text


class _FakeIMAP:
    """Минимальный IMAP: одно непрочитанное письмо, запоминает выставленные флаги."""

    def __init__(self, *a, **kw):
        self.stored: list[tuple] = []
        self.readonly = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        return "OK", [b""]

    def select(self, box, readonly=False):
        self.readonly = readonly
        return "OK", [b"1"]

    def search(self, charset, criteria):
        return "OK", [b"1"]

    def fetch(self, msg_id, parts):
        raw = (b"From: Elena <elena319@list.ru>\r\n"
               b"Subject: Re: report\r\n"
               b"Date: Thu, 30 Jul 2026 15:00:00 +0300\r\n\r\n")
        return "OK", [(b"1", raw)]

    def store(self, msg_id, cmd, flags):
        self.stored.append((msg_id, cmd, flags))
        return "OK", [b""]


def test_seen_set_only_after_successful_notify(monkeypatch):
    fake = _FakeIMAP()
    monkeypatch.setattr(inbox.imaplib, "IMAP4_SSL", lambda *a, **kw: fake)
    monkeypatch.setattr(telegram, "send_message", lambda chat, text: True)

    summary = inbox.run()

    assert summary["notified"] == 1
    assert fake.stored == [(b"1", "+FLAGS", "\\Seen")]


def test_failed_notify_leaves_message_unread(monkeypatch):
    """Упавший Telegram не должен съедать входящие: без пометки письмо
    достанется следующему прогону."""
    fake = _FakeIMAP()
    monkeypatch.setattr(inbox.imaplib, "IMAP4_SSL", lambda *a, **kw: fake)
    monkeypatch.setattr(telegram, "send_message", lambda chat, text: False)

    summary = inbox.run()

    assert summary["notified"] == 0
    assert summary["skipped"] == 1
    assert fake.stored == []


def test_dry_run_touches_nothing(monkeypatch):
    fake = _FakeIMAP()
    monkeypatch.setattr(inbox.imaplib, "IMAP4_SSL", lambda *a, **kw: fake)
    monkeypatch.setattr(telegram, "send_message",
                        lambda chat, text: pytest.fail("dry-run не должен слать"))

    summary = inbox.run(dry_run=True)

    assert summary["notified"] == 0
    assert fake.stored == []
    assert fake.readonly is True


def test_skips_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "imap_host", "")
    summary = inbox.run()
    assert summary == {"seen": 0, "notified": 0, "skipped": 0, "dry_run": False}
