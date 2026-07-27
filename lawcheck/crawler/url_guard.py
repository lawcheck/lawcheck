"""Защита от SSRF: краулер идёт по адресу, который дал произвольный посетитель.

Сервис живёт в сети Docker Compose рядом с `postgres`, `redis`, `api` и `caddy`.
Без проверки любой желающий может заставить его сходить во внутреннюю сеть
(`http://api:8000/inbox`), на loopback или к метаданным облака
(`http://169.254.169.254/`) — и получить содержимое чужой страницы в своём отчёте.

Проверяем не строку, а РЕЗУЛЬТАТ РЕЗОЛВА: `api`, `localhost`, `0`, `127.1`,
`internal.example.ru` с A-записью в 10.0.0.0/8 — всё это внешне выглядит
по-разному, а ведёт в одно и то же место.

Резолвим через `socket.getaddrinfo`, который на этот момент уже подменён
`net.force_ipv4()`. Это сознательно: проверять нужно ровно те адреса, по которым
пойдёт настоящий клиент, иначе проверка и соединение разойдутся.
"""
import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = ("http", "https")
_DEFAULT_PORTS = {"http": 80, "https": 443}


class UnsafeUrl(ValueError):
    """Адрес ведёт не в публичный интернет."""


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private        # 10/8, 172.16/12, 192.168/16, fc00::/7
        or ip.is_loopback    # 127/8, ::1
        or ip.is_link_local  # 169.254/16 (метаданные облака), fe80::/10
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified  # 0.0.0.0
    )


def check_url(url: str) -> None:
    """Ничего не делает для публичного адреса, иначе бросает UnsafeUrl."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrl(f"поддерживаются только http и https, а не «{parsed.scheme}»")
    host = parsed.hostname
    if not host:
        raise UnsafeUrl("в адресе нет хоста")
    try:
        port = parsed.port or _DEFAULT_PORTS[parsed.scheme]
    except ValueError as e:  # порт не число
        raise UnsafeUrl("некорректный порт") from e
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise UnsafeUrl(f"хост «{host}» не резолвится") from e
    if not infos:
        raise UnsafeUrl(f"хост «{host}» не резолвится")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _is_public(ip):
            raise UnsafeUrl(f"хост «{host}» указывает на внутренний адрес {ip}")


def is_safe(url: str) -> bool:
    try:
        check_url(url)
    except UnsafeUrl:
        return False
    return True
