from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser as PWBrowser, async_playwright

from lawcheck.config import settings
from lawcheck.crawler.snapshot import Link, NetworkRequest, PageSnapshot


class Browser:
    """Тонкая обёртка над Playwright Chromium для краулинга одной страницы."""

    def __init__(self) -> None:
        self._pw = None
        self._browser: PWBrowser | None = None

    async def __aenter__(self) -> "Browser":
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def fetch(self, url: str) -> PageSnapshot:
        assert self._browser is not None, "Browser used outside async context"

        ctx = await self._browser.new_context(
            user_agent=settings.crawler_user_agent,
            ignore_https_errors=True,
        )
        page = await ctx.new_page()

        network: list[NetworkRequest] = []

        def on_request(req) -> None:
            try:
                domain = urlparse(req.url).netloc
                network.append(NetworkRequest(url=req.url, domain=domain, resource_type=req.resource_type))
            except Exception:
                pass

        page.on("request", on_request)

        try:
            response = await page.goto(
                url,
                timeout=settings.crawler_timeout_sec * 1000,
                wait_until="domcontentloaded",
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            status = response.status if response else 0
            title = await page.title()
            html = await page.content()

            anchors = await page.eval_on_selector_all(
                "a[href]",
                """els => els.map(a => ({ href: a.href, text: (a.innerText || '').trim().slice(0, 200) }))""",
            )
            links: list[Link] = []
            for a in anchors:
                href = (a.get("href") or "").strip()
                if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                    continue
                links.append(Link(url=urljoin(url, href), text=a.get("text") or ""))

            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            cookies = await ctx.cookies()

            return PageSnapshot(
                url=url,
                status=status,
                title=title,
                html=html,
                text=text or "",
                links=links,
                network=network,
                cookies=cookies,
            )
        except Exception as e:
            return PageSnapshot(url=url, status=0, error=str(e), network=network)
        finally:
            await ctx.close()
