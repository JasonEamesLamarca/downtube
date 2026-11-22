import asyncio
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
        for f in sys.argv[1:]:
            svg = Path(f).read_text()
            w, h = map(int, re.search(r'width="(\d+)" height="(\d+)"', svg).groups())
            pg = await b.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
            await pg.set_content(f'<html><body style="margin:0">{svg}</body></html>')
            await pg.screenshot(path=str(Path(f).with_suffix(".png")))
            await pg.close()
        await b.close()


asyncio.run(main())
