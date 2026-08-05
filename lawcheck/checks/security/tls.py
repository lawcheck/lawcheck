"""I1 — данные с форм уходят по исправному HTTPS.

Раньше «HTTPS» в отчёте означало ровно одно: строка адреса начинается на
`https://`. Причём схему подставляли мы сами — посетитель вводит `example.com`,
форма дописывает `https://`. А краулер ходит с `ignore_https_errors=True`,
чтобы просроченный сертификат не срывал проверку целиком. В сумме сайт с
протухшим, самоподписанным или выписанным на чужое имя сертификатом получал
в отчёте зелёный бейдж «HTTPS».

Для отчёта, которым клиент отчитывается перед собой и проверяющим, это
ложноотрицательный результат ровно там, где ошибка стоит денег: передача ПДн
по неисправному каналу — нарушение мер защиты (ст. 19 152-ФЗ).

Сеть здесь используется так же, как в C2/E2: не ответили — это INFO
«не смогли проверить», а не приговор.
"""
from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.crawler.snapshot import SiteSnapshot

CHECK_ID = "I1"
TITLE = "Защита канала: HTTPS и сертификат"
LAW_REF = "ст. 19 152-ФЗ"

# За сколько дней до конца срока начинаем предупреждать. Две недели — это
# и запас на ручное продление, и вдвое больше типичного окна автопродления.
_EXPIRY_WARN_DAYS = 14
_TIMEOUT = 8

# Сообщения OpenSSL по-русски. Сверяем по подстроке: точный текст зависит от
# версии библиотеки, а ключевые слова стабильны.
_REASONS = (
    ("certificate has expired", "срок действия сертификата истёк"),
    ("certificate is not yet valid", "срок действия сертификата ещё не начался"),
    ("hostname mismatch", "сертификат выписан на другое имя"),
    ("doesn't match", "сертификат выписан на другое имя"),
    ("self signed", "сертификат самоподписанный — браузер ему не доверяет"),
    ("self-signed", "сертификат самоподписанный — браузер ему не доверяет"),
    ("unable to get local issuer", "цепочка сертификатов неполная: нет промежуточного"),
    ("unable to verify the first certificate", "цепочка сертификатов неполная"),
)


@dataclass
class TlsProbe:
    """Что удалось узнать о TLS хоста.

    `error` — это «не дозвонились» (третье состояние), а не «сертификат плохой».
    Смешивать их нельзя: сайт, недоступный в момент проверки, не виноват.
    """
    ok: bool = False
    reason: str = ""
    days_left: int | None = None
    error: str = ""


def _ru_reason(message: str) -> str:
    low = message.lower()
    for needle, ru in _REASONS:
        if needle in low:
            return ru
    return message


def _days_left(cert: dict | None) -> int | None:
    raw = cert.get("notAfter") if cert else None
    if not raw:
        return None
    try:
        expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(raw), tz=timezone.utc)
    except (ValueError, TypeError):
        return None
    return (expires - datetime.now(timezone.utc)).days


def probe_tls(host: str, port: int = 443) -> TlsProbe:
    """Проверить сертификат так, как это сделал бы браузер."""
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                return TlsProbe(ok=True, days_left=_days_left(tls.getpeercert()))
    except ssl.SSLCertVerificationError as e:
        return TlsProbe(ok=False, reason=_ru_reason(e.verify_message or str(e)))
    except ssl.SSLError as e:
        return TlsProbe(ok=False, reason=_ru_reason(str(e)))
    except (OSError, ValueError) as e:
        # Хост не ответил, таймаут, кривой порт — проверить не смогли.
        return TlsProbe(error=f"{type(e).__name__}: {e}")


def http_redirects_to_https(host: str) -> bool | None:
    """Уводит ли http:// на https://. None — если проверить не удалось."""
    try:
        r = httpx.get(f"http://{host}/", timeout=_TIMEOUT, follow_redirects=False)
    except httpx.HTTPError:
        return None
    location = r.headers.get("location", "")
    if r.is_redirect:
        return location.lower().startswith("https://")
    return False


class TlsCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        parsed = urlparse(snapshot.start_url)
        host = parsed.hostname
        if not host:
            return []
        where = snapshot.start_url

        if parsed.scheme != "https":
            return [Finding(
                check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                evidence=f"Сайт открывается по HTTP: {snapshot.start_url}. Всё, что "
                         f"посетитель вводит в формы, идёт по открытому каналу и "
                         f"читается любым узлом по пути.",
                location=where, law_reference=LAW_REF,
                recommendation="Выпустите сертификат (Let's Encrypt — бесплатно) и "
                               "включите постоянный редирект с http:// на https://.",
                extra={"https": False},
            )]

        probe = probe_tls(host, parsed.port or 443)
        if probe.error:
            return [Finding(
                check_id=self.id, severity=Severity.INFO, title=self.title,
                evidence=f"Не удалось проверить сертификат {host}: {probe.error}. "
                         f"Возможно, сайт был недоступен в момент проверки.",
                location=where, law_reference=LAW_REF,
                recommendation=f"Проверьте вручную: https://www.ssllabs.com/ssltest/analyze.html?d={host}",
                extra={"https": True, "cert_checked": False},
            )]

        if not probe.ok:
            return [Finding(
                check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                evidence=f"Адрес начинается с https://, но сертификат {host} "
                         f"недействителен: {probe.reason}. Браузер посетителя покажет "
                         f"страницу с предупреждением, а данные форм при этом "
                         f"защищены не так, как выглядит.",
                location=where, law_reference=LAW_REF,
                recommendation="Перевыпустите сертификат и проверьте, что сервер отдаёт "
                               "всю цепочку — включая промежуточный сертификат.",
                extra={"https": True, "cert_valid": False},
            )]

        findings = []
        if probe.days_left is not None and probe.days_left <= _EXPIRY_WARN_DAYS:
            findings.append(Finding(
                check_id=f"{self.id}.expiry", severity=Severity.WARNING, title=self.title,
                evidence=f"Сертификат {host} действует ещё {probe.days_left} дн. "
                         f"Когда он истечёт, сайт откроется с предупреждением браузера.",
                location=where, law_reference=LAW_REF,
                recommendation="Проверьте автопродление (certbot renew / панель хостинга).",
                extra={"https": True, "cert_valid": True, "days_left": probe.days_left},
            ))

        redirects = http_redirects_to_https(host)
        if redirects is False:
            findings.append(Finding(
                check_id=f"{self.id}.redirect", severity=Severity.WARNING, title=self.title,
                evidence=f"http://{host}/ открывается без переадресации на https. "
                         f"Посетитель, пришедший по старой ссылке, заполнит форму "
                         f"на открытой версии сайта.",
                location=f"http://{host}/", law_reference=LAW_REF,
                recommendation="Настройте постоянный редирект 301 с http:// на https://.",
                extra={"https": True, "cert_valid": True},
            ))

        findings.append(Finding(
            check_id=self.id, severity=Severity.OK, title=self.title,
            evidence=f"Сайт работает по HTTPS, сертификат {host} действителен"
                     + (f" ещё {probe.days_left} дн." if probe.days_left is not None else "."),
            location=where, law_reference=LAW_REF,
            extra={"https": True, "cert_valid": True, "days_left": probe.days_left},
        ))
        return findings
