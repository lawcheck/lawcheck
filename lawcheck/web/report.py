"""Страница отчёта: диагноз бесплатно, рецепты «Как исправить» — по оплате.

Здесь же живёт правило доступа к платной части (`unlock_order_id`) и политика
индексации отчётов: они завязаны друг на друга и на витрину `INDEXABLE_REPORTS`,
которую заявляет sitemap.

Под-роутер по образцу web/blog.py: `templates` проставляется из web/routes.py.
"""
import asyncio
import logging
from collections import defaultdict

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from lawcheck.config import settings
from lawcheck.db import repo
from lawcheck.notify import telegram
from lawcheck.reporting import fines, policy_draft, rkn_notification_draft
from lawcheck.utils.contact import mask_contact
from lawcheck.utils.email import valid_email
from lawcheck.web import deps

log = logging.getLogger(__name__)

router = APIRouter()
templates: Jinja2Templates = None  # type: ignore[assignment]  # задаётся из routes.py


# Отчёты по умолчанию закрыты от индексации, и вот почему. К 28.07.2026 Яндекс
# держал в поиске 105 страниц сайта, почти все — `/report/{id}` о чужих сайтах.
# Профиль запросов домена определяли не «уведомление РКН» и «152-ФЗ», а названия
# порносайтов: 14 из топ-20 запросов и ~70% показов приходились на людей, искавших
# отчёт по adult-домену, который кто-то прогнал через сканер. Плюс в индексе лежали
# заключения о нарушениях на поддоменах Госуслуг и у крупных ритейлеров — публичное
# обвинение чужого сайта в нарушении закона от лица юридического сервиса.
#
# Фильтр `_FEED_BLOCK_SUBSTRINGS` убирает такие домены из ленты на главной, но сам
# отчёт остаётся доступным по ссылке и индексируемым — лента лишь один из путей,
# которым робот о нём узнаёт.
#
# Ссылка продолжает работать: `noindex` закрывает поиск, а не доступ.
#
# Набор ниже — фиксированная витрина из ранних проверок: обычные небольшие
# коммерческие сайты. Намеренно не входят adult, госдомены, медицина
# (спецкатегория ПДн), юрфирмы, конкурирующие сканеры, тестовые хосты и URL
# с токенами в query. Список меняется только руками.
INDEXABLE_REPORTS = frozenset({
    "a3b79625c0b74c10adda48a571016583",  # seltex-iv.ru
    "3c67c09aaaaa4e158e83ce3eda2e03c7",  # wako-lab.ru
    "6b0d701982a443a2886f362a06fdc359",  # caspiancluster.ru
    "d4596cf540524275ae2fdab71ea8d4a3",  # loftpromusic.ru
    "a9d2507fe94c42f0bb1a37ca3e047abc",  # dilerpro.ru
    "c13654dc73134e2ca6716113bf51ae8a",  # hardkam.ru
    "1152495aadbc433e937c44fc58c95b7a",  # yarfanera.ru
    "b77c041e036a4510b735b932a3fdbde0",  # kortingshop.ru
    "d037e3b17bcd4d848047358cc3823342",  # stroyhub03.ru
    "8d52d5441af242ceadc90ea17a13681d",  # smrtour.ru
})


def _indexing(response: Response, scan_id: str) -> Response:
    """Закрыть отчёт от поиска, если он не из фиксированной витрины."""
    if scan_id not in INDEXABLE_REPORTS:
        response.headers["X-Robots-Tag"] = "noindex, follow"
    return response



def _channel_secure(scan) -> bool:
    """Рисовать ли в шапке отчёта бейдж «HTTPS».

    Берём результат проверки I1, а не строку адреса. Схему `https://` мы
    подставляем сами, когда посетитель ввёл голый домен, а краулер ходит с
    `ignore_https_errors=True` — то есть по адресу нельзя судить ни о чём.

    У сканов, снятых до появления I1, данных нет, и бейдж не рисуем: он
    утверждает, что канал защищён, а мы этого не проверяли.
    """
    for f in scan.findings:
        if f.check_id.split(".")[0] == "I1":
            return bool((f.extra or {}).get("cert_valid"))
    return False



_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}

# Секции отчёта в порядке отображения: (slug-якорь, заголовок, [префиксы check_id])
_BLOCK_DEFS = [
    ("pdn", "Политика обработки ПДн", ["A1", "A2", "A3"]),
    ("forms", "Формы и согласия", ["B1", "B2"]),
    ("cookies", "Cookies и трекеры", ["D1", "D2"]),
    ("owner", "Реквизиты владельца", ["E1", "E2"]),
    ("rkn", "Реестр операторов РКН", ["C2"]),
    ("zozpp", "ЗОЗПП и Правила продажи", ["F1", "F2", "F3"]),
    ("ads", "ФЗ «О рекламе»", ["G1", "G2", "G3"]),
    ("kids", "Защита детей (436-ФЗ)", ["H1"]),
    ("channel", "Защита канала", ["I1"]),
]


# Сколько рекомендаций «Как исправить» открыто в бесплатном отчёте.
# Диагноз (что сломано, цитата, штраф) открыт всегда; рецепты сверх лимита — в Pro.
FREE_RECIPES = 2


async def unlock_order_id(request: Request, scan, order: str = "") -> str | None:
    """id заказа, дающего ЭТОМУ посетителю полный доступ к отчёту скана.

    Три пути, и все три — про личность посетителя, а не про факт оплаты скана:
    1. `?order=<id>` в ссылке — та же капабилити-модель, что у /account/{order_id};
    2. заказ, уже предъявленный в этой сессии (после оплаты или по такой ссылке);
    3. владелец скана с действующей Pro-подпиской.

    Предъявленный заказ обязан быть оформлен именно с этого отчёта — чужой
    оплаченный заказ чужой отчёт не открывает.
    """
    candidates = [order, *deps.session_order_ids(request)]
    oid = await asyncio.to_thread(repo.paid_order_id_for_scan, scan.id, candidates)
    if oid:
        deps.remember_order(request, oid)
        return oid
    user = await deps.current_user(request)
    if user is not None and scan.user_id == user.id:
        return await asyncio.to_thread(repo.latest_paid_order_id_for_user, user.id)
    return None


@router.get("/report/{scan_id}/documents", response_class=HTMLResponse)
async def report_documents(request: Request, scan_id: str, order: str = ""):
    """Авто-черновик Политики ПДн + текста согласия под конкретный сайт (Pro)."""
    scan = await asyncio.to_thread(repo.get_scan, scan_id)
    if scan is None or scan.status != "done":
        raise HTTPException(status_code=404, detail="scan not found")
    if not await unlock_order_id(request, scan, order):
        return RedirectResponse(url=f"/pricing?scan={scan_id}", status_code=303)
    html = await asyncio.to_thread(policy_draft.render, scan)
    return HTMLResponse(content=html)


@router.get("/report/{scan_id}/rkn-notification", response_class=HTMLResponse)
async def report_rkn_notification(request: Request, scan_id: str, order: str = ""):
    """Черновик уведомления в РКН под конкретный сайт (Pro)."""
    scan = await asyncio.to_thread(repo.get_scan, scan_id)
    if scan is None or scan.status != "done":
        raise HTTPException(status_code=404, detail="scan not found")
    if not await unlock_order_id(request, scan, order):
        return RedirectResponse(url=f"/pricing?scan={scan_id}", status_code=303)
    html = await asyncio.to_thread(rkn_notification_draft.render, scan)
    return HTMLResponse(content=html)


@router.get("/report/{scan_id}", response_class=HTMLResponse)
async def report(request: Request, scan_id: str, sub: int = 0, order: str = ""):
    scan = await asyncio.to_thread(repo.get_scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")

    if order:
        # Заказ предъявлен ссылкой. Запоминаем его в сессии и уводим на чистый
        # URL: иначе id заказа остаётся в адресной строке, в истории браузера и
        # уезжает в Яндекс.Метрику, которая шлёт путь и query текущей страницы.
        await unlock_order_id(request, scan, order)
        clean = f"/report/{scan_id}" + ("?sub=1" if sub else "")
        return RedirectResponse(url=clean, status_code=303)

    by_prefix: dict[str, list] = defaultdict(list)
    counts = {"critical": 0, "warning": 0, "info": 0, "ok": 0}
    for f in scan.findings:
        by_prefix[f.check_id.split(".")[0]].append(f)
        counts[f.severity] = counts.get(f.severity, 0) + 1

    blocks = []
    for slug, title, prefixes in _BLOCK_DEFS:
        items = [f for p in prefixes for f in by_prefix.get(p, [])]
        if not items:
            continue
        items.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.check_id))
        problems = [f for f in items if f.severity != "ok"]
        oks = [f for f in items if f.severity == "ok"]
        worst = problems[0].severity if problems else "ok"
        blocks.append({
            "slug": slug, "title": title,
            "problems": problems, "oks": oks, "worst": worst,
        })

    total = sum(counts.values())
    compliance = round(counts["ok"] / total * 100) if total else 0

    # Gate рецептов: тизер открываем у НАИМЕНЕЕ тяжёлых находок, а фиксы за
    # самый крупный риск держим под замком — иначе бесплатно раздаётся ровно
    # то, за что платят (см. вики free-report-gating, замер воронки 2026-07-21).
    # Оплаченный заказ с этим scan_id снимает замок со всех рецептов.
    all_problems = sorted(
        (f for f in scan.findings if f.severity != "ok" and f.recommendation),
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.check_id),
    )
    # Разблокировка «Как исправить»: (1) разовая покупка с этого отчёта, либо
    # (2) Pro-подписка — залогиненный ВЛАДЕЛЕЦ скана с оплаченным заказом видит
    # свои отчёты открытыми целиком (чужие сканы так не открываются).
    unlocked_order = await unlock_order_id(request, scan, order)
    unlocked = bool(unlocked_order)
    cabinet_href = f"/account/{unlocked_order}" if unlocked_order else "/dashboard"
    # База для ссылок на готовый текст в шаблонах (доступна при оплаченном заказе).
    templates_href = f"/account/{unlocked_order}/templates" if unlocked_order else ""
    if unlocked:
        open_rec_ids = {f.id for f in all_problems}
        locked_count = 0
    else:
        # all_problems отсортированы critical→info, поэтому «хвост» — наименее
        # тяжёлые находки: их рецепты и показываем как тизер качества.
        free_sample = all_problems[-FREE_RECIPES:] if FREE_RECIPES else []
        open_rec_ids = {f.id for f in free_sample}
        locked_count = max(0, len(all_problems) - len(open_rec_ids))

    return _indexing(templates.TemplateResponse(request, "report.html", {
        "scan": scan,
        "blocks": blocks,
        "counts": counts,
        "compliance": compliance,
        "risk": fines.risk_total(scan.findings),
        "is_https": _channel_secure(scan),
        "is_active": scan.status in ("pending", "running"),
        "open_rec_ids": open_rec_ids,
        "locked_count": locked_count,
        "unlocked": unlocked,
        "unlock_order_id": unlocked_order or "",
        "cabinet_href": cabinet_href,
        "templates_href": templates_href,
        "subscribed": bool(sub),
    }), scan_id)


@router.post("/report/{scan_id}/subscribe", response_class=HTMLResponse)
async def report_subscribe(request: Request, scan_id: str, bg: BackgroundTasks,
                           email: str = Form(...)):
    scan = await asyncio.to_thread(repo.get_scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")
    email = email.strip().lower()
    if valid_email(email):
        if await asyncio.to_thread(repo.create_lead, scan_id, scan.url, email):
            log.info("lead: %s (скан %s, %s)", mask_contact(email), scan_id[:8], scan.url)
            await asyncio.to_thread(repo.nurture_subscribe, email)
            bg.add_task(telegram.notify_owner,
                        f"📩 Новый лид: <b>{telegram.esc(email)}</b>\nсайт: {telegram.esc(scan.url)}\n"
                        f"отчёт: {settings.site_base_url}/report/{scan_id}")
    return RedirectResponse(url=f"/report/{scan_id}?sub=1", status_code=303)
