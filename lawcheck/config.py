from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    crawler_max_pages: int = 30
    crawler_timeout_sec: int = 30
    crawler_user_agent: str = "LawCheckBot/0.1 (+https://lawcheck.ru/bot)"

    api_host: str = "0.0.0.0"
    api_port: int = 8000


settings = Settings()
