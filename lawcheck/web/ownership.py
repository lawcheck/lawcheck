"""Подтверждение владения сайтом для Pro-функций (мониторинг, PDF).

Два способа, любой из них достаточен:
1. TXT-запись на зарегистрированном домене:  lawcheck-verify=<token>
2. Meta-тег на главной странице:  <meta name="lawcheck-verify" content="<token>">
"""
import logging
import re
import secrets

import dns.resolver
import httpx
import tldextract

log = logging.getLogger(__name__)

TXT_PREFIX = "lawcheck-verify="


def new_token() -> str:
    return secrets.token_hex(16)


def registered_domain(url: str) -> str:
    ext = tldextract.extract(url)
    return f"{ext.domain}.{ext.suffix}".lower() if ext.suffix else ext.domain.lower()


def _txt_ok(domain: str, token: str) -> bool:
    try:
        answers = dns.resolver.resolve(domain, "TXT", lifetime=6)
    except Exception as e:
        log.info("ownership: TXT %s не получен: %s", domain, e)
        return False
    expected = TXT_PREFIX + token
    for rec in answers:
        txt = b"".join(rec.strings).decode("utf-8", errors="replace").strip().strip('"')
        if txt == expected:
            return True
    return False


def _meta_ok(url: str, token: str) -> bool:
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True,
                      headers={"User-Agent": "LawCheckBot/0.1 (+https://lawchek.ru/bot)"})
    except Exception as e:
        log.info("ownership: главная %s не получена: %s", url, e)
        return False
    if r.status_code >= 400:
        return False
    pattern = (r'<meta[^>]+name=["\']lawcheck-verify["\'][^>]+content=["\']'
               + re.escape(token) + r'["\']')
    # порядок атрибутов может быть любым
    pattern_rev = (r'<meta[^>]+content=["\']' + re.escape(token)
                   + r'["\'][^>]+name=["\']lawcheck-verify["\']')
    html = r.text[:300_000]
    return bool(re.search(pattern, html, re.I) or re.search(pattern_rev, html, re.I))


def check_ownership(url: str, token: str) -> str:
    """Возвращает способ подтверждения ('txt' | 'meta') или '' если не подтверждено."""
    domain = registered_domain(url)
    if domain and _txt_ok(domain, token):
        return "txt"
    if _meta_ok(url, token):
        return "meta"
    return ""
