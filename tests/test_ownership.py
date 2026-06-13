"""Юниты модуля подтверждения владения (web/ownership.py)."""
from unittest import mock

import httpx

from lawcheck.web import ownership


def test_registered_domain():
    assert ownership.registered_domain("https://www.mystore.ru/page?x=1") == "mystore.ru"
    assert ownership.registered_domain("http://shop.example.co/") == "example.co"


def test_new_token_unique_and_hex():
    a, b = ownership.new_token(), ownership.new_token()
    assert a != b and len(a) == 32 and all(c in "0123456789abcdef" for c in a)


def test_meta_ok_matches_both_attr_orders():
    token = "abc123"
    html_fwd = f'<head><meta name="lawcheck-verify" content="{token}"></head>'
    html_rev = f'<head><meta content="{token}" name="lawcheck-verify"></head>'
    for html in (html_fwd, html_rev):
        resp = httpx.Response(200, text=html)
        with mock.patch("httpx.get", return_value=resp):
            assert ownership._meta_ok("https://x.ru/", token) is True


def test_meta_ok_rejects_wrong_token():
    resp = httpx.Response(200, text='<meta name="lawcheck-verify" content="OTHER">')
    with mock.patch("httpx.get", return_value=resp):
        assert ownership._meta_ok("https://x.ru/", "abc123") is False


def test_check_ownership_prefers_txt(monkeypatch):
    monkeypatch.setattr(ownership, "_txt_ok", lambda d, t: True)
    monkeypatch.setattr(ownership, "_meta_ok", lambda u, t: False)
    assert ownership.check_ownership("https://x.ru/", "tok") == "txt"


def test_check_ownership_falls_back_to_meta(monkeypatch):
    monkeypatch.setattr(ownership, "_txt_ok", lambda d, t: False)
    monkeypatch.setattr(ownership, "_meta_ok", lambda u, t: True)
    assert ownership.check_ownership("https://x.ru/", "tok") == "meta"


def test_check_ownership_none_when_neither(monkeypatch):
    monkeypatch.setattr(ownership, "_txt_ok", lambda d, t: False)
    monkeypatch.setattr(ownership, "_meta_ok", lambda u, t: False)
    assert ownership.check_ownership("https://x.ru/", "tok") == ""
