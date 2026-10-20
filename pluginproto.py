"""Plugin protocol v1: constants and manifest validation (no dependency on the rest of the server).

Used by the core (plugins.py) and the checker (plugincheck.py); plugins may copy this file together with plugincheck.py
into their tests. See PLUGINS.md.
"""
import re

API_VERSION = 1  # version of the plugin protocol
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
FEATURES = {"search", "stream", "radio", "artist", "album", "suggest", "prefetch", "nowplaying"}  # optional: suggest (search suggestions while typing), prefetch (hint about upcoming tracks), nowplaying (current song of a live stream)
KINDS = {"songs", "artists", "albums"}
SETTING_TYPES = {"bool", "int", "choice", "text"}
ACTION_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
MAX_ACTIONS = 4
TEXT_MAX = 400  # longest allowed text input
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_]{1,23}$")
MAX_CATEGORIES = 4
LANGS = ("en", "de")  # languages a plugin may provide texts for (the first is the fallback)


def translate(msg: str, **values) -> str:
    """Hook for message translation. The server replaces it with its translator; standalone it just fills in the values."""
    return msg.format(**values) if values else msg


class PluginError(Exception):
    """Plugin unreachable, or its answer does not follow the protocol.

    `msg` is an English template with {placeholders}; `localized()` translates it into the language of the current request.
    """

    def __init__(self, msg: str, **values):
        super().__init__(msg.format(**values) if values else msg)
        self.msg, self.values = msg, values

    def localized(self) -> str:
        return translate(self.msg, **self.values)


def text(value, lang: str = "en") -> str:
    """A plugin text in the wanted language. Plugins give either a plain (English) string or {"en": ..., "de": ...}."""
    if isinstance(value, dict):
        return str(value.get(lang) or value.get("en") or next(iter(value.values()), ""))
    return str(value or "")


def _check_text(value, where: str, required: bool = True) -> None:
    ok = isinstance(value, str) and value.strip() or isinstance(value, dict) and value and all(
        isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in value.items()) and "en" in value
    if not ok and (required or value not in (None, "")):
        raise PluginError("Invalid manifest: {where} must be a text, or one text per language ('en' and optionally 'de').", where=where)


def validate_categories(raw, features) -> list[dict]:
    """The optional `categories` of a manifest: extra search tabs (e.g. live radio, podcasts) whose results are track-shaped."""
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_CATEGORIES:
        raise PluginError("Invalid manifest: 'categories' must be a list of at most {n} entries.", n=MAX_CATEGORIES)
    if "search" not in features:
        raise PluginError("Invalid manifest: categories need the 'search' feature.")
    out: list[dict] = []
    for c in raw:
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not CATEGORY_RE.match(c["id"]) or c["id"] in KINDS:
            raise PluginError("Invalid manifest: every category needs an 'id' (a-z, 0-9, underscore, 2-24 characters; not songs, artists or albums).")
        if any(o["id"] == c["id"] for o in out):
            raise PluginError("Invalid manifest: category '{id}' is defined twice.", id=c["id"])
        _check_text(c.get("label"), f"label of category '{c['id']}'")
        out.append({"id": c["id"], "label": c["label"]})
    return out


def require_identity(m: dict, where: str) -> None:
    """name, description and version are mandatory: the server shows all three in the admin area."""
    for key in ("name", "description", "version"):
        if not isinstance(m.get(key), str) or not m[key].strip():
            raise PluginError("{where}: '{key}' is required (a non-empty text).", where=where, key=key)


def validate_settings_schema(raw) -> list[dict]:
    """Checks the optional `settings` list of a manifest and returns it normalized.

    Every entry: key, type (bool | int | choice | text), label, default, optional help; int: min and max, optional unit;
    choice: choices [{value, label}]; text: optional max_length (default 200, at most 400), placeholder, required (used by actions) and
    secret (a password or token: the admin area never shows the saved value again, leaving the field empty keeps it);
    optional depends_on (key of a bool setting: shown only while that one is on).
    """
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > 30:
        raise PluginError("Invalid manifest: 'settings' must be a list of at most 30 entries.")
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not KEY_RE.match(item["key"]):
            raise PluginError("Invalid manifest: every setting needs a 'key' (a-z, 0-9, underscore).")
        key = item["key"]
        if any(o["key"] == key for o in out):
            raise PluginError("Invalid manifest: setting '{key}' is defined twice.", key=key)
        kind = item.get("type")
        if kind not in SETTING_TYPES:
            raise PluginError("Invalid manifest: setting '{key}' has an unknown type (allowed: {allowed}).", key=key, allowed=sorted(SETTING_TYPES))
        _check_text(item.get("label"), f"label of setting '{key}'")
        _check_text(item.get("help"), f"help of setting '{key}'", required=False)
        entry = {"key": key, "type": kind, "label": item["label"], "help": item.get("help") or ""}
        if kind == "int":
            lo, hi = item.get("min"), item.get("max")
            if not all(isinstance(x, int) and not isinstance(x, bool) for x in (lo, hi)) or lo > hi:
                raise PluginError("Invalid manifest: setting '{key}' needs integer 'min' and 'max'.", key=key)
            entry.update(min=lo, max=hi)
            if item.get("unit"):
                _check_text(item["unit"], f"unit of setting '{key}'")
                entry["unit"] = item["unit"]
        elif kind == "text":
            limit = item.get("max_length", 200)
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= TEXT_MAX:
                raise PluginError("Invalid manifest: setting '{key}' needs 'max_length' between 1 and {max}.", key=key, max=TEXT_MAX)
            entry["max_length"] = limit
            entry["required"] = item.get("required") is True
            entry["secret"] = item.get("secret") is True
            if item.get("placeholder"):
                _check_text(item["placeholder"], f"placeholder of setting '{key}'")
                entry["placeholder"] = item["placeholder"]
        elif kind == "choice":
            choices = item.get("choices")
            if not isinstance(choices, list) or not choices or not all(isinstance(c, dict) and isinstance(c.get("value"), str) and c["value"] for c in choices):
                raise PluginError("Invalid manifest: setting '{key}' needs 'choices' with a 'value' and a 'label' each.", key=key)
            for c in choices:
                _check_text(c.get("label"), f"choice label of setting '{key}'")
            entry["choices"] = [{"value": c["value"], "label": c["label"]} for c in choices]
        entry["default"] = _coerce(entry, item.get("default", "" if kind == "text" else None), strict=True)
        if item.get("depends_on"):
            dep = next((o for o in out if o["key"] == item["depends_on"]), None)
            if not dep or dep["type"] != "bool":
                raise PluginError("Invalid manifest: 'depends_on' of setting '{key}' must name an earlier bool setting.", key=key)
            entry["depends_on"] = dep["key"]
        out.append(entry)
    return out


def _coerce(entry: dict, value, strict: bool):
    """Value as the setting's type. strict: wrong input raises, otherwise the default is used."""
    ok, result = True, None
    if entry["type"] == "bool":
        ok, result = isinstance(value, bool), value
    elif entry["type"] == "text":
        ok, result = isinstance(value, str) and len(value) <= entry["max_length"], value
    elif entry["type"] == "int":
        ok = isinstance(value, int) and not isinstance(value, bool) and entry["min"] <= value <= entry["max"]
        result = value
    else:
        ok, result = value in [c["value"] for c in entry["choices"]], value
    if ok:
        return result
    if strict:
        raise PluginError("Invalid value for setting '{key}'.", key=entry["key"])
    return entry["default"]


def clean_values(schema: list[dict], values) -> dict:
    """All settings of the schema with valid values (unknown keys dropped, missing or invalid ones replaced by the default)."""
    values = values if isinstance(values, dict) else {}
    return {e["key"]: _coerce(e, values.get(e["key"]), strict=False) for e in schema}


def check_values(schema: list[dict], values) -> dict:
    """Like clean_values, but for input from the admin: an invalid value is an error (missing keys keep the default)."""
    if not isinstance(values, dict):
        raise PluginError("Invalid value for setting '{key}'.", key="")
    unknown = [k for k in values if k not in {e["key"] for e in schema}]
    if unknown:
        raise PluginError("Unknown setting '{key}'.", key=unknown[0])
    out = {e["key"]: _coerce(e, values[e["key"]], strict=True) if e["key"] in values else e["default"] for e in schema}
    for e in schema:
        if e["type"] == "text":
            out[e["key"]] = out[e["key"]].strip()
            if e["required"] and not out[e["key"]]:
                raise PluginError("The field '{key}' is required.", key=e["key"])
    return out


def validate_actions(raw, features) -> list[dict]:
    """The optional `actions` of a manifest: things the admin can run from the plugin's settings sheet (with a small form), e.g. "add a station".

    Every entry: id, label, optional help and fields (same entries as settings; the form is filled in by the admin, nothing is stored)."""
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ACTIONS:
        raise PluginError("Invalid manifest: 'actions' must be a list of at most {n} entries.", n=MAX_ACTIONS)
    out: list[dict] = []
    for a in raw:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not ACTION_RE.match(a["id"]):
            raise PluginError("Invalid manifest: every action needs an 'id' (a-z, 0-9, underscore, 2-32 characters).")
        if any(o["id"] == a["id"] for o in out):
            raise PluginError("Invalid manifest: action '{id}' is defined twice.", id=a["id"])
        _check_text(a.get("label"), f"label of action '{a['id']}'")
        _check_text(a.get("help"), f"help of action '{a['id']}'", required=False)
        out.append({"id": a["id"], "label": a["label"], "help": a.get("help") or "", "fields": validate_settings_schema(a.get("fields"))})
    return out


def validate_manifest(m: dict) -> dict:
    if not isinstance(m, dict) or not isinstance(m.get("id"), str) or not ID_RE.match(m["id"]):
        raise PluginError("Invalid manifest: 'id' is missing or invalid (a-z, 0-9, hyphen, 2-24 characters).")
    if m["id"] == "local":
        raise PluginError("The identifier 'local' is reserved for the own library.")
    if m.get("api_version") != API_VERSION:
        raise PluginError("The plugin speaks protocol version {got}, this server understands {want}.", got=m.get("api_version"), want=API_VERSION)
    features = m.get("features")
    if not isinstance(features, list) or not set(features) <= FEATURES or not features:
        raise PluginError("Invalid manifest: 'features' must be a non-empty list from {allowed}.", allowed=sorted(FEATURES))
    kinds = m.get("search_kinds", ["songs"])
    if not isinstance(kinds, list) or not set(kinds) <= KINDS:
        raise PluginError("Invalid manifest: 'search_kinds'.")
    require_identity(m, "Manifest")
    return {"id": m["id"], "name": m["name"].strip()[:60], "version": m["version"].strip()[:40],
            "description": m["description"].strip()[:300], "features": sorted(features), "search_kinds": sorted(kinds),
            "settings": validate_settings_schema(m.get("settings")), "categories": validate_categories(m.get("categories"), features),
            "actions": validate_actions(m.get("actions"), features)}
