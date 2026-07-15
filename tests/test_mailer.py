"""Юниты транзакционной почты (notify/mailer.py)."""
from unittest import mock

from lawcheck.notify import mailer


def test_console_backend_when_not_configured(monkeypatch):
    monkeypatch.setattr(mailer.settings, "smtp_host", "")
    assert mailer.is_configured() is False
    with mock.patch("smtplib.SMTP") as smtp:
        ok = mailer.send_email("u@x.ru", "Тема", "<p>привет</p>")
        assert ok is True          # console-бэкенд «успешен», чтобы поток шёл в dev
        smtp.assert_not_called()   # по сети не ходим


def test_starttls_path_builds_and_sends(monkeypatch):
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(mailer.settings, "smtp_port", 587)
    monkeypatch.setattr(mailer.settings, "smtp_user", "user")
    monkeypatch.setattr(mailer.settings, "smtp_password", "pass")
    monkeypatch.setattr(mailer.settings, "smtp_from", "LawCheck <noreply@lawchek.ru>")
    monkeypatch.setattr(mailer.settings, "smtp_starttls", True)

    with mock.patch("smtplib.SMTP") as smtp:
        srv = smtp.return_value.__enter__.return_value
        ok = mailer.send_email("client@inbox.ru", "Подтверждение", "<p>ссылка</p>",
                               text_body="ссылка")
        assert ok is True
        srv.starttls.assert_called_once()
        srv.login.assert_called_once_with("user", "pass")
        srv.send_message.assert_called_once()
        msg = srv.send_message.call_args.args[0]
        assert msg["To"] == "client@inbox.ru"
        assert msg["Subject"] == "Подтверждение"
        assert msg["From"] == "LawCheck <noreply@lawchek.ru>"
        # multipart: text/plain + text/html
        assert msg.get_content_type() == "multipart/alternative"


def test_port_465_uses_ssl(monkeypatch):
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(mailer.settings, "smtp_port", 465)
    monkeypatch.setattr(mailer.settings, "smtp_user", "")
    with mock.patch("smtplib.SMTP_SSL") as smtp_ssl, mock.patch("smtplib.SMTP") as smtp:
        ok = mailer.send_email("u@x.ru", "Тема", "<p>тело</p>")
        assert ok is True
        smtp_ssl.assert_called_once()
        smtp.assert_not_called()


def test_smtp_error_returns_false(monkeypatch):
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(mailer.settings, "smtp_port", 587)
    with mock.patch("smtplib.SMTP", side_effect=RuntimeError("network down")):
        assert mailer.send_email("u@x.ru", "Тема", "<p>тело</p>") is False


def test_empty_recipient_is_false(monkeypatch):
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.example.com")
    assert mailer.send_email("", "Тема", "<p>тело</p>") is False
