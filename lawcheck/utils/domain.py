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

# Свой экстрактор, а не `tldextract.extract`. У готового по умолчанию включён
# public suffix list ИЗ СЕТИ: на пустом кеше первый вызов уходит на
# publicsuffix.org (~0,4 с при живой сети, таймаут при мёртвой) и только потом
# откатывается на снапшот из пакета. В контейнере кеш пустой после каждой
# пересборки, а вызывается это теперь при рендере главной — то есть цену
# платил бы первый посетитель после выката.
#
# `suffix_list_urls=()` выключает сеть: работаем на снапшоте, который приехал
# вместе с пакетом. Для «наш это домен или чужой» его достаточно с запасом —
# у зон, которыми пользуются наши посетители, суффиксы не меняются годами.
_extract = tldextract.TLDExtract(suffix_list_urls=())


def registrable_domain(value: str) -> str:
    """`https://www.lawchek.ru/pricing` → `lawchek.ru`. Принимает и голый хост."""
    # Без схемы urlparse кладёт хост в path и отдаёт hostname=None — поэтому
    # подставляем `//`, а не префикс `https://`: схема здесь не важна.
    host = urlparse(value if "//" in value else f"//{value}").hostname or ""
    ext = _extract(host)
    return f"{ext.domain}.{ext.suffix}".lower() if ext.suffix else ext.domain.lower()


def is_own_site(value: str) -> bool:
    """Адрес ведёт на наш собственный сайт (домен из `site_base_url`)."""
    own = registrable_domain(settings.site_base_url)
    return bool(own) and registrable_domain(value) == own
