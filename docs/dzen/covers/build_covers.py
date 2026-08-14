"""Обложки для статей в Дзене: HTML → PNG через Chromium из Playwright.

Обложки типографские, генератор картинок не нужен. Цвета — из base.html,
чтобы лента и сайт выглядели одним хозяйством.

    .venv/bin/python docs/dzen/covers/build_covers.py
"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).parent
PAGE, NAVY, BRAND, CRIT, MUTED = "#f8fafc", "#00053d", "#0b5cff", "#991b1b", "#64748b"
FONT = '-apple-system, "SF Pro Display", "Helvetica Neue", Arial, sans-serif'

BASE = f"""
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ width:1280px; height:720px; background:{PAGE}; font-family:{FONT};
         color:{NAVY}; display:flex; align-items:center; overflow:hidden; }}
  .pad {{ padding:0 90px; width:100%; }}
  .kicker {{ font-size:26px; color:{MUTED}; font-weight:600; letter-spacing:.2px; }}
  .huge {{ font-size:230px; font-weight:800; letter-spacing:-8px; line-height:.95; }}
  .sub {{ font-size:44px; font-weight:700; line-height:1.25; margin-top:18px; }}
  .accent {{ color:{BRAND}; }}
  .crit {{ color:{CRIT}; }}
  .rule {{ width:88px; height:8px; background:{BRAND}; border-radius:4px; margin:34px 0; }}
  .site {{ position:absolute; bottom:46px; right:90px; font-size:24px;
           color:{MUTED}; font-weight:600; }}
</style>
"""

COVERS = {
    "01-385-saitov.png": BASE + f"""
<div class="pad">
  <div class="kicker">Проверил сайты малого бизнеса на 152-ФЗ</div>
  <div class="huge">385</div>
  <div class="rule"></div>
  <div class="sub">сайтов проверено.<br><span class="crit">Чистым оказался один</span></div>
</div>
<div class="site">lawchek.ru</div>
""",
    "02-shest-servisov.png": BASE + f"""
<style>
  .row {{ display:flex; gap:18px; margin:38px 0 0; }}
  .card {{ flex:1; background:#fff; border:2px solid #e2e8f0; border-radius:18px;
           padding:26px 0; text-align:center; }}
  .card .n {{ font-size:76px; font-weight:800; color:{CRIT}; line-height:1; }}
  .card .l {{ font-size:19px; color:{MUTED}; margin-top:10px; font-weight:600; }}
</style>
<div class="pad">
  <div class="kicker">Один сайт, шесть проверок на 152-ФЗ</div>
  <div class="sub" style="font-size:52px; margin-top:10px">Нарушений насчитали<br><span class="accent">от 2 до 6</span></div>
  <div class="row">
    <div class="card"><div class="n">6</div><div class="l">saitscan</div></div>
    <div class="card"><div class="n">5</div><div class="l">vlip</div></div>
    <div class="card"><div class="n">5</div><div class="l">LawCheck</div></div>
    <div class="card"><div class="n">3</div><div class="l">help152</div></div>
    <div class="card"><div class="n">2</div><div class="l">quickaudit</div></div>
    <div class="card"><div class="n">2</div><div class="l">1ps</div></div>
  </div>
</div>
""",
    "03-otkuda-milliony.png": BASE + f"""
<style>
  .sums {{ display:flex; align-items:center; gap:44px; margin-top:34px; }}
  .sum {{ font-size:80px; font-weight:800; letter-spacing:-2px; }}
  .vs {{ font-size:44px; color:{MUTED}; font-weight:700; }}
</style>
<div class="pad">
  <div class="kicker">Оценка штрафа за один и тот же сайт</div>
  <div class="sums">
    <div class="sum">925 000 ₽</div>
    <div class="vs">против</div>
    <div class="sum crit">6 525 000 ₽</div>
  </div>
  <div class="rule"></div>
  <div class="sub">Откуда сервисы берут<br>эти миллионы</div>
</div>
<div class="site">lawchek.ru</div>
""",
}

AVATAR = f"""
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ width:400px; height:400px; background:{NAVY}; font-family:{FONT};
          display:flex; flex-direction:column; align-items:center;
          justify-content:center; color:#fff; }}
  .mono {{ font-size:150px; font-weight:800; letter-spacing:-6px; line-height:1; }}
  .dot {{ width:44px; height:8px; background:{BRAND}; border-radius:4px; margin-top:22px; }}
</style>
<div class="mono">МП</div><div class="dot"></div>
"""


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for name, html in COVERS.items():
            page = await browser.new_page(viewport={"width": 1280, "height": 720},
                                          device_scale_factor=2)
            await page.set_content(html)
            await page.screenshot(path=str(OUT / name))
            await page.close()
            print("готово:", name)
        page = await browser.new_page(viewport={"width": 400, "height": 400},
                                      device_scale_factor=2)
        await page.set_content(AVATAR)
        await page.screenshot(path=str(OUT / "avatar-monogram.png"))
        await page.close()
        print("готово: avatar-monogram.png")
        await browser.close()


asyncio.run(main())
