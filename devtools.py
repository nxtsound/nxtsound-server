"""Developer tools: extra information about why the server did what it did (scores of a radio, and so on).

Off unless the environment variable DEVTOOLS=1 is set, and then only for admins. The check lives here and is made on the server for
every request that can hand out such information; the page hiding a panel is not a protection.
"""
import os

ENABLED = os.getenv("DEVTOOLS", "0") == "1"


def active(user: dict | None) -> bool:
    """May this user get developer information?"""
    return ENABLED and bool(user and user.get("is_admin"))
