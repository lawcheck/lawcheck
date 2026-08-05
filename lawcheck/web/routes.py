"""Публичный сайт: главная, лента проверок, SEO-страницы, лид-формы.

Остальное разнесено по под-роутерам, которые подключаются ниже и получают тот
же экземпляр `templates` (в нём общие globals — реквизиты оператора, Метрика,
nonce для CSP):
  web/report.py    — страница отчёта и доступ к платной части;
  web/payments.py  — оплата и вебхук банка;
  web/account.py   — кабинет заказа;
  web/internal.py  — ручки для cron;
  web/auth.py      — аккаунты; web/blog.py, web/landings.py, web/rkn.py — SEO.
"""
import asyncio
import logging
import re

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pathlib import Path
from urllib.parse import urlparse

from lawcheck.config import settings
from lawcheck.crawler.url_guard import UnsafeUrl, check_url
from lawcheck.db import repo
from lawcheck.notify import mailer, telegram
from lawcheck.reporting import fines
from lawcheck.utils.contact import contact_url, mask_contact
from lawcheck.utils.email import valid_email
from lawcheck.web import (
    account, auth, blog, deps, internal, landings, magnets, payments, ratelimit,
    report, rkn, security,
)
from lawcheck.web.operator import OPERATOR
from lawcheck.web.scanning import start_scan

log = logging.getLogger(__name__)

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _money(value: int) -> str:
    """1234567 -> '1 234 567' (неразрывные пробелы между разрядами)."""
    return f"{int(value):,}".replace(",", " ")


templates.env.filters["money"] = _money
templates.env.globals["fine_group"] = fines.group_for  # вызывается внутри Jinja-макроса
templates.env.globals["contact_url"] = contact_url  # контакт заявки ссылкой в /inbox

templates.env.globals["operator"] = OPERATOR
templates.env.globals["metrika_id"] = settings.metrika_id
templates.env.globals["site_base_url"] = settings.site_base_url.rstrip("/")
templates.env.globals["google_site_verification"] = settings.google_site_verification
templates.env.globals["magnet_for"] = magnets.get  # статья блога → лид-магнит, если он есть
templates.env.globals["csp_nonce"] = deps.csp_nonce  # nonce для инлайн-скриптов

# Блог и нишевые посадочные используют тот же экземпляр templates (общие globals)
# и подключаются как под-роутеры — только когда SEO-контент готов к публикации.
blog.templates = templates
landings.templates = templates
templates.env.globals["seo_enabled"] = settings.seo_enabled
if settings.seo_enabled:
    router.include_router(blog.router)
    router.include_router(landings.router)

# Посадочная «Уведомление в РКН» и проверка по реестру операторов — без гейта
# SEO: это цель рекламной кампании, должна жить независимо от флага.
rkn.templates = templates
router.include_router(rkn.router)

# Аккаунты (регистрация/вход/выход). Сессии всегда включены (SessionMiddleware
# ставится в create_app с секретом из .env или эфемерным в dev).
auth.templates = templates
templates.env.globals["accounts_enabled"] = True
templates.env.globals["session_email"] = deps.session_email  # для навигации в шаблонах
templates.env.globals["session_unverified"] = deps.session_unverified  # баннер «подтвердите email»
router.include_router(auth.router)

# Отчёт, оплата, кабинет — тот же экземпляр templates.
report.templates = templates
payments.templates = templates
account.templates = templates
router.include_router(report.router)
router.include_router(payments.router)
router.include_router(account.router)
# Ручки для cron ничего не рендерят — шаблоны им не нужны.
router.include_router(internal.router)


# Связка «находка → готовый текст»: с какого раздела шаблонов (pro_templates.html)
# брать болванку под эту находку. Ключ — префикс check_id (часть до "."),
# значение — (id-якорь раздела, подпись). None — для находки готового текста нет.
_TEMPLATE_FIX = {
    "A1": ("tpl-policy", "Политика обработки ПДн"),
    "A2": ("tpl-policy", "Политика обработки ПДн"),
    "A3": ("tpl-policy", "Политика обработки ПДн"),
    "B2": ("tpl-consent", "Согласие для форм"),
    "C2": ("tpl-rkn", "Уведомление в РКН"),
    "D1": ("tpl-rkn", "Уведомление в РКН (трансграничная передача)"),
}


def _fix_template(check_id: str):
    return _TEMPLATE_FIX.get(check_id.split(".")[0])


templates.env.globals["fix_template"] = _fix_template


# === Главная: форма + список последних сканов ===

# Публичная лента «Последние проверки» — это соцдоказательство и SEO-страницы.
# Рядом с отчётами корпоративных клиентов не должно быть adult/треш-доменов:
# один такой сосед закрывает вкладку B2B-клиенту.
#
# Однозначные корни ищем подстрокой — так ловятся зеркала и поддомены
# (ru.xvideos.com, porno-hd.net, xn--…).
_FEED_BLOCK_SUBSTRINGS = (
    "porn", "xvideos", "xnxx", "xhamster", "hentai", "escort",
    "casino", "1xbet", "vulkan", "viagra", "cialis",
)
# Короткие и многозначные — только как отдельное слово внутри домена.
# Подстрокой они выкидывали из ленты beton-zavod.ru, alphabet.ru и sexton.ru,
# то есть ровно ту аудиторию, ради которой лента и существует.
_FEED_BLOCK_WORDS = frozenset({"sex", "bet", "loan"})


# Чат-виджет и подписка на отчёт шлют уведомления владельцу.
_RL_INQUIRY = ratelimit.Limit(limit=10, window_sec=3600)


def _feed_domain_blocked(domain: str) -> bool:
    if any(bad in domain for bad in _FEED_BLOCK_SUBSTRINGS):
        return True
    words = {w for w in re.split(r"[^a-z0-9]+", domain) if w}
    return bool(words & _FEED_BLOCK_WORDS)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    raw_recent = await asyncio.to_thread(repo.list_recent_scans, 80)
    # Анти-соцдоказательство: один и тот же домен 10 раз подряд выглядит как
    # «сервисом пользуется только владелец». Дедуплицируем по домену и
    # показываем блок только при достаточном разнообразии.
    seen: set[str] = set()
    recent = []
    for s in raw_recent:
        domain = urlparse(s.url).netloc.lower().removeprefix("www.")
        if domain in seen or _feed_domain_blocked(domain):
            continue
        seen.add(domain)
        recent.append(s)
        if len(recent) >= 10:
            break
    if len(recent) < 5:
        recent = []
    return templates.TemplateResponse(request, "index.html", {"recent": recent})


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {})


def _checked(value: str) -> bool:
    """Отмечен ли чекбокс. Пустая строка приходит от снятого поля, но `bool()`
    здесь мало: строку `0` или `false` (скрытое поле-спутник, чужая интеграция)
    truthiness превратила бы в согласие, которого человек не давал."""
    return value.strip().lower() in {"1", "on", "true", "yes", "да"}


@router.post("/inquiry")
async def inquiry(request: Request, bg: BackgroundTasks,
                  message: str = Form(...), contact: str = Form(""),
                  page: str = Form(""), website: str = Form(""),
                  pd_consent: str = Form(""), ad_consent: str = Form("")):
    """Вопрос из чат-виджета. Сохраняем + мгновенный алерт владельцу в Telegram.

    Два разных согласия: на обработку ПДн (обязательное — без него нельзя даже
    хранить контакт, ст. 9 152-ФЗ) и на рекламные письма (добровольное,
    ст. 18 ФЗ «О рекламе»). Второе не влияет на ответ по существу вопроса.
    """
    if website:  # honeypot: бот заполнил скрытое поле — тихо игнорируем
        return {"ok": True}
    ratelimit.enforce(request, "inquiry", _RL_INQUIRY,
                      message="Слишком много сообщений. Попробуйте позже.")
    message = message.strip()
    contact = contact.strip()
    if len(message) < 2:
        raise HTTPException(status_code=422, detail="empty message")
    # Заявка без обратного адреса мертва в момент создания: ответить некуда,
    # а вопрос из виджета — единственный канал, где человек пишет сам.
    if len(contact) < 3:
        raise HTTPException(status_code=422, detail="empty contact")
    if not _checked(pd_consent):
        raise HTTPException(status_code=422, detail="no pd consent")
    ads = _checked(ad_consent)
    inq_id = await asyncio.to_thread(repo.create_inquiry, message, contact, page, ads)
    # Текст обращения в лог не пишем: человек оставляет там и ФИО, и адрес сайта,
    # и обстоятельства дела. Он целиком уходит владельцу в Telegram и лежит в БД.
    log.info("inquiry #%s: %s симв. | контакт: %s | реклама: %s",
             inq_id, len(message), mask_contact(contact), "да" if ads else "нет")
    bg.add_task(
        telegram.notify_owner,
        f"💬 Вопрос с сайта #{inq_id}\n{telegram.esc(message[:1500])}\n\n"
        f"Ответить: <b>{telegram.contact_link(contact)}</b>"
        + ("\n📬 Согласие на рассылку — можно писать предложения" if ads else "")
        + (f"\nСтраница: {telegram.esc(page)}" if page else ""),
    )
    return {"ok": True}


@router.get("/oferta", response_class=HTMLResponse)
async def oferta(request: Request):
    return templates.TemplateResponse(request, "oferta.html", {})


@router.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request):
    """Входящие: вопросы чат-виджета + email-лиды.

    Основная защита — basic_auth на Caddy. Здесь она продублирована на уровне
    приложения: запрос, пришедший мимо прокси (например, от собственного
    краулера через SSRF на `http://api:8000/inbox`), несёт внутренний Host и до
    данных не доходит. Одного слоя защиты мало, когда в сеть можно зайти сбоку.
    """
    expected = urlparse(settings.site_base_url).netloc.lower()
    if expected and request.headers.get("host", "").lower() != expected:
        # 404, а не 403: посторонним не сообщаем, что страница существует.
        raise HTTPException(status_code=404, detail="not found")
    inquiries = await asyncio.to_thread(repo.list_inquiries, 200)
    leads = await asyncio.to_thread(repo.list_leads, 200)
    return templates.TemplateResponse(request, "inbox.html", {
        "inquiries": inquiries, "leads": leads,
    })


# === SEO: sitemap.xml + robots.txt ===

@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> str:
    base = settings.site_base_url.rstrip("/")
    # Clean-param — директива Яндекса: рекламные метки не меняют содержимое
    # страницы, и без неё каждый переход из Директа (`?yclid=...`) робот видит
    # как отдельный URL — дубли главной и лендингов в индексе.
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Clean-param: utm_source&utm_medium&utm_campaign&utm_content&utm_term"
        "&yclid&_openstat&gclid\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )


@router.get("/sitemap.xml")
async def sitemap() -> Response:
    base = settings.site_base_url.rstrip("/")
    # (path, lastmod|None)
    entries: list[tuple[str, str | None]] = [
        ("/", None), ("/pricing", None), ("/privacy", None), ("/oferta", None),
        ("/uvedomlenie-rkn", None), ("/reestr-rkn", None),
    ]
    if settings.seo_enabled:
        articles = [
            (f"/blog/{a.slug}", a.date.isoformat() if a.date and a.date.year > 1 else None)
            for a in blog.list_articles()
        ]
        # Дата листинга блога — дата самой свежей статьи на нём. Выдумывать
        # lastmod для остальных страниц не из чего, поэтому там его нет:
        # недостоверную дату поисковик всё равно игнорирует.
        dates = [lm for _, lm in articles if lm]
        entries.append(("/blog", max(dates) if dates else None))
        entries += articles
        entries += [(f"/proverka/{niche}", None) for niche in landings.LANDINGS]
    # Витрина отчётов: остальные закрыты `noindex` в report.py, эти —
    # единственные, которым место в поиске, поэтому заявляем их явно.
    entries += [(f"/report/{scan_id}", None) for scan_id in sorted(report.INDEXABLE_REPORTS)]
    items = "".join(
        f"<url><loc>{base}{path}</loc>" + (f"<lastmod>{lm}</lastmod>" if lm else "") + "</url>"
        for path, lm in entries
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'
    return Response(content=xml, media_type="application/xml")






@router.post("/webhooks/telegram")
async def telegram_webhook(request: Request):
    """Апдейты бота. Нужны только для deep-link подключения мониторинга:
    клиент жмёт Start по ссылке t.me/bot?start=<order_id> → привязываем его чат.

    Секрет обязателен. Раньше пустая настройка выключала проверку целиком, и
    эндпойнт оставался открыт всему интернету: `chat_id` берётся из присланного
    тела, то есть посторонний рассылал сообщения нашим ботом кому угодно, а зная
    order_id — подписывал свой чат на чужой мониторинг. Незаданный секрет теперь
    закрывает ручку, а не открывает.
    """
    given = request.headers.get("X-Telegram-Bot-Api-Secret-Token") or ""
    if not security.secret_matches(given, settings.telegram_webhook_secret):
        raise HTTPException(status_code=403, detail="forbidden")
    try:
        upd = await request.json()
    except Exception:
        return {"ok": True}
    msg = upd.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id") or "")
    if not chat_id or not text.startswith("/start"):
        return {"ok": True}
    parts = text.split(maxsplit=1)
    order_id = parts[1].strip() if len(parts) > 1 else ""
    if order_id:
        order = await asyncio.to_thread(repo.set_client_chat_id, order_id, chat_id)
        if order:
            lines = [f"✅ Доступ к заказу <b>{order.id[:8]}</b> сохранён.",
                     f"Личный кабинет: {settings.site_base_url}/account/{order.id}",
                     "(сохраните это сообщение — здесь ваша постоянная ссылка)"]
            if order.monitored_url:
                lines.append(f"\nБуду присылать сюда изменения по сайту "
                             f"<b>{order.monitored_url}</b> после еженедельных проверок.")
            await asyncio.to_thread(telegram.send_message, chat_id, "\n".join(lines))
        else:
            await asyncio.to_thread(
                telegram.send_message, chat_id,
                "Не нашёл заказ. Откройте ссылку из кабинета ещё раз.")
    else:
        await asyncio.to_thread(
            telegram.send_message, chat_id,
            "Это бот уведомлений LawCheck. Подключите его кнопкой в кабинете заказа.")
    return {"ok": True}


@router.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request, scan: str = ""):
    recent = await asyncio.to_thread(repo.list_recent_scans, 30)
    example = next(
        (s for s in recent if s.status == "done"
         and not _feed_domain_blocked(urlparse(s.url).netloc.lower().removeprefix("www."))),
        None,
    )
    # scan прилетает с CTA отчёта («Открыть исправления») — привяжем к нему покупку,
    # чтобы после оплаты открыть рецепты именно на этом отчёте. Заодно покажем
    # мост «для вашего отчёта: N исправлений готовы» вместо безликого hero.
    scan_id = scan.strip()
    scan_ctx = None
    if scan_id:
        s = await asyncio.to_thread(repo.get_scan, scan_id)
        if s is not None and s.status == "done":
            locked = sum(1 for f in s.findings
                         if f.severity != "ok" and f.recommendation)
            scan_ctx = {"url": s.url,
                        "locked": max(0, locked - report.FREE_RECIPES),
                        "id": s.id}
    return templates.TemplateResponse(request, "pricing.html",
                                      {"example": example, "scan_id": scan_id,
                                       "scan_ctx": scan_ctx})


# === POST формы — создаёт скан, редиректит на /report/{id} ===

@router.post("/scan", response_class=HTMLResponse)
async def create_scan_form(request: Request, bg: BackgroundTasks, url: str = Form(...),
                           max_pages: int = Form(10)):
    # Скан поднимает Chromium и краулит ЧУЖОЙ сайт с нашего IP: без лимита это
    # и расход ресурсов, и abuse-жалобы за чужой краулинг в нашу сторону.
    if ratelimit.exceeded(request, "scan", ratelimit.SCAN):
        return templates.TemplateResponse(
            request, "index.html",
            {"recent": [], "url": url,
             "url_error": "слишком много проверок с вашего адреса, попробуйте через час"},
            status_code=429)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        await asyncio.to_thread(check_url, url)
    except UnsafeUrl as e:
        return templates.TemplateResponse(request, "index.html",
                                          {"recent": [], "url_error": str(e), "url": url},
                                          status_code=422)
    # Верхняя граница есть в API-схеме (ge=1, le=100) — публичная форма не должна
    # быть защищена слабее.
    max_pages = max(1, min(max_pages, 100))
    # Залогинен — привяжем скан к аккаунту, чтобы он попал в «Мои отчёты».
    scan_id = await asyncio.to_thread(start_scan, bg, url, max_pages,
                                      user_id=deps.session_uid(request))
    return RedirectResponse(url=f"/report/{scan_id}", status_code=303)


@router.post("/obrazec/{slug}", response_class=HTMLResponse)
async def magnet_send(request: Request, slug: str, bg: BackgroundTasks,
                      email: str = Form(...)):
    """Прислать типовой документ на почту со страницы статьи блога.

    Сам текст документа открыт на странице — он и приносит трафик. Почта здесь
    конвертирует уже пришедшего читателя в лид, а не прячет контент.
    """
    magnet = magnets.get(slug)
    if magnet is None:
        raise HTTPException(status_code=404, detail="not found")
    ratelimit.enforce(request, "magnet", _RL_INQUIRY,
                      message="Слишком много запросов. Попробуйте позже.")
    email = email.strip().lower()
    if not valid_email(email):
        return RedirectResponse(url=f"/blog/{slug}?mfail=1#obrazec", status_code=303)

    page_url = f"{settings.site_base_url}/blog/{slug}"
    # scan_id у лида с магнита синтетический: скана за ним нет, и письмо-догонялка
    # по отчёту такой лид пропустит (followup.run проверяет get_scan на None).
    is_new = await asyncio.to_thread(repo.create_lead, f"magnet:{slug}", page_url, email)
    body = (f"<p>Здравствуйте! Вот образец, который вы запросили на "
            f"<a href=\"{page_url}\">{page_url}</a>.</p>"
            f"<h2>{magnet.doc_title}</h2>{magnet.body_html}"
            f"<hr><p>Подставить сюда реквизиты вашей компании, поля ваших форм и "
            f"найденные на сайте трекеры — это делает LawCheck на тарифе Pro: "
            f"<a href=\"{settings.site_base_url}/pricing\">{settings.site_base_url}/pricing</a></p>")
    bg.add_task(mailer.send_email, email, magnet.doc_title, body)
    if is_new:
        log.info("magnet: %s запросил %s", mask_contact(email), slug)
        bg.add_task(telegram.notify_owner,
                    f"📄 Запросили образец: <b>{telegram.esc(email)}</b>\n{telegram.esc(slug)}")
    return RedirectResponse(url=f"/blog/{slug}?msent=1#obrazec", status_code=303)


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
async def unsubscribe(request: Request, token: str):
    """Отписка от рассылки по токену из письма (ст. 18 ФЗ «О рекламе»).

    Токен ищем и среди лидов с отчёта, и среди заявок из чат-виджета: ссылка
    в футере письма одна, а откуда человек к нам попал — ему знать незачем.
    """
    email = await asyncio.to_thread(repo.unsubscribe_lead, token)
    if not email:
        email = await asyncio.to_thread(repo.unsubscribe_inquiry, token)
    if email:
        title = "Вы отписаны"
        message = (f"Больше не будем писать на {email}. "
                   "Если передумаете — просто запустите проверку сайта заново.")
    else:
        title = "Ссылка недействительна"
        message = "Не нашли подписку по этой ссылке — возможно, вы уже отписались."
    return templates.TemplateResponse(request, "message.html", {
        "title": title, "message": message,
        "cta_href": "/", "cta_label": "На главную",
    })
