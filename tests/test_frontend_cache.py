"""The page is always revalidated; its scripts carry a hash and may be kept (the app then shows its page without the server)."""
import re

import main


def test_page_points_at_hashed_assets_and_is_never_cached(anon):
    r = anon.get("/")
    assert r.headers["cache-control"] == "no-cache"
    assert f'<script src="app.js?v={main.ASSET_VERSION}"></script>' in r.text and f'href="style.css?v={main.ASSET_VERSION}"' in r.text
    assert re.search(r'href="icon\.svg"', r.text)  # other files stay as they are


def test_hashed_assets_are_kept_forever_and_plain_ones_revalidated(anon):
    assert anon.get(f"/app.js?v={main.ASSET_VERSION}").headers["cache-control"] == "public, max-age=31536000, immutable"
    assert anon.get(f"/i18n/de.json?v={main.ASSET_VERSION}").headers["cache-control"].startswith("public")
    assert anon.get("/app.js").headers["cache-control"] == "no-cache"
    assert anon.get("/app.js?v=old").headers["cache-control"] == "no-cache"  # an outdated hash is not trusted
    assert anon.get("/nothing.js?v=" + main.ASSET_VERSION).status_code == 404


def test_version_changes_with_content(tmp_path):
    (tmp_path / "a.js").write_text("1")
    first = main._asset_version(tmp_path)
    (tmp_path / "a.js").write_text("2")
    assert main._asset_version(tmp_path) != first
