import logging
import re
from urllib.parse import unquote, urlparse

import tldextract

from lawcheck.config import settings
from lawcheck.crawler.browser import Browser
from lawcheck.crawler.pdf_fetcher import fetch_pdf
from lawcheck.crawler.snapshot import SiteSnapshot

log = logging.getLogger(__name__)

# Чем меньше priority — тем раньше посетим. Приоритезируем юридически значимые страницы.
PRIORITY_KEYWORDS = [
    "polic", "privacy", "confidential", "konfidencial", "personal-data", "persdata",
    "политик", "персональн", "конфиденциальн", "приватн",
    "contact", "контакт", "ofert", "оферт", "соглашен", "согласи",
    "cookie", "куки", "rules", "правил",
]

# Сегменты пути, которые не являются «контентом» с точки зрения комплаенса
# (auth-flow, API, статика, технические endpoints). На таких страницах не ждём
# ни Политики в футере, ни форм сбора ПДн — отсекаем сразу.
_SKIP_PATH_RE = re.compile(
    r"(^|/)("
    r"auth|login|logout|signin|signout|signup|register|registration|"
    r"oauth|sso|callback|"
    r"api|v\d+|graphql|rss|sitemap|"
    r"_next|_nuxt|static|assets|build|dist|cdn|"
    r"admin|wp-admin|wp-json|wp-login|"
    r"feed|atom|amp|"
    r"download|upload|export|import"
    r")(/|$)",
    re.I,
)
_SKIP_EXT_RE = re.compile(
    r"\.(jpg|jpeg|png|gif|webp|svg|ico|bmp|"
    r"mp4|mp3|webm|avi|mov|wav|"
    r"pdf|doc|docx|xls|xlsx|ppt|pptx|"
    r"zip|tar|gz|rar|7z|"
    r"css|js|mjs|map|json|xml|woff2?|ttf|otf|eot)$",
    re.I,
)


def _registered_domain(url: str) -> str:
    ext = tldextract.extract(url)
    return f"{ext.domain}.{ext.suffix}".lower() if ext.suffix else ext.domain.lower()


def _is_pdf(url: str) -> bool:
    return (urlparse(url).path or "").lower().endswith(".pdf")


def _is_priority_pdf(url: str) -> bool:
    """PDF, который выглядит как Политика/Согласие/Оферта — стоит скачать и распарсить."""
    if not _is_pdf(url):
        return False
    # Декодируем percent-encoding: путь вроде /uploads/%D0%9F%D0%BE%D0%BB...pdf
    # («Политика….pdf») иначе не совпадёт с кириллическими ключевыми словами.
    u = unquote(url).lower()
    return any(kw in u for kw in PRIORITY_KEYWORDS)


def _is_content_url(url: str) -> bool:
    if _is_priority_pdf(url):
        return True  # PDF-политику пропускаем через fetch_pdf, не через браузер
    path = urlparse(url).path or "/"
    if _SKIP_EXT_RE.search(path):
        return False
    if _SKIP_PATH_RE.search(path):
        return False
    return True


def _score_url(url: str) -> int:
    """Меньше = выше приоритет."""
    u = unquote(url).lower()  # учитываем кириллицу в percent-encoded путях
    for kw in PRIORITY_KEYWORDS:
        if kw in u:
            return 0
    # Глубина по числу сегментов URL
    depth = len([s for s in urlparse(u).path.split("/") if s])
    return 10 + depth


class Crawler:
    def __init__(self, browser: Browser, max_pages: int | None = None) -> None:
        self.browser = browser
        self.max_pages = max_pages or settings.crawler_max_pages

    async def crawl(self, start_url: str) -> SiteSnapshot:
        snapshot = SiteSnapshot(start_url=start_url)
        base_domain = _registered_domain(start_url)

        visited: set[str] = set()
        queue: list[tuple[int, str]] = [(0, start_url)]

        while queue and len(snapshot.pages) < self.max_pages:
            queue.sort(key=lambda x: x[0])
            _, url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)

            log.info("crawling [%d/%d] %s", len(snapshot.pages) + 1, self.max_pages, url)
            if _is_pdf(url):
                # PDF не рендерится в Chromium — качаем через httpx и парсим pypdf.
                # Делаем в threadpool, чтобы не блокировать event loop.
                import asyncio
                page = await asyncio.to_thread(fetch_pdf, url)
            else:
                page = await self.browser.fetch(url)
            snapshot.pages.append(page)

            for link in page.links:
                if link.url in visited:
                    continue
                if _registered_domain(link.url) != base_domain:
                    continue
                if not _is_content_url(link.url):
                    continue
                queue.append((_score_url(link.url), link.url))

        return snapshot
