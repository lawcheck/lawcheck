import logging

from fastapi import FastAPI

from lawcheck.api.routes import scan
from lawcheck.db.session import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

app = FastAPI(
    title="LawCheck API",
    description="Проверка сайтов на соответствие 152-ФЗ и смежному законодательству РФ",
    version="0.1.0",
)

app.include_router(scan.router, tags=["scan"])


@app.on_event("startup")
def _on_startup() -> None:
    init_db()


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
