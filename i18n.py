"""Translations for user-visible server messages.

Source strings in the code are English. `static/i18n/<lang>.json` maps an English string to its translation; the web UI loads
the same file, so server messages and UI texts share one catalog. Missing entries fall back to the English text.

The language of a request is resolved in `SecurityMiddleware` (cookie `nx_lang`, then the `Accept-Language` header, then
`DEFAULT_LANG`) and kept in a context variable, so `tr()` can be called anywhere while a request is handled.
"""
import contextvars
import json
import os
import re
from pathlib import Path

LANGS = ("en", "de")
DEFAULT_LANG = os.getenv("DEFAULT_LANG", "en").lower()
if DEFAULT_LANG not in LANGS:
    DEFAULT_LANG = "en"
LANG_COOKIE = "nx_lang"

_current: contextvars.ContextVar[str] = contextvars.ContextVar("lang", default=DEFAULT_LANG)
_catalogs: dict[str, dict[str, str]] = {}


def _load(lang: str) -> dict[str, str]:
    if lang not in _catalogs:
        try:
            _catalogs[lang] = json.loads((Path(__file__).parent / "static" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _catalogs[lang] = {}
    return _catalogs[lang]


def set_lang(lang: str) -> None:
    _current.set(lang if lang in LANGS else DEFAULT_LANG)


def current() -> str:
    return _current.get()


def tr(msg: str, **values) -> str:
    """Translate `msg` into the current request language; `{name}` placeholders are filled from `values`."""
    out = _load(_current.get()).get(msg, msg) if _current.get() != "en" else msg
    return out.format(**values) if values else out


def parse_accept_language(header: str) -> str:
    """First supported language of an Accept-Language header ('de-CH,de;q=0.9,en;q=0.8' -> 'de'), else ''."""
    ranked: list[tuple[float, str]] = []
    for part in (header or "").split(","):
        m = re.match(r"^\s*([A-Za-z]{2,3})(?:-[A-Za-z0-9]+)*\s*(?:;\s*q=([0-9.]+))?\s*$", part)
        if m:
            try:
                ranked.append((float(m.group(2) or 1), m.group(1).lower()))
            except ValueError:
                continue
    for _q, code in sorted(ranked, key=lambda x: -x[0]):
        if code in LANGS:
            return code
    return ""


def resolve(cookie: str, accept_language: str) -> str:
    """Request language: explicit cookie, else browser preference, else the server default."""
    if cookie in LANGS:
        return cookie
    return parse_accept_language(accept_language) or DEFAULT_LANG


def trn(n: int, one: str, many: str) -> str:
    """Translate with a singular/plural choice; `{n}` is filled in."""
    return tr(one if n == 1 else many, n=n)
