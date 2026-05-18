# Базовый образ с Python 3.12 + предустановленным Chromium для Playwright.
# Используется и для api, и для worker (одна кодовая база, разный CMD).
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Сначала ставим зависимости — кэшируется отдельно от кода
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Затем кладём приложение
COPY lawcheck ./lawcheck

# По умолчанию — API. Worker запускается с другой CMD через compose.
EXPOSE 8000
CMD ["uvicorn", "lawcheck.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
