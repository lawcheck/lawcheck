"""Доверие TLS у клиента эквайринга: корень Минцифры добавлен и прибит.

11.09.2026 два заказа (8 000 ₽ и 990 ₽) не получили платёжную ссылку:
`enter.tochka.com` предъявляет сертификат УЦ Минцифры, которого нет ни в
системном хранилище, ни в certifi, и httpx падал с CERTIFICATE_VERIFY_FAILED.
Здесь проверяется, что корень на месте, что он именно тот и что стандартные
корни при этом не потерялись.
"""
import hashlib
import ssl

import pytest

from lawcheck.payments import tochka


def _subjects(ctx: ssl.SSLContext) -> list[str]:
    out = []
    for cert in ctx.get_ca_certs():
        for rdn in cert.get("subject", ()):
            out += [value for key, value in rdn if key == "commonName"]
    return out


def test_root_ca_file_matches_pinned_fingerprint():
    """Файл в репозитории — тот самый корень, а не подменённый."""
    pem = tochka._ROOT_CA.read_text(encoding="ascii")
    got = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
    assert got == tochka._ROOT_CA_SHA256


def test_context_trusts_ministry_root():
    ctx = tochka._ssl_context()
    assert "Russian Trusted Root CA" in _subjects(ctx)


def test_context_keeps_default_roots():
    """Корень Минцифры добавляется к обычным, а не вместо них: тот же клиент
    ходит к банку по обычному TLS на других ручках."""
    ctx = tochka._ssl_context()
    assert len(ctx.get_ca_certs()) > 50


def test_context_verifies_hostname_and_chain():
    ctx = tochka._ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_substituted_root_is_rejected(tmp_path, monkeypatch):
    """Подменить корень в образе — значит доверить чужой УЦ. Падаем громко."""
    other = tmp_path / "other.pem"
    other.write_text(
        ssl.DER_cert_to_PEM_cert(
            ssl.PEM_cert_to_DER_cert(tochka._ROOT_CA.read_text(encoding="ascii"))),
        encoding="ascii")
    monkeypatch.setattr(tochka, "_ROOT_CA_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="не тот"):
        tochka._ssl_context()
