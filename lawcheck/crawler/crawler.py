import asyncio
import heapq
import logging
import re
import time
from urllib.parse import unquote, urlparse

import tldextract

from lawcheck.config import settings
from lawcheck.crawler.browser import Browser
from lawcheck.crawler.pdf_fetcher import fetch_pdf
from lawcheck.crawler.snapshot import SiteSnapshot
from lawcheck.crawler.url_guard import check_url, is_safe

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


# Потолок очереди. Больше страниц, чем max_pages, всё равно не обойдём, а
# держать в памяти десятки тысяч ссылок с календаря незачем.
_MAX_QUEUE = 2000


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


# Ключи для ТЕКСТА ссылки — отдельно от PRIORITY_KEYWORDS. Те подобраны под
# слаги URL, где набор символов зажат, и подстрочный матч по ним безопасен.
# На свободном тексте он даёт мусор: «исправили» содержит «правил», а
# «контактные линзы» — «контакт», и каталог интернет-магазина забивает
# приоритетный ярус, вытесняя из бюджета обхода ровно ту юридическую страницу,
# ради которой ярус и заведён. Отсюда границы слова и потолок длины: ссылка на
# документ в футере — короткая подпись, а не абзац.
_PRIORITY_TEXT_RE = re.compile(
    r"\b("
    r"политик[а-яё]*|положени[а-яё]*|оферт[а-яё]*|соглашени[а-яё]*|"
    r"конфиденциальност[а-яё]*|приватност[а-яё]*|персональн[а-яё]*|"
    r"обработк[а-яё]*\s+данных|согласи[ея]|"
    r"правил[ао]|реквизит[а-яё]*|контакты|куки|"
    r"privacy|polic[a-z]*|cookies?|personal\s+data|terms"
    r")\b",
    re.I,
)
_PRIORITY_TEXT_MAX = 60


def _score_url(url: str, text: str = "") -> int:
    """Меньше = выше приоритет."""
    u = unquote(url).lower()  # учитываем кириллицу в percent-encoded путях
    for kw in PRIORITY_KEYWORDS:
        if kw in u:
            return 0
    # Адрес юридического документа часто ничего о нём не говорит: «Политика
    # безопасности» лежит на /info/security и по URL неотличима от любой
    # страницы каталога. Тогда единственная подсказка — текст ссылки.
    if text and len(text) <= _PRIORITY_TEXT_MAX and _PRIORITY_TEXT_RE.search(text):
        return 1
    # Глубина по числу сегментов URL
    depth = len([s for s in urlparse(u).path.split("/") if s])
    return 10 + depth


class Crawler:
    def __init__(self, browser: Browser, max_pages: int | None = None,
                 deadline_sec: int | None = None) -> None:
        self.browser = browser
        self.max_pages = max_pages or settings.crawler_max_pages
        # Запас до job_timeout=600: свой выход по времени вместо убийства процесса.
        self.deadline_sec = deadline_sec or 540

    async def crawl(self, start_url: str) -> SiteSnapshot:
        snapshot = SiteSnapshot(start_url=start_url)
        deadline = time.monotonic() + self.deadline_sec
        # Стартовый адрес приходит от посетителя. Веб-форма и API проверяют его
        # заранее, чтобы показать понятную ошибку; здесь проверка повторяется,
        # потому что краулер вызывается и из воркера очереди.
        check_url(start_url)
        base_domain = _registered_domain(start_url)

        visited: set[str] = set()
        queued: set[str] = set([start_url])
        # Куча вместо списка с сортировкой: раньше очередь пересортировывалась
        # на КАЖДОЙ итерации, то есть обход стоил O(n² log n) по числу ссылок.
        queue: list[tuple[int, int, str]] = [(0, 0, start_url)]
        seq = 0  # тай-брейкер, чтобы куча не сравнивала строки при равном score

        while queue and len(snapshot.pages) < self.max_pages:
            # Мягкий дедлайн по времени: медленный сайт (страница за 29 сек)
            # иначе упирается в жёсткий job_timeout=600, где RQ убивает процесс
            # сигналом и mark_error не выполняется. Здесь выходим сами — со
            # статусом done по уже собранным страницам.
            if time.monotonic() > deadline:
                log.warning("дедлайн обхода (%d сек), в очереди осталось %d",
                            self.deadline_sec, len(queue))
                break
            _, _, url = heapq.heappop(queue)
            if url in visited:
                continue
            visited.add(url)

            log.info("crawling [%d/%d] %s", len(snapshot.pages) + 1, self.max_pages, url)
            if _is_pdf(url):
                # PDF не рендерится в Chromium — качаем через httpx и парсим pypdf.
                # Делаем в threadpool, чтобы не блокировать event loop.
                page = await asyncio.to_thread(fetch_pdf, url)
            else:
                page = await self.browser.fetch(url)
            snapshot.pages.append(page)

            for link in page.links:
                if link.url in visited or link.url in queued:
                    continue
                if len(queue) >= _MAX_QUEUE:
                    # Календари, фильтры и пагинация плодят ссылки быстрее, чем
                    # мы их разбираем. Бюджет страниц всё равно кончится раньше.
                    break
                if _registered_domain(link.url) != base_domain:
                    continue
                if not _is_content_url(link.url):
                    continue
                # Свой домен ещё не значит публичный адрес: поддомен вроде
                # internal.example.ru может резолвиться в 10.0.0.0/8.
                if not await asyncio.to_thread(is_safe, link.url):
                    log.info("пропускаем непубличный адрес: %s", link.url)
                    continue
                seq += 1
                queued.add(link.url)
                heapq.heappush(queue, (_score_url(link.url, link.text), seq, link.url))

        # Очередь не пуста => вышли по лимиту страниц, часть сайта не смотрели.
        snapshot.budget_reached = bool(queue)
        if snapshot.budget_reached:
            log.info("бюджет страниц исчерпан (%d), в очереди осталось %d",
                     self.max_pages, len(queue))
        return snapshot
