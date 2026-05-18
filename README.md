# LawCheck

Сервис автоматической проверки сайтов на соответствие российскому законодательству.

## Скоуп MVP

- **152-ФЗ** «О персональных данных» — Политика, формы, согласия, реестр операторов РКН, трансграничная передача
- **Cookies и трекеры** — инвентаризация сторонних скриптов, cookie-баннер
- **Реквизиты владельца** — извлечение и валидация ИНН/ОГРН, сверка с ЕГРЮЛ

Дальнейшие фазы: ЗОЗПП и Роспотребнадзор, ФЗ «О рекламе», 436-ФЗ, 149-ФЗ.

## Стек

Python 3.12 · FastAPI · Playwright · PostgreSQL · Redis · RQ

## Структура

```
lawcheck/
├── api/            HTTP-слой (FastAPI)
├── crawler/        Сбор «слепка» сайта (Playwright)
├── checks/         Проверки — ядро бизнес-логики
├── dictionaries/   Словари маркеров и трекеров (YAML)
├── external/       Клиенты внешних реестров (РКН, ЕГРЮЛ)
├── reporting/      Сборка отчёта
├── workers/        Воркеры очереди
├── db/             Модели и миграции
└── utils/
```

## Запуск (dev)

Требуется Python 3.12. Менеджер — [uv](https://docs.astral.sh/uv/).

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
.venv/bin/playwright install chromium

# тесты
.venv/bin/python -m pytest tests/ -q

# API (sqlite-файл lawcheck.db создаётся автоматически)
.venv/bin/uvicorn lawcheck.api.main:app --reload
```

Открыть http://127.0.0.1:8000/docs и сделать `POST /scan` с телом
`{"url": "https://example.ru", "max_pages": 10}`. В ответ — `scan_id`,
результат — `GET /scan/{scan_id}`.

В dev-режиме сканирование идёт прямо в процессе API через FastAPI
BackgroundTasks (Redis не нужен).

## Запуск (prod, Docker Compose)

```bash
docker compose up -d --build
```

Поднимается:
- `postgres` — БД сканов
- `redis` — брокер очереди
- `api` — FastAPI на `:8000`
- `worker` — RQ-воркер, обрабатывает задачи из очереди `lawcheck`

API автоматически переключается на RQ, как только видит непустой
`REDIS_URL`. Можно запустить несколько воркеров для параллельных
сканирований:

```bash
docker compose up -d --scale worker=3
```

## Дисклеймер

Результаты анализа носят информационный и рекомендательный характер. Сервис не оказывает юридических услуг.
