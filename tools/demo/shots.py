import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

"""Takes the screenshots (desktop and phone) from a running demo server. Usage: shots.py <output folder> home,player,search,...  (see README.md)"""
import os
BASE = os.environ.get("DEMO_URL", "http://localhost:8030"); OUT = Path(sys.argv[1]); OUT.mkdir(exist_ok=True)
which = sys.argv[2].split(",")
MODES = {"desktop": dict(viewport={"width": 1440, "height": 900}, device_scale_factor=1),
         "mobile": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)}

# The demo password is random and written by seed.py (DEMO_PASSWORD overrides it)
PASSWORD = os.environ.get("DEMO_PASSWORD") or (Path(os.environ.get("DEMO_DIR", str(Path(__file__).resolve().parent / "work"))) / "demo-password.txt").read_text().strip()

# The admin pages print this machine's folders (plugins folder, repository of a locally installed plugin). Show the usual Docker paths instead.
PLUGINS_DIR = os.environ.get("DEMO_DIR", str(Path(__file__).resolve().parent / "work")) + "/plugins"
LOCAL_REPOS = os.environ.get("DEMO_LOCAL_REPOS", "")  # e.g. /path/to/checkouts/  ->  https://github.com/nxtsound/
CLEAN = """([plugins, repos]) => {
  const swap = (t) => { t = t.split(plugins).join('/plugins'); return repos ? t.split(repos).join('https://github.com/nxtsound/') : t; };
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) { const v = swap(n.nodeValue); if (v !== n.nodeValue) n.nodeValue = v; }
}"""

def settle(page, ms=1500):
    page.wait_for_load_state("networkidle"); page.wait_for_timeout(ms)

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
    for mode, opts in MODES.items():
        ctx = browser.new_context(locale="en-US", color_scheme="dark", **opts)
        ctx.request.post(BASE + "/api/login", data={"name": "demo", "password": PASSWORD})
        page = ctx.new_page()
        def shot(name): page.screenshot(path=str(OUT / (name + ("" if mode == "desktop" else "-mobile") + ".png"))); print(mode, name)
        if "home" in which:
            page.goto(BASE + "/#/home"); settle(page, 2500); shot("home")
        if "player" in which:
            page.goto(BASE + "/#/home"); settle(page)
            page.locator(".qtile, .card[data-l]").first.click(); page.wait_for_timeout(1200)
            page.evaluate("document.getElementById('audio').currentTime = 78"); page.wait_for_timeout(600)
            page.locator("#mOpen").click(); page.wait_for_timeout(1500); shot("player"); page.locator("#fClose").click(); page.wait_for_timeout(500)
        if "search" in which:
            page.goto(BASE + "/#/home"); settle(page, 800)
            page.locator("#searchOpen").click(); page.wait_for_timeout(500)
            page.keyboard.type("neon", delay=60); page.keyboard.press("Enter"); settle(page, 2500); shot("search")
        if "playlists" in which:
            page.goto(BASE + "/#/playlist/1"); settle(page, 2000); shot("playlists")
        if "recap" in which:
            page.goto(BASE + "/#/account/recap"); settle(page, 2000); shot("recap")
        if "community" in which:
            page.goto(BASE + "/#/community"); settle(page, 1500); shot("community")
        if "admin-plugins" in which:
            page.goto(BASE + "/#/admin/plugins"); settle(page, 2000)
            page.evaluate("document.querySelector('details.plg[data-id=onlineradio]').open = true"); page.wait_for_timeout(600); page.evaluate(CLEAN, [PLUGINS_DIR, LOCAL_REPOS]); shot("admin-plugins")
        if "plugin-settings" in which:
            for pid, name in (("onlineradio", "radio-settings"), ("nextcloud", "nextcloud-settings")):
                page.goto(BASE + "/#/admin/plugins"); settle(page, 1500)
                page.evaluate(f"document.querySelector('details.plg[data-id={pid}]').open = true"); page.wait_for_timeout(400)
                page.locator(f"[data-act=pluginSettings][data-id={pid}]").click(); page.wait_for_timeout(1800)
                if pid == "nextcloud":
                    ins = page.locator("#sheet input:not([type=checkbox])")
                    ins.nth(0).fill("https://cloud.example.com"); ins.nth(1).fill("alice"); ins.nth(2).fill("abcde-fghij-klmno-pqrst-uvwxy")
                    page.wait_for_timeout(300)
                page.evaluate(CLEAN, [PLUGINS_DIR, LOCAL_REPOS]); shot(name); page.keyboard.press('Escape'); page.wait_for_timeout(400)
        if "radio-search" in which:
            page.goto(BASE + "/#/home"); settle(page, 800)
            page.locator("#searchOpen").click(); page.wait_for_timeout(400)
            page.keyboard.type("jazz", delay=60); page.keyboard.press("Enter"); settle(page, 4000)
            page.get_by_text("Live radio").first.click(); settle(page, 3500); shot("radio-search")
        ctx.close()
    browser.close()
