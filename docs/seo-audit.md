# SEO-аудит LawCheck — 2026-07-27

## Сводка

| Категория Lighthouse (desktop) | Балл |
|---|---|
| **SEO** | **100/100** |
| Performance | 99/100 |
| Best Practices | 100/100 |
| Accessibility | 94/100 |

## Core Web Vitals (desktop, lawchek.ru)

| Метрика | Значение | Статус |
|---|---|---|
| LCP | 0.4 s | 🟢 |
| FCP | 0.4 s | 🟢 |
| CLS | 0 | 🟢 |
| TBT | 0 ms | 🟢 |
| Speed Index | 0.6 s | 🟢 |

## Что уже сделано (база — отличная)

- `robots.txt`: `User-agent: * / Allow: / / Sitemap: https://lawchek.ru/sitemap.xml`
- Динамический `sitemap.xml`: 26 URL (главная, pricing, privacy, oferta, 2 лендинга РКН, блог — 15 статей, 5 нишевых лендингов `/proverka/{niche}`)
- Canonical на каждой странице (`{{ site_base_url ~ request.url.path }}`)
- OG-теги (site_name, type, title, description, url, image)
- `<meta name="yandex-verification" content="89b1f3af94c61af6">`
- `<html lang="ru">`
- Шрифты самохостятся (`/static/fonts/`), нет запросов к Google Fonts → нет передачи IP за рубеж (требование 152-ФЗ)

## Найденные проблемы

### P0 — нет JSON-LD микроразметки → РЕАЛИЗОВАНО ЛОКАЛЬНО (2026-07-27)

Все блоки JSON-LD добавлены в шаблоны (несмерженный diff в рабочей копии):

| Шаблон | Типы JSON-LD | Статус |
|---|---|---|
| `base.html` | Organization, WebSite | глобально на всех страницах |
| `index.html` | Service, FAQPage | главная |
| `pricing.html` | SoftwareApplication, FAQPage | тарифы |
| `landing_rkn.html` | FAQPage | лендинг уведомления РКН |
| `blog_article.html` | Article | статьи блога (было ранее) |
| `landing_niche.html` | Service | нишевые лендинги |

Проверено локально (uvicorn :8899): все типы рендерятся корректно.
**Что осталось**: закоммитить + задеплоить на прод.

### P1 — нет HTTP security headers
Caddy не отдаёт: Content-Security-Policy, X-Frame-Options, Strict-Transport-Security, X-Content-Type-Options.

Caddyfile:
```
header {
  Strict-Transport-Security "max-age=31536000; includeSubDomains"
  X-Content-Type-Options "nosniff"
  X-Frame-Options "SAMEORIGIN"
  Referrer-Policy "strict-origin-when-cross-origin"
}
```

### P1 — кэширование статики
Lighthouse: «Est savings of 177 KiB» — нет `Cache-Control` для `/static/*`.

Caddyfile:
```
@static path /static/*
header @static Cache-Control "public, max-age=31536000, immutable"
```

### P2 — accessibility (косвенно SEO)
Известные проблемы из `docs/marketing-review.md` (P0 a11y):
- Контраст `--faint` на `*-soft` фонах
- Порядок заголовков (heading order)
- Table headers в сравнительной таблице (`.cmp`)
- Accessible names у элементов с видимым текстом

## Sitemap (состояние на 2026-07-27)

26 URL в sitemap:
- `/` — главный лендинг
- `/pricing`, `/privacy`, `/oferta`
- `/uvedomlenie-rkn`, `/reestr-rkn` — лендинги РКН
- `/blog` + 15 статей (даты: 2026-06-09 до 2026-07-19)
- `/proverka/{kliniki|internet-magaziny|onlajn-shkoly|salony-krasoty|fitnes-kluby}`

`seo_enabled = True` в проде (блог и лендинги попадают в sitemap).

## Контентный план: пробелы блога (Wordstat, июль 2026)

Сопоставление 15 блог-статей с Wordstat-частотами (из `lawcheck-demand-wordstat-2026-07`).

### P0 — новые статьи под высокий спрос

| Запрос (Wordstat) | Показов/мес | Почему важно |
|---|---|---|
| **оператор персональных данных** | 37 000 | Крупнейший gap. Прямая связь с продуктом: LawCheck проверяет статус оператора. |
| **маркировка рекламы** | 9 800 | LawCheck уже проверяет (test_g_advertising). Статьи нет. |
| **политика обработки персональных данных** | 14 124 | Текущая статья называется «Политика конфиденциальности», не матчит. Расширить или alias. |

### P1 — средний спрос, прямая связь с продуктом

| Запрос | Показов/мес | Тип |
|---|---|---|
| ответственный за обработку ПДн | 3 890 | Новая статья |
| обучение по персональным данным | 2 194 | Новая статья |
| возрастная маркировка 436-ФЗ | растёт | Новая статья (есть проверка в продукте) |
| согласие на обработку образец/бланк | хвост 116к | Лид-магнит: шаблон за email |
| уведомление РКН бланк/форма | хвост 15к | Лид-магнит: шаблон уведомления |

### P2 — низкий спрос, доп. покрытие

| Запрос | Показов/мес | Тип |
|---|---|---|
| аудит персональных данных | 1 093 | Перенаправить интент |
| ЗОЗПП на сайте: возврат, доставка | есть | Новая статья (есть проверка в продукте) |

## Инструменты

- **Lighthouse CLI 13.4.1** (установлен глобально: `npm i -g lighthouse`)
- Hermes-скилл `seo-audit` — полный процесс аудита с командами

## Повторные прогоны

```bash
# Desktop
lighthouse https://lawchek.ru/ --output=html --output-path=./lh-desktop.html --preset=desktop --quiet

# Mobile
lighthouse https://lawchek.ru/ --output=html --output-path=./lh-mobile.html --quiet

# Только метрики
lighthouse https://lawchek.ru/ --output=json --quiet | python3 -c "
import sys, json
r = json.load(sys.stdin)
for k in ['seo','performance','accessibility','best-practices']:
    s = r['categories'].get(k,{}).get('score',0)
    print(f'{k}: {s*100:.0f}/100' if s else f'{k}: N/A')
"
```
