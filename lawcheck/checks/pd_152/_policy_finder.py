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


# Опознание документа-Политики по самой странице. Нужно, когда ссылка названа
# так, что по тексту её не опознать («Политика безопасности», «Правовая
# информация»): документ на сайте есть и закон выполнен, а мы бы отдали
# владельцу критичное нарушение.
#
# Условие из двух частей, и обе обязательны. Сначала самоназвание документа:
# без него набор маркеров ниже добирают и статья блога про обработку ПДн, и
# страница тарифов оператора связи — а называются они иначе, чем документ.
# Затем содержание: элементы, которые Политика обязана раскрывать по ст. 18.1
# ч. 2 152-ФЗ (цели, правовое основание, права субъекта), плюс объём документа.
_POLICY_TITLE_RE = re.compile(
    r"(политик|положени|регламент)[а-яё]*[^.\n]{0,60}"
    r"(персональн|обработк|конфиденциальн|приватн)",
    re.I,
)
# Заголовок стоит в начале извлечённого текста — если `title` страницы пуст.
_POLICY_TITLE_ZONE = 400
_POLICY_BODY_MARKERS = (
    re.compile(r"цел[а-яё]*[^.]{0,40}обработк", re.I),
    re.compile(r"правов[а-яё]*[^.]{0,20}основани", re.I),
    re.compile(r"субъект[а-яё]*\s+персональн", re.I),
    re.compile(r"оператор[а-яё]*\s+персональн", re.I),
    re.compile(r"согласи[а-яё]*[^.]{0,40}обработк", re.I),
    re.compile(r"(уничтожени|блокирован|обезличиван)[а-яё]*[^.]{0,40}персональн", re.I),
    re.compile(r"152-?\s?ФЗ|о персональных данных", re.I),
)
_POLICY_BODY_MIN_LEN = 1500
_POLICY_BODY_MIN_MARKERS = 3


def is_policy_link(url: str, text: str) -> bool:
    return bool(_POLICY_RE.search(normalize_ru(text)) or _POLICY_URL_RE.search(url))


def looks_like_policy_body(title: str, text: str) -> bool:
    """Похож ли текст страницы на сам документ Политики обработки ПДн."""
    if len(text) < _POLICY_BODY_MIN_LEN:
        return False
    if not _POLICY_TITLE_RE.search(title or "") \
            and not _POLICY_TITLE_RE.search(text[:_POLICY_TITLE_ZONE]):
        return False
    hits = sum(1 for rx in _POLICY_BODY_MARKERS if rx.search(text))
    return hits >= _POLICY_BODY_MIN_MARKERS


def find_policy_by_body(snapshot: SiteSnapshot) -> str | None:
    """URL страницы, которая сама выглядит как документ Политики, или None.

    Отдельно от `find_policy_links` и намеренно: это догадка по содержанию, и
    место ей только в A1, где вопрос «документ вообще есть?». Проверкам
    содержания (A2, A3) догадку подсовывать нельзя — они начнут судить о
    разделах и дате документа, которого на сайте может не быть вовсе.
    """
    for page in snapshot.pages:
        if page.error or page.status >= 400:
            continue
        if looks_like_policy_body(page.title, page.text):
            return page.url
    return None


def pages_linking_to(snapshot: SiteSnapshot, policy_url: str) -> list[tuple[str, str]]:
    """(page_url, policy_url) для страниц, ведущих на уже известный документ."""
    out: list[tuple[str, str]] = []
    for page in snapshot.pages:
        if page.error or page.status >= 400:
            continue
        if page.url == policy_url or any(link.url == policy_url for link in page.links):
            out.append((page.url, policy_url))
    return out


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
