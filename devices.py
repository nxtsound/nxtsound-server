"""Browser and operating system from a User-Agent string, only what is needed to tell one's own devices apart."""
import re

_OS = (
    (re.compile(r"iPhone"), "iPhone"),
    (re.compile(r"iPad"), "iPad"),
    (re.compile(r"Android"), "Android"),
    (re.compile(r"Windows"), "Windows"),
    (re.compile(r"Macintosh|Mac OS X"), "Mac"),
    (re.compile(r"CrOS"), "Chromebook"),
    (re.compile(r"Linux|X11"), "Linux"),
)
_BROWSERS = (  # order matters: Edge and Opera also say Chrome, Chrome also says Safari
    (re.compile(r"Edg(?:e|A|iOS)?/"), "Edge"),
    (re.compile(r"OPR/|Opera"), "Opera"),
    (re.compile(r"Firefox/|FxiOS/"), "Firefox"),
    (re.compile(r"Chrome/|CriOS/"), "Chrome"),
    (re.compile(r"Safari/"), "Safari"),
)
_APP = re.compile(r"^(NxtSound[\w-]*)/")


def describe(user_agent: str) -> tuple[str, str]:
    """(browser or app, operating system) of a User-Agent, each "" if not recognizable: ("Safari", "iPhone"), ("Chrome", "Windows").
    The caller joins them in the reader's language ("Safari on iPhone")."""
    ua = (user_agent or "").strip()
    if not ua:
        return "", ""
    os_name = next((name for rx, name in _OS if rx.search(ua)), "")
    app = _APP.match(ua)
    if app:
        return app.group(1), os_name
    return next((name for rx, name in _BROWSERS if rx.search(ua)), ""), os_name


def short_agent(user_agent: str) -> str:
    """What is stored: the User-Agent without control characters, limited in length."""
    return "".join(ch for ch in (user_agent or "") if ch.isprintable())[:300]
