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


def test_cli_entrypoint_forces_ipv4():
    """Контейнер IPv4-only: без force_ipv4 httpx берёт IPv6-адрес
    api.telegram.org и уведомления не уходят никогда. Точка входа поллера
    не проходит через api/main.py, поэтому патч обязан быть в ней самой."""
    import importlib
    import socket

    from lawcheck import net

    socket.getaddrinfo = net._orig_getaddrinfo
    try:
        importlib.reload(importlib.import_module("lawcheck.tools.poll_inbox"))
        assert socket.getaddrinfo is not net._orig_getaddrinfo
    finally:
        net.force_ipv4()


_BOUNCE_RAW = (
    b"From: Mail Delivery System <Mailer-Daemon@timeweb.ru>\r\n"
    b"Subject: Mail delivery failed: returning message to sender\r\n"
    b"Date: Fri, 02 Oct 2026 06:12:05 +0300\r\n"
    b"MIME-Version: 1.0\r\n"
    b'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\r\n\r\n'
    b"--B\r\nContent-Type: text/plain; charset=us-ascii\r\n\r\n"
    b"A message that you sent could not be delivered to one or more of its\r\n"
    b"recipients. This is a permanent error. The following address(es) failed:\r\n\r\n"
    b"  nobody@gmail.com\r\n"
    b"    host gmail-smtp-in.l.google.com [173.194.221.26]\r\n"
    b"    SMTP error from remote mail server after RCPT TO:<nobody@gmail.com>:\r\n"
    b"    550-5.1.1 The email account that you tried to reach does not exist.\r\n\r\n"
    b"%(dsn)s"
    b"--B\r\nContent-Type: message/rfc822\r\n\r\n"
    b"From: noreply@lawchek.ru\r\nTo: nobody@gmail.com\r\n"
    b"Subject: =?utf-8?b?0J/QvtC00YLQstC10YDQttC00LXQvdC40LUgZW1haWw=?=\r\n\r\nbody\r\n"
    b"--B--\r\n"
)
_DSN_PART = (
    b"--B\r\nContent-Type: message/delivery-status\r\n\r\n"
    b"Reporting-MTA: dns; smtp.timeweb.ru\r\n\r\n"
    b"Action: failed\r\nFinal-Recipient: rfc822;nobody@gmail.com\r\nStatus: 5.0.0\r\n"
    b"Diagnostic-Code: smtp; 550 5.1.1 <no> such user\r\n\r\n"
)


def _bounce(dsn: bytes):
    import email
    return inbox._parse_bounce(email.message_from_bytes(_BOUNCE_RAW % {b"dsn": dsn}))


def test_bounce_parsed_from_delivery_status():
    b = _bounce(_DSN_PART)
    assert b.recipient == "nobody@gmail.com"
    assert b.subject == "Подтверждение email"
    assert b.reason == "550 5.1.1 <no> such user"


def test_bounce_reason_falls_back_to_exim_text():
    """Без Diagnostic-Code (получатель не ответил вовсе) причина есть только
    в тексте отбойника."""
    b = _bounce(b"")
    assert b.recipient == "nobody@gmail.com"
    assert b.reason.startswith("host gmail-smtp-in.l.google.com")
    assert "does not exist" in b.reason


def test_bounce_alert_names_letter_and_escapes_reason():
    text = inbox._format("Mailer-Daemon@timeweb.ru", "Mail delivery failed", "", _bounce(_DSN_PART))
    assert "кому: nobody@gmail.com" in text
    assert "письмо: Подтверждение email" in text
    assert "&lt;no&gt;" in text and "<no>" not in text
    assert "тема:" not in text


class _FakeBounceIMAP(_FakeIMAP):
    def fetch(self, msg_id, parts):
        return "OK", [(b"1", _BOUNCE_RAW % {b"dsn": _DSN_PART})]


def test_run_sends_bounce_details(monkeypatch):
    fake = _FakeBounceIMAP()
    out: list[str] = []
    monkeypatch.setattr(inbox.imaplib, "IMAP4_SSL", lambda *a, **kw: fake)
    monkeypatch.setattr(telegram, "send_message", lambda chat, text: out.append(text) or True)

    assert inbox.run()["notified"] == 1
    assert "кому: nobody@gmail.com" in out[0]
    assert "причина: 550 5.1.1" in out[0]
