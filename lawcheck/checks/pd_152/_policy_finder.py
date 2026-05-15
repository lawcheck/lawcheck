"""Общая логика обнаружения ссылки на Политику обработки ПДн.

Используется проверками A1 (наличие ссылки) и A2 (валидность документа),
а в дальнейшем — A3 (разделы), A4 (реквизиты), A5 (актуальность).
"""
import re

from lawcheck.crawler.snapshot import PageSnapshot, SiteSnapshot
from lawcheck.utils.text import normalize_ru

_POLICY_RE = re.compile(
    r"(политик[аи][^.]{0,40}(персональн|конфиденциальн|приватн))"
    r"|(privacy[-_ ]?polic)"
    r"|(обработк[аеи]\s+персональн)"
    r"|(personal[-_ ]?data)"
    r"|(\bконфиденциальност[ьи]\b)"
    r"|(\bприватност[ьи]\b)"
    r"|(\bprivacy\b)",
    re.I,
)
_POLICY_URL_RE = re.compile(
    r"(privacy|polic[yi]|persdata|persdannye|personal[-_]?data|политик|персональн|konfidencial|confidential|privat)",
    re.I,
)


def is_policy_link(url: str, text: str) -> bool:
    return bool(_POLICY_RE.search(normalize_ru(text)) or _POLICY_URL_RE.search(url))


def find_policy_links(snapshot: SiteSnapshot) -> list[tuple[str, str]]:
    """(page_url, policy_url) для каждой страницы, где найдена ссылка на Политику."""
    out: list[tuple[str, str]] = []
    for page in snapshot.pages:
        if page.error or page.status >= 400:
            continue
        for link in page.links:
            if is_policy_link(link.url, link.text):
                out.append((page.url, link.url))
                break
    return out


def find_policy_page(snapshot: SiteSnapshot, policy_url: str) -> PageSnapshot | None:
    """Страница самой Политики, если краулер успел её посетить."""
    for page in snapshot.pages:
        if page.url == policy_url:
            return page
    return None
