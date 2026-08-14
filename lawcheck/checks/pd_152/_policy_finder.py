"""Общая логика обнаружения ссылки на Политику обработки ПДн.

Используется проверками A1 (наличие ссылки) и A2 (валидность документа),
а в дальнейшем — A3 (разделы), A4 (реквизиты), A5 (актуальность).
"""
import re

from lawcheck.crawler.snapshot import PageSnapshot, SiteSnapshot
from lawcheck.utils.text import normalize_ru

_POLICY_RE = re.compile(
    r"(политик[аи][^.]{0,40}(персональн|конфиденциальн|приватн))"
    r"|(положени[ея][^.]{0,40}персональн)"
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


# Маркеры документа-Политики в тексте самой страницы. Нужны, когда ссылка
# названа так, что по тексту её не опознать («Политика безопасности»,
# «Правовая информация»): документ на сайте есть и закон выполнен, а мы бы
# отдали владельцу критичное нарушение. Требуем несколько маркеров и объём,
# чтобы не принять за Политику случайное упоминание ПДн в статье блога.
_POLICY_BODY_MARKERS = (
    re.compile(r"обработк[аеи]\s+персональн", re.I),
    re.compile(r"субъект[а-я]*\s+персональн", re.I),
    re.compile(r"\bоператор", re.I),
    re.compile(r"18\.1", re.I),
    re.compile(r"152-?ФЗ|о персональных данных", re.I),
)
_POLICY_BODY_MIN_LEN = 800
_POLICY_BODY_MIN_MARKERS = 3


def is_policy_link(url: str, text: str) -> bool:
    return bool(_POLICY_RE.search(normalize_ru(text)) or _POLICY_URL_RE.search(url))


def looks_like_policy_body(text: str) -> bool:
    """Похож ли текст страницы на сам документ Политики обработки ПДн."""
    if len(text) < _POLICY_BODY_MIN_LEN:
        return False
    hits = sum(1 for rx in _POLICY_BODY_MARKERS if rx.search(text))
    return hits >= _POLICY_BODY_MIN_MARKERS


def find_policy_by_body(snapshot: SiteSnapshot) -> str | None:
    """URL страницы, которая сама выглядит как документ Политики, или None."""
    for page in snapshot.pages:
        if page.error or page.status >= 400:
            continue
        if looks_like_policy_body(page.text):
            return page.url
    return None


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
    if out:
        return out

    # Ссылку по названию не опознали — ищем сам документ в обойдённых страницах
    # и считаем ведущими на него все страницы, где на него есть ссылка.
    policy_url = find_policy_by_body(snapshot)
    if policy_url is None:
        return []
    for page in snapshot.pages:
        if page.error or page.status >= 400:
            continue
        if page.url == policy_url or any(link.url == policy_url for link in page.links):
            out.append((page.url, policy_url))
    return out


def find_policy_page(snapshot: SiteSnapshot, policy_url: str) -> PageSnapshot | None:
    """Страница самой Политики, если краулер успел её посетить."""
    for page in snapshot.pages:
        if page.url == policy_url:
            return page
    return None
