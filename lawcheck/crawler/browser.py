from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser as PWBrowser, async_playwright

from lawcheck.config import settings
from lawcheck.crawler.snapshot import Form, FormField, Link, NetworkRequest, PageSnapshot

_FORMS_JS = """
() => {
  const PD_RE = /(privacy|polic|persdata|persdannye|personal-data|политик|персональн|konfidencial|confidential|privat)/i;
  return Array.from(document.querySelectorAll('form')).map(form => {
    const fields = Array.from(form.querySelectorAll('input, select, textarea')).map(el => {
      let label = '';
      if (el.id) {
        const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lbl) label = (lbl.innerText || lbl.textContent || '').trim();
      }
      if (!label) {
        const wrap = el.closest('label');
        if (wrap) label = (wrap.innerText || wrap.textContent || '').trim();
      }
      return {
        name: el.name || '',
        id: el.id || '',
        type: (el.type || el.tagName.toLowerCase()).toLowerCase(),
        placeholder: el.placeholder || '',
        checked: !!el.checked,
        required: !!el.required,
        label: label.slice(0, 300),
      };
    });
    // ближайший общий контейнер — родитель формы; даёт текст рядом с submit
    const parent = form.parentElement || form;
    const surrounding = (parent.innerText || parent.textContent || '').slice(0, 3000);
    // ссылки на Политику в окрестности формы
    const links = Array.from(parent.querySelectorAll('a[href]'));
    const policyLink = links.some(a => PD_RE.test(a.href) || PD_RE.test(a.innerText || ''));
    return {
      action: form.action || '',
      method: (form.method || 'get').toLowerCase(),
      fields,
      surrounding,
      hasPolicyLink: policyLink,
    };
  });
}
"""


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

            raw_forms = await page.evaluate(_FORMS_JS)
            forms: list[Form] = []
            for rf in raw_forms or []:
                fields = [FormField(
                    name=f.get("name") or "",
                    type=f.get("type") or "",
                    placeholder=f.get("placeholder") or "",
                    label=f.get("label") or "",
                    id=f.get("id") or "",
                    checked=bool(f.get("checked")),
                    required=bool(f.get("required")),
                ) for f in rf.get("fields") or []]
                forms.append(Form(
                    action=rf.get("action") or "",
                    method=rf.get("method") or "get",
                    fields=fields,
                    surrounding_text=rf.get("surrounding") or "",
                    page_url=url,
                    has_policy_link=bool(rf.get("hasPolicyLink")),
                ))

            return PageSnapshot(
                url=url,
                status=status,
                title=title,
                html=html,
                text=text or "",
                links=links,
                forms=forms,
                network=network,
                cookies=cookies,
            )
        except Exception as e:
            return PageSnapshot(url=url, status=0, error=str(e), network=network)
        finally:
            await ctx.close()
