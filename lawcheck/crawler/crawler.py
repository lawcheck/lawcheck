import logging
from urllib.parse import urlparse

import tldextract

from lawcheck.config import settings
from lawcheck.crawler.browser import Browser
from lawcheck.crawler.snapshot import SiteSnapshot

log = logging.getLogger(__name__)

# Чем меньше priority — тем раньше посетим. Приоритезируем юридически значимые страницы.
PRIORITY_KEYWORDS = [
    "polic", "privacy", "политик", "персональн", "конфиденциальн",
    "contact", "контакт", "ofert", "оферт", "соглашен", "согласи",
    "cookie", "куки", "rules", "правил",
]


def _registered_domain(url: str) -> str:
    ext = tldextract.extract(url)
    return f"{ext.domain}.{ext.suffix}".lower() if ext.suffix else ext.domain.lower()


def _score_url(url: str) -> int:
    """Меньше = выше приоритет."""
    u = url.lower()
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
            page = await self.browser.fetch(url)
            snapshot.pages.append(page)

            for link in page.links:
                if link.url in visited:
                    continue
                if _registered_domain(link.url) != base_domain:
                    continue
                queue.append((_score_url(link.url), link.url))

        return snapshot
