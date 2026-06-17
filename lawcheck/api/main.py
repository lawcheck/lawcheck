import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from lawcheck.net import force_ipv4

# Контейнер IPv4-only — фильтруем AAAA до любых исходящих запросов
# (Telegram-алерты, API Точки). Иначе httpx падает на IPv6.
force_ipv4()

from lawcheck.api.routes import scan  # noqa: E402
from lawcheck.db.session import init_db  # noqa: E402
from lawcheck.web import routes as web_routes  # noqa: E402

_STATIC_DIR = Path(web_routes.__file__).parent / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

app = FastAPI(
    title="LawCheck API",
    description="Проверка сайтов на соответствие 152-ФЗ и смежному законодательству РФ",
    version="0.1.0",
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
