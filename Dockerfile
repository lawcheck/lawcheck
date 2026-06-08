# Базовый образ с Python 3.12 (Ubuntu Noble) + предустановленным Chromium
# для Playwright. Используется и для api, и для worker (одна кодовая база,
# разный CMD). noble = Ubuntu 24.04 → Python 3.12 (проект требует >=3.12).
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

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
