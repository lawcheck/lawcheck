import logging
import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
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
