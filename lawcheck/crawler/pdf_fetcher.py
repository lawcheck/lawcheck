"""Загрузка PDF-страниц (например, Политика в PDF) и извлечение текста.

Используется параллельно с Playwright: если URL похож на «политика.pdf»
или это PDF по content-type, краулер вызывает fetch_pdf() и кладёт
текст в PageSnapshot.text как обычную страницу.
"""
import io
import logging

import httpx

from lawcheck.config import settings
from lawcheck.crawler.snapshot import PageSnapshot
from lawcheck.crawler.url_guard import UnsafeUrl, check_url

log = logging.getLogger(__name__)
_TIMEOUT = httpx.Timeout(20.0, connect=8.0)
_MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_REDIRECTS = 5


def _extract_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # ленивый импорт, чтобы не тащить в тесты без PDF
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception as e:
                log.debug("pypdf page extract failed: %s", e)
        return "\n".join(pages)
    except Exception as e:
        log.warning("pypdf failed to parse PDF: %s", e)
        return ""


def fetch_pdf(url: str) -> PageSnapshot:
    """Скачивает PDF и возвращает PageSnapshot с распарсенным текстом."""
    try:
        check_url(url)
    except UnsafeUrl as e:
        log.warning("pdf: непубличный адрес %s (%s)", url, e)
        return PageSnapshot(url=url, status=0, error="unsafe_url")
    try:
        with httpx.Client(
            headers={"User-Agent": settings.crawler_user_agent},
            timeout=_TIMEOUT,
            # Редиректы ведём вручную: follow_redirects=True отправил бы запрос
            # на внутренний адрес до того, как мы его увидим.
            follow_redirects=False,
        ) as client:
            r = client.send(client.build_request("GET", url), stream=True)
            for _ in range(_MAX_REDIRECTS):
                if not r.is_redirect:
                    break
                nxt = str(r.next_request.url) if r.next_request else ""
                r.close()
                try:
                    check_url(nxt)
                except UnsafeUrl as e:
                    log.warning("pdf: редирект на непубличный адрес %s (%s)", nxt, e)
                    return PageSnapshot(url=url, status=0, error="unsafe_redirect")
                r = client.send(client.build_request("GET", nxt), stream=True)
            if r.is_redirect:
                r.close()
                return PageSnapshot(url=url, status=0, error="too_many_redirects")
            if r.status_code >= 400:
                r.close()
                return PageSnapshot(url=url, status=r.status_code, error=f"http {r.status_code}")
            # Читаем тело порциями с потолком: проверка после client.get()
            # означала, что 2 ГБ уже лежат в памяти, когда мы смотрим на лимит.
            chunks: list[bytes] = []
            received = 0
            try:
                for chunk in r.iter_bytes(chunk_size=64 * 1024):
                    received += len(chunk)
                    if received > _MAX_PDF_BYTES:
                        return PageSnapshot(url=url, status=r.status_code,
                                            error="pdf too large")
                    chunks.append(chunk)
            finally:
                r.close()
            data = b"".join(chunks)
            text = _extract_text(data)
            return PageSnapshot(
                url=url,
                status=r.status_code,
                title="",
                text=text,
                html="",
            )
    except httpx.TimeoutException:
        return PageSnapshot(url=url, status=0, error="timeout")
    except Exception as e:
        log.exception("pdf fetch failed for %s", url)
        return PageSnapshot(url=url, status=0, error=f"unexpected_{type(e).__name__}")
