"""Аватар для Дзена из портрета с сайта: квадрат 400x400, лицо крупно."""
import asyncio
import base64
import sys
from pathlib import Path

from playwright.async_api import async_playwright

SRC = Path("lawcheck/web/static/lawyer-podolskiy.jpg")
OUT = Path("docs/dzen/covers/avatar.png")

# подбирается на глаз по превью: масштаб и сдвиг исходника внутри квадрата
SCALE = float(sys.argv[1]) if len(sys.argv) > 1 else 1.85
OX = float(sys.argv[2]) if len(sys.argv) > 2 else -165
OY = float(sys.argv[3]) if len(sys.argv) > 3 else -79

W, H = 384, 480
b64 = base64.b64encode(SRC.read_bytes()).decode()

HTML = f"""
<style>
  * {{ margin:0; padding:0; }}
  body {{ width:400px; height:400px; overflow:hidden;
          background-image:url(data:image/jpeg;base64,{b64});
          background-size:{W * SCALE:.0f}px {H * SCALE:.0f}px;
          background-position:{OX:.0f}px {OY:.0f}px;
          background-repeat:no-repeat; }}
</style>
"""


async def main() -> None:
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        p = await b.new_page(viewport={"width": 400, "height": 400},
                             device_scale_factor=2)
        await p.set_content(HTML)
        await p.screenshot(path=str(OUT))
        await b.close()
    print(f"готово: {OUT}  scale={SCALE} offset=({OX:.0f},{OY:.0f})")


asyncio.run(main())
