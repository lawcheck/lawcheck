"""H1 — Возрастная маркировка медиа-контента (436-ФЗ).

ФЗ № 436-ФЗ требует маркировки информационной продукции в категориях
0+, 6+, 12+, 16+, 18+. Применимо к СМИ, новостным сайтам, видеосервисам,
блогам с публикуемыми материалами.

Эвристика классификатора «это медиа?»:
- наличие большого числа ссылок на «статьи»/«новости»/«посты»;
- разделы /news/, /article/, /post/, /blog/ в URL ссылок;
- HTML-разметка <article> или text-density на главной;
- Schema.org разметка NewsArticle/Article (не парсим в MVP).

Если медиа — ищем хотя бы одну маркировку в видимом тексте сайта.
"""
import re

from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.crawler.snapshot import SiteSnapshot

CHECK_ID = "H1"
TITLE = "Возрастная маркировка информационной продукции"
LAW_REF = "ст. 11, 13 ФЗ № 436-ФЗ «О защите детей от информации, причиняющей вред их здоровью»"

# Маркеры «это медиа»
_MEDIA_URL_RE = re.compile(r"/(news|article|articles|post|posts|blog|story|stories)/", re.I)
_MEDIA_TEXT_RE = re.compile(
    r"(\bновост[иье]\b|\bстать[ияй]\b|\bблог\b|\bредакц[ияи]\b|"
    r"\bжурналист[аов]?\b|\bавтор[аы]?\b|\bопубликован[аоы]?\b|"
    r"\bподписаться\s+на\s+(?:новости|канал))",
    re.I,
)

# Сами возрастные метки на странице
# \b после + не работает (+ — не word-char), поэтому boundary только слева.
_AGE_LABEL_RE = re.compile(r"(?:^|[^\d\w])(0\+|6\+|12\+|16\+|18\+)")
# В мета-тегах может быть в виде «age-rating: 12+»
_AGE_META_RE = re.compile(r"age[-_]?rating", re.I)


def _is_media_site(snapshot: SiteSnapshot) -> tuple[bool, str]:
    article_links = 0
    text_chunks: list[str] = []
    for page in snapshot.pages:
        if page.error or page.status >= 400:
            continue
        for link in page.links:
            if _MEDIA_URL_RE.search(link.url):
                article_links += 1
        if page.text:
            text_chunks.append(page.text)

    combined = " ".join(text_chunks).lower().replace("ё", "е")
    text_hits = len(_MEDIA_TEXT_RE.findall(combined))

    if article_links >= 10:
        return True, f"{article_links} ссылок типа /news/, /article/, /blog/"
    if article_links >= 3 and text_hits >= 5:
        return True, f"{article_links} article-ссылок + {text_hits} медиа-маркеров в тексте"
    return False, f"article_links={article_links}, text_hits={text_hits}"


class AgeMarkingCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        is_media, reason = _is_media_site(snapshot)
        if not is_media:
            return []  # 436-ФЗ применяется к информационной продукции — пропускаем не-медиа

        labels: set[str] = set()
        sample_url: str | None = None
        for page in snapshot.pages:
            if page.error or page.status >= 400 or not page.text:
                continue
            for m in _AGE_LABEL_RE.finditer(page.text):
                labels.add(m.group(1))
                sample_url = sample_url or page.url
            if _AGE_META_RE.search(page.html or ""):
                # есть meta-тег age-rating — тоже признак маркировки
                sample_url = sample_url or page.url

        if not labels:
            return [Finding(
                check_id=self.id, severity=Severity.WARNING, title=self.title,
                evidence=f"Сайт классифицирован как медиа ({reason}), но возрастная маркировка "
                         f"(0+/6+/12+/16+/18+) на просканированных страницах не обнаружена.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Разместите возрастную маркировку у каждого материала. "
                               "Для общего сайта — в подвале или в шапке.",
            )]

        return [Finding(
            check_id=self.id, severity=Severity.OK, title=self.title,
            evidence=f"Найдены возрастные маркировки: {', '.join(sorted(labels))}.",
            location=sample_url or snapshot.start_url, law_reference=LAW_REF,
            extra={"labels": sorted(labels)},
        )]
