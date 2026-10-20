"""Collects the translatable source strings (msgids) from the Python and JavaScript sources."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_FILES = ["logconf.py", "main.py", "db.py", "library.py", "enrich.py", "autolists.py", "plugins.py", "pluginhost.py", "pluginproto.py", "i18n.py"]
JS_FILES = ["static/app.js", "static/tutorial.js"]
HTML_FILES = ["static/index.html"]

# Strings that are translated through a variable and therefore cannot be found by reading the code
DYNAMIC = {
    "Unknown artist",  # library.UNKNOWN_ARTIST
    "Band", "Solo artist", "Orchestra", "Choir", "Character", "Artist",  # enrich._TYPES and fallback
    "Server", "Access log (HTTP requests)", "Library scan", "Information from the internet", "Plugin management", "Plugin output",
    "Other libraries",  # logconf.CATEGORIES labels
}


def _const(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def python_keys() -> set[str]:
    keys: set[str] = set()
    for name in PY_FILES:
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            fname = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            args = node.args
            picks: list = []
            if fname in ("tr", "translate", "PluginError") and args:
                picks = [args[0]]
            elif fname == "trn" and len(args) >= 3:
                picks = [args[1], args[2]]
            elif fname == "_set" and len(args) >= 3:
                picks = [args[2]]
            for a in picks:
                s = _const(a)
                if s:
                    keys.add(s)
    return keys


_JS_TR = re.compile(r"""\btr\(\s*(?P<q>['"`])(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)""", re.S)
_JS_TRN = re.compile(r"""\btrn\(\s*[^,]+?,\s*(?P<q1>['"`])(?P<one>(?:\\.|(?!(?P=q1)).)*)(?P=q1)\s*,\s*(?P<q2>['"`])(?P<many>(?:\\.|(?!(?P=q2)).)*)(?P=q2)""", re.S)
_ATTR = re.compile(r"""data-i18n(?:-ph|-aria|-title)?="([^"]+)\"""")


def _unescape(s: str) -> str:
    return s.replace("\\'", "'").replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


def js_keys() -> set[str]:
    keys: set[str] = set()
    for name in JS_FILES:
        text = (ROOT / name).read_text(encoding="utf-8")
        keys |= {_unescape(m.group("s")) for m in _JS_TR.finditer(text)}
        for m in _JS_TRN.finditer(text):
            keys |= {_unescape(m.group("one")), _unescape(m.group("many"))}
    for name in HTML_FILES:
        keys |= set(_ATTR.findall((ROOT / name).read_text(encoding="utf-8")))
    return keys


def all_keys() -> set[str]:
    return python_keys() | js_keys() | DYNAMIC
