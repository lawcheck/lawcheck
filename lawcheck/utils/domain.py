"""Свой ли это адрес. Сервис не выносит вердикт о самом себе.

Оценка собственного соответствия, выданная сервисом, который эту оценку
продаёт, ничего не стоит: посетитель не может отличить «у нас всё чисто» от
«мы себе нарисовали». Поэтому свой домен не сканируем вовсе — это честнее
любого результата, который мы бы про себя показали.

Домен берём из `site_base_url`, а не константой в коде: на staging и в тестах
база другая, и исключение должно ехать вместе с ней.

Сравниваем регистрируемый домен, а не строку: `www.lawchek.ru`, `LAWCHEK.RU`,
`https://lawchek.ru/pricing` и поддомены — один и тот же сайт, а вот
`lawchek.ru.example.com` — чужой.
"""
from urllib.parse import urlparse

import tldextract

from lawcheck.config import settings


def registrable_domain(value: str) -> str:
    """`https://www.lawchek.ru/pricing` → `lawchek.ru`. Принимает и голый хост."""
    # Без схемы urlparse кладёт хост в path и отдаёт hostname=None — поэтому
    # подставляем `//`, а не префикс `https://`: схема здесь не важна.
    host = urlparse(value if "//" in value else f"//{value}").hostname or ""
    ext = tldextract.extract(host)
    return f"{ext.domain}.{ext.suffix}".lower() if ext.suffix else ext.domain.lower()


def is_own_site(value: str) -> bool:
    """Адрес ведёт на наш собственный сайт (домен из `site_base_url`)."""
    own = registrable_domain(settings.site_base_url)
    return bool(own) and registrable_domain(value) == own
