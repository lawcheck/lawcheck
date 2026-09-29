"""Обложки статей блога: HTML → JPEG через Chromium из Playwright.

Тот же типографский стиль, что у обложек Дзена (docs/dzen/covers). Картинка
кладётся в static/blog/<slug>.jpg; blog.py сам находит её по слагу и ставит
под заголовок и в og:image. Цифры на обложках – только из текста статьи.

    .venv/bin/python ops/build_blog_covers.py            # все
    .venv/bin/python ops/build_blog_covers.py <slug>...  # выборочно
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "lawcheck" / "web" / "static" / "blog"
PAGE, NAVY, BRAND, CRIT, MUTED = "#f8fafc", "#00053d", "#0b5cff", "#991b1b", "#64748b"
FONT = '-apple-system, "SF Pro Display", "Helvetica Neue", Arial, sans-serif'

CSS = f"""
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ width:1200px; height:630px; background:{PAGE}; font-family:{FONT};
         color:{NAVY}; display:flex; align-items:center; overflow:hidden; }}
  .pad {{ padding:0 84px; width:100%; }}
  .kicker {{ font-size:28px; color:{MUTED}; font-weight:600; }}
  .big {{ font-weight:800; line-height:1; margin-top:14px; white-space:nowrap; }}
  .num {{ font-size:150px; letter-spacing:-5px; }}
  .word {{ font-size:112px; letter-spacing:-3px; }}
  .rule {{ width:84px; height:8px; background:{BRAND}; border-radius:4px; margin:30px 0 26px; }}
  .sub {{ font-size:40px; font-weight:700; line-height:1.25; }}
  .crit {{ color:{CRIT}; }}
  .site {{ position:absolute; bottom:40px; right:84px; font-size:24px;
           color:{MUTED}; font-weight:600; }}
</style>
"""

# slug: (надпись сверху, крупная строка, вид крупной строки, первая строка
# подписи, вторая строка подписи – алым)
COVERS = {
    "besplatnaya-proverka-sajta-na-152-fz": (
        "Бесплатная проверка сайта на 152-ФЗ", "300–5 000 ₽", "num",
        "просят за детали нарушений.", "Что видно без оплаты"),
    "cookie-banner-po-zakonu": (
        "Cookie-баннер по 152-ФЗ", "«Хорошо»", "word",
        "одной кнопки мало –", "нужна возможность отказаться"),
    "informacionnoe-pismo-ob-izmeneniyah-rkn": (
        "Изменения в уведомлении РКН", "До 15-го", "word",
        "числа следующего месяца –", "срок информационного письма"),
    "kak-podat-uvedomlenie-v-rkn": (
        "Уведомление в Роскомнадзор", "30–60 минут", "num",
        "на подачу через Госуслуги,", "если сведения собраны заранее"),
    "kak-projti-proverku-roskomnadzora": (
        "Проверка Роскомнадзора", "Чек-лист", "word",
        "что инспектор смотрит на сайте", "в первую очередь"),
    "lokalizaciya-personalnyh-dannyh-v-rossii": (
        "Локализация персональных данных", "Серверы в РФ", "word",
        "базы с данными россиян –", "только в России"),
    "markirovka-internet-reklamy": (
        "Маркировка интернет-рекламы", "500 000 ₽", "num",
        "максимум юрлицу за рекламу", "без пометки «Реклама»"),
    "obuchenie-personalnym-dannym": (
        "Обучение по персональным данным", "Лист ознакомления", "word",
        "вместо курсов и сертификатов:", "что требует 152-ФЗ на деле"),
    "operator-personalnyh-dannyh": (
        "Оператор персональных данных", "100–300 тыс. ₽", "num",
        "за работу без уведомления", "для юрлиц и ИП"),
    "otvetstvennyj-za-obrabotku-personalnyh-dannyh": (
        "Статья 22.1 152-ФЗ", "Ответственный", "word",
        "с этого вопроса начинается", "почти любая проверка РКН"),
    "peredacha-dannyh-tretim-licam": (
        "Передача данных третьим лицам", "Поручение", "word",
        "нужно на CRM, доставку", "и платёжный сервис"),
    "politika-konfidencialnosti-na-sajte": (
        "Политика обработки ПДн", "Ст. 18.1 152-ФЗ", "word",
        "политика обязательна", "для сайта, который собирает данные"),
    "predpisanie-roskomnadzora-chto-delat": (
        "Предписание Роскомнадзора", "10 рабочих дней", "word",
        "на ответ на запрос РКН.", "Как обжаловать предписание"),
    "registraciya-v-roskomnadzore-ip-ooo": (
        "Регистрация в Роскомнадзоре", "30 дней", "num",
        "на внесение в реестр.", "За неподачу – до 300 000 ₽"),
    "shtraf-za-nepodachu-uvedomleniya-rkn": (
        "Штраф за неподачу уведомления", "300 000 ₽", "num",
        "максимум для юрлиц и ИП", "с 30 мая 2025 года"),
    "shtraf-za-utechku-personalnyh-dannyh": (
        "Утечка персональных данных", "до 15 млн ₽", "num",
        "за первый инцидент.", "За повторный – оборотный штраф"),
    "shtrafy-152-fz-2026": (
        "Штрафы по 152-ФЗ в 2026 году", "до 6 млн ₽", "num",
        "за нарушение локализации", "и трансграничной передачи"),
    "soglasie-na-obrabotku-personalnyh-dannyh": (
        "Согласие на обработку ПДн", "Не склеивать", "word",
        "согласие на рассылку –", "отдельно от согласия на заявку"),
    "uvedomlenie-rkn-komu-nuzhno": (
        "Нужно ли уведомление в РКН", "Большинству – да", "word",
        "кто обязан подавать", "и кто освобождён"),
    "uvedomlenie-rkn-obrazec-zapolneniya": (
        "Уведомление в Роскомнадзор", "Образец", "word",
        "что писать в каждом поле,", "с примерами формулировок"),
    "vozrastnaya-markirovka-sajta-436-fz": (
        "Возрастная маркировка, 436-ФЗ", "0+ … 18+", "num",
        "бизнес-сайту обычно не нужна.", "Кому нужна – до 50 000 ₽"),
    "yandeks-metrika-i-152-fz": (
        "Яндекс Метрика и 152-ФЗ", "Законно", "word",
        "но счётчик включается", "только после согласия"),
    "zarubezhnye-schetchiki-i-trekery-152-fz": (
        "Зарубежные счётчики и пиксели", "За рубеж", "word",
        "идентификаторы посетителей уходят", "на серверы за пределами РФ"),
    "zozpp-dlya-sajta-i-internet-magazina": (
        "ЗОЗПП для интернет-магазина", "5 000–10 000 ₽", "num",
        "штраф юрлицу, если на сайте", "нет информации о продавце"),
}


def cover_html(kicker: str, big: str, kind: str, sub1: str, sub2: str) -> str:
    return CSS + f"""
<div class="pad">
  <div class="kicker">{kicker}</div>
  <div class="big {kind}">{big}</div>
  <div class="rule"></div>
  <div class="sub">{sub1}<br><span class="crit">{sub2}</span></div>
</div>
<div class="site">lawchek.ru</div>
"""


async def main(slugs: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 630},
                                      device_scale_factor=2)
        for slug in slugs:
            await page.set_content(cover_html(*COVERS[slug]))
            # Длинная крупная строка ужимается до ширины обложки, но не мельче 72px.
            overflow = await page.evaluate("""() => {
                const el = document.querySelector('.big');
                let size = parseFloat(getComputedStyle(el).fontSize);
                while (el.scrollWidth > 1200 - 2*84 && size > 72) {
                    size -= 4; el.style.fontSize = size + 'px';
                }
                return el.scrollWidth > 1200 - 2*84;
            }""")
            if overflow:
                raise SystemExit(f"{slug}: крупная строка шире обложки")
            await page.screenshot(path=str(OUT / f"{slug}.jpg"), type="jpeg", quality=82)
            print("готово:", slug)
        await browser.close()


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(COVERS)
    unknown = [s for s in wanted if s not in COVERS]
    if unknown:
        raise SystemExit(f"нет описания обложки: {', '.join(unknown)}")
    asyncio.run(main(wanted))
