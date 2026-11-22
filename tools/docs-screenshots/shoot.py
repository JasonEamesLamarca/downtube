"""Capture DownTube UI screenshots with Playwright (usage: shoot.py <demo_dir> <out_dir>)."""

import asyncio
import sys

from playwright.async_api import async_playwright

SP = sys.argv[1]
OUT = sys.argv[2]
sess = open(f"{SP}/sess").read().strip()
code = [l for l in open(f"{SP}/a.log") if "setup code" in l][-1].split()[-1]
MASK = r"""(() => {
  const fix = t => t.replace(/\/tmp\/[^\s]*?\/c\/archive/g, '/archive').replace(/http:\/\/localhost:880\d/g, 'https://downtube.example.com');
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n; while ((n = w.nextNode())) { const v = fix(n.nodeValue); if (v !== n.nodeValue) n.nodeValue = v; }
  for (const i of document.querySelectorAll('input')) { const v = fix(i.value || ''); if (v !== i.value) i.value = v; }
})()"""


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
        # setup page (fresh instance) — light & filled
        ctx = await b.new_context(
            viewport={"width": 1360, "height": 900}, device_scale_factor=1.5, color_scheme="light"
        )
        pg = await ctx.new_page()
        await pg.goto("http://localhost:8801/setup")
        await pg.wait_for_timeout(1200)
        await pg.evaluate(
            "for (const i of document.querySelectorAll('input')) if ((i.value||'').includes('/api/auth/callback')) i.value='https://downtube.example.com/api/auth/callback'"
        )
        await pg.screenshot(path=f"{OUT}/setup-empty.png", full_page=True)
        inputs = pg.locator("input")
        n = await inputs.count()
        vals = [code, "123456789012-abcdefghijklmnop.apps.googleusercontent.com", "GOCSPX-demo-not-a-real-secret"]
        vi = 0
        for i in range(n):
            t = await inputs.nth(i).get_attribute("type")
            if vi < len(vals) and t in (None, "text", "password") and await inputs.nth(i).is_editable():
                await inputs.nth(i).fill(vals[vi])
                vi += 1
        em = pg.get_by_placeholder("name@gmail.com")
        if await em.count():
            await em.first.fill("you@example.com")
            await em.first.press("Enter")
        await pg.locator("h1").first.click()
        await pg.wait_for_timeout(500)
        await pg.screenshot(path=f"{OUT}/setup-filled.png", full_page=True)
        # login page
        await pg.goto("http://localhost:8802/login")
        await pg.wait_for_timeout(1000)
        await pg.screenshot(path=f"{OUT}/login.png")
        await ctx.close()
        for scheme in ("light", "dark"):
            ctx = await b.new_context(
                viewport={"width": 1440, "height": 900}, device_scale_factor=1.5, color_scheme=scheme
            )
            await ctx.add_cookies([{"name": "dt_session", "value": sess, "url": "http://localhost:8802"}])
            pg = await ctx.new_page()
            for route, name in [
                ("/", "dashboard"),
                ("/subscriptions", "subscriptions"),
                ("/likes", "likes"),
                ("/downloads", "downloads"),
                ("/library", "library"),
                ("/settings", "settings"),
            ]:
                await pg.goto("http://localhost:8802" + route)
                await pg.wait_for_timeout(1500)
                await pg.evaluate(MASK)
                await pg.screenshot(path=f"{OUT}/{name}-{scheme}.png", full_page=(name in ("settings",)))
            if scheme == "light":  # confirmation dialogs (0.3.0)
                for route, button, name in [
                    ("/library", 'button[aria-label^="Delete "]', "library-delete"),
                    ("/subscriptions", 'button[aria-label^="Unsubscribe from"]', "unsubscribe"),
                ]:
                    await pg.goto("http://localhost:8802" + route)
                    await pg.wait_for_timeout(1500)
                    await pg.locator(button).first.click()
                    await pg.wait_for_timeout(500)
                    await pg.evaluate(MASK)
                    await pg.screenshot(path=f"{OUT}/{name}.png")
            await ctx.close()
        # mobile
        ctx = await b.new_context(
            viewport={"width": 390, "height": 844}, device_scale_factor=2, color_scheme="dark", is_mobile=True
        )
        await ctx.add_cookies([{"name": "dt_session", "value": sess, "url": "http://localhost:8802"}])
        pg = await ctx.new_page()
        await pg.goto("http://localhost:8802/")
        await pg.wait_for_timeout(1500)
        await pg.screenshot(path=f"{OUT}/dashboard-mobile.png")
        await b.close()


asyncio.run(main())
