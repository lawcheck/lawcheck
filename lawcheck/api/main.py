import logging
import secrets
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from lawcheck.config import settings
from lawcheck.net import force_ipv4

# Контейнер IPv4-only — фильтруем AAAA до любых исходящих запросов
# (Telegram-алерты, API Точки). Иначе httpx падает на IPv6.
force_ipv4()

from lawcheck.api.routes import scan  # noqa: E402
from lawcheck.db.session import init_db  # noqa: E402
from lawcheck.web import routes as web_routes  # noqa: E402

log = logging.getLogger(__name__)
_STATIC_DIR = Path(web_routes.__file__).parent / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


# Методы, которые не меняют состояние, проверять незачем.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


async def _reject_cross_origin(request: Request, call_next):
    """CSRF-защита проверкой Origin: чужой сайт не должен слать нам формы.

    Cookie-сессия помечена SameSite=lax, и этого почти достаточно. Почти —
    потому что вся защита держится на одной настройке, а `strict` поставить
    нельзя: возврат из банка на /pay/success приходит кросс-сайтовым переходом,
    и без cookie покупатель попал бы на свой же отчёт как посторонний.

    Проверяем Origin, а не токен в форме: одно место вместо скрытого поля в
    каждом шаблоне, и новые формы закрыты автоматически. Origin браузер шлёт
    на любой POST; его отсутствие — это curl, cron или вебхук банка, поэтому
    пустой заголовок пропускаем. Сравниваем с Host запроса, а не с настройкой:
    так одинаково работает и прод, и локальная разработка.
    """
    if request.method not in _SAFE_METHODS:
        origin = request.headers.get("origin") or ""
        host = (request.headers.get("host") or "").lower()
        # Вебхуки банка и Telegram приходят без Origin и не от браузера.
        if origin and host and urlparse(origin).netloc.lower() != host:
            log.warning("csrf: отклонён POST %s с Origin %s (host %s)",
                        request.url.path, origin, host)
            return PlainTextResponse("cross-origin request rejected", status_code=403)
    return await call_next(request)


# Метрика — единственный внешний источник скриптов, картинок и фреймов.
# Доменов два. Загрузчик `tag.js` приходит с mc.yandex.ru, но хиты счётчик
# шлёт либо туда же, либо на mc.yandex.com — домен он выбирает сам, судя по
# региону посетителя. Пока в списке был только .ru, у посетителей, которым
# достался .com, CSP резал вообще всё: `watch`, `sync_cookie_image_check` и
# callback-скрипт (проверено в браузере 10.08.2026). Такой визит Метрика не
# видит ни в каком источнике — его просто нет.
_METRIKA = "https://mc.yandex.ru https://mc.yandex.com"
# Счётчик держит ещё и вебсокет (`wss://mc.yandex.com/solid.ws`). Для WebSocket
# источник сравнивается вместе со схемой, поэтому `https://…` его не покрывает
# и домены приходится перечислять второй раз под `wss://`.
_METRIKA_WS = "wss://mc.yandex.ru wss://mc.yandex.com"
# Страница оплаты Точки — единственный внешний адресат наших форм.
_BANK = "https://merch.tochka.com"


def _csp(nonce: str) -> str:
    return "; ".join([
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'self'",
        # Касса банка нужна здесь явно. form-action проверяется НА ВСЕЙ цепочке
        # навигации формы, включая редиректы: POST /buy/pro отвечает 303 на
        # merch.tochka.com, и под `form-action 'self'` браузер этот переход
        # молча блокирует — кнопка «Перейти к оплате» просто перестаёт работать.
        # Ошибка видна только в консоли, сервер при этом отвечает 303 как ни в
        # чём не бывало. Ровно так оплата легла 05.08.2026.
        f"form-action 'self' {_BANK}",
        f"script-src 'self' 'nonce-{nonce}' {_METRIKA}",
        # Инлайн-стили: в шаблонах десятки атрибутов style="…", и nonce на
        # атрибуты не действует в принципе. Риск от них несопоставим со
        # скриптами, а вычищать их — отдельная работа по вёрстке.
        "style-src 'self' 'unsafe-inline'",
        f"img-src 'self' data: {_METRIKA}",
        "font-src 'self'",
        f"connect-src 'self' {_METRIKA} {_METRIKA_WS}",
        f"frame-src {_METRIKA}",
    ])


async def _content_security_policy(request: Request, call_next):
    """CSP с одноразовым nonce на запрос.

    Отчёт показывает данные с чужих страниц: адрес сайта, цитаты находок, тексты
    форм и cookie-баннеров. Сейчас всё это экранирует Jinja, и дыры нет — CSP
    здесь второй рубеж на случай, когда однажды кто-то напишет `|safe`.

    Nonce, а не 'unsafe-inline': инлайн-скриптов в шаблонах полтора десятка,
    включая JSON-LD (его браузер тоже блокирует без разрешения, и разметка
    молча пропадает из поиска). Каждому проставлен `nonce="{{ csp_nonce(...) }}"`,
    а тест `test_csp.py` следит, чтобы новый тег не забыли.
    """
    nonce = secrets.token_urlsafe(16)
    request.state.csp_nonce = nonce
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", _csp(nonce))
    return response


async def _load_session_user(request: Request, call_next):
    """Подтянуть пользователя сессии один раз за запрос (см. web/deps)."""
    from lawcheck.web import deps
    await deps.load_user(request)
    return await call_next(request)


async def _capture_ad_entry(request: Request, call_next):
    """Запомнить адрес входа: рекламную метку для Метрики и первое касание
    визита для атрибуции заказа (см. web/deps.remember_ad_entry / remember_entry)."""
    from lawcheck.web import deps
    deps.remember_ad_entry(request)
    deps.remember_entry(request)
    return await call_next(request)


async def _handle_head(request: Request, call_next):
    """HEAD → GET без тела.

    Uvicorn не обрабатывает HEAD автоматически для FastAPI-маршрутов:
    GET-эндпойнт отвечает 200, а HEAD на тот же URL — 405 Method Not Allowed.
    Googlebot иногда проверяет страницы через HEAD, и 405 означает «страница
    недоступна». Middleware ловит HEAD до всех остальных, дёргает GET и
    возвращает его заголовки без тела — ровно то, что RFC 7231 требует от HEAD.
    """
    if request.method == "HEAD":
        request.scope["method"] = "GET"
        response = await call_next(request)
        # ASGI response body — асинхронный поток; читаем его до конца,
        # чтобы внутренние middleware (CSP, сжатие) сделали своё дело,
        # но клиенту ничего не возвращаем.
        async for _ in response.body_iterator:
            pass
        response.body_iterator = _empty_body()
        response.headers["content-length"] = "0"
        return response
    return await call_next(request)


async def _empty_body():
    """Пустой async-генератор для тела ответа."""
    return
    yield  # pragma: no cover — yield превращает функцию в генератор


def create_app() -> FastAPI:
    app = FastAPI(
        title="LawCheck API",
        description="Проверка сайтов на соответствие 152-ФЗ и смежному законодательству РФ",
        version="0.1.0",
    )

    # Cookie-сессии для аккаунтов. В проде секрет задаётся в .env (SESSION_SECRET);
    # если пуст — генерируем эфемерный на процесс (dev: вход работает, но
    # слетает при рестарте). В проде обязательно задать постоянный.
    secret = settings.session_secret
    if not secret:
        secret = secrets.token_hex(32)
        log.warning("SESSION_SECRET не задан — использую эфемерный секрет "
                    "(сессии сбросятся при рестарте). В проде задайте SESSION_SECRET в .env.")
    # Порядок важен: последний добавленный оборачивает остальные, то есть
    # выполняется первым. Нужно HEAD → session → csrf → csp → загрузка
    # пользователя → рекламная метка, поэтому добавляем в обратном порядке.
    app.add_middleware(BaseHTTPMiddleware, dispatch=_capture_ad_entry)
    app.add_middleware(BaseHTTPMiddleware, dispatch=_load_session_user)
    app.add_middleware(BaseHTTPMiddleware, dispatch=_content_security_policy)
    app.add_middleware(BaseHTTPMiddleware, dispatch=_reject_cross_origin)
    app.add_middleware(BaseHTTPMiddleware, dispatch=_handle_head)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="lc_session",
        same_site="lax",
        https_only=settings.site_base_url.startswith("https://"),
        max_age=60 * 60 * 24 * 30,  # 30 дней
    )

    app.include_router(scan.router, prefix="/api", tags=["scan"])
    app.include_router(web_routes.router, tags=["web"])
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.on_event("startup")
    def _on_startup() -> None:
        init_db()

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
