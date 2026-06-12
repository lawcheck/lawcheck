from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    crawler_max_pages: int = 30
    crawler_timeout_sec: int = 30
    crawler_user_agent: str = "LawCheckBot/0.1 (+https://lawchek.ru/bot)"

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # По умолчанию — локальный sqlite, чтобы dev не требовал Docker.
    # В проде указываем postgresql+psycopg://... через env.
    database_url: str = "sqlite:///lawcheck.db"

    # Если пусто — задачи выполняются через FastAPI BackgroundTasks (dev-режим).
    # В проде: redis://redis:6379/0 — задачи идут в RQ-воркер.
    redis_url: str = ""

    # Интернет-эквайринг Точка (https://developers.tochka.com).
    # Пока tochka_jwt пуст — оплата работает в fallback-режиме (заявка на email).
    tochka_jwt: str = ""
    tochka_customer_code: str = ""
    tochka_merchant_id: str = ""
    tochka_base_url: str = "https://enter.tochka.com/uapi"
    site_base_url: str = "https://lawchek.ru"


settings = Settings()
