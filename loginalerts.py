"""Warns a user when their account is logged in from a device or network they have not used before. The warning is a message in their bell; the
admin can turn it off. There is no look-up of where an address is on the map (that would mean asking a third party): a "network" is the address group
(IPv4 /24, IPv6 /48), a "device" is browser and system. Every login is remembered, the very first one of a user raises nothing."""
import ipaddress
import os

import db
import devices

SETTING = "login_alerts"
DEFAULT_ON = os.getenv("LOGIN_ALERTS", "1") != "0"


def enabled() -> bool:
    """The admin's choice, else the environment's."""
    v = db.get_setting(SETTING, "")
    return DEFAULT_ON if v == "" else v == "1"


def set_enabled(on: bool) -> None:
    db.set_setting(SETTING, "1" if on else "0")


def network(ip: str) -> str:
    """The address group an IP belongs to; anything that is no IP address stands for itself."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    return str(ipaddress.ip_network(f"{ip}/{24 if a.version == 4 else 48}", strict=False))


def on_login(user_id: int, ip: str, user_agent: str) -> dict:
    """Called for every login: remembers it and, if it is new for the user, leaves a message in their bell."""
    browser, system = devices.describe(user_agent)
    res = db.record_login(user_id, ip, network(ip), browser, system)
    if not res["first"] and (res["new_device"] or res["new_network"]) and enabled():
        db.notify(user_id, f"login:{res['id']}", "new_login", {"browser": browser, "system": system, "ip": ip, "new_device": res["new_device"], "new_network": res["new_network"]})
    return res
