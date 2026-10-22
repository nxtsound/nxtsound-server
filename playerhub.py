"""Remote control between the players of one account. A player that has something loaded (a browser tab, an app) keeps an event stream open to the server
and publishes what it plays; another device of the same account lists those players and sends them commands (play, pause, next, ...), which arrive
through the stream. Players that are idle do not hold a connection: a controller only asks for the list while its devices sheet is open.
Everything is in memory and belongs to one server process: after a restart the players connect again by themselves."""
import asyncio
import time

MAX_PLAYERS = 20       # connected players per account
QUEUE_SIZE = 50        # messages waiting for one player; a player that cannot keep up is dropped
STATE_TTL = 60         # seconds without any news after which a player no longer counts as playing

COMMANDS = {"play", "pause", "toggle", "next", "prev", "seek", "volume"}


class Player:
    __slots__ = ("session", "name", "browser", "system", "state", "state_at", "queue")

    def __init__(self, session: int, name: str, browser: str, system: str):
        self.session, self.name, self.browser, self.system = session, name, browser, system
        self.state: dict | None = None
        self.state_at = 0.0
        self.queue: asyncio.Queue = asyncio.Queue(QUEUE_SIZE)


_hub: dict[int, dict[str, Player]] = {}  # user ID -> {player ID: player}


def connect(user_id: int, player_id: str, session: int, name: str, browser: str, system: str) -> Player | None:
    """Registers a connected player (replacing an older connection with the same ID). None if the account has too many."""
    mine = _hub.setdefault(user_id, {})
    if player_id not in mine and len(mine) >= MAX_PLAYERS:
        return None
    old = mine.get(player_id)
    if old:
        _put(old, None)  # the older stream ends
    p = Player(session, name, browser, system)
    mine[player_id] = p
    return p


def disconnect(user_id: int, player_id: str, player: Player) -> None:
    mine = _hub.get(user_id, {})
    if mine.get(player_id) is player:  # not an older stream that was replaced
        del mine[player_id]
    if not mine:
        _hub.pop(user_id, None)


def _put(p: Player, msg) -> bool:
    try:
        p.queue.put_nowait(msg)
        return True
    except asyncio.QueueFull:
        return False


def set_state(user_id: int, player_id: str, state: dict) -> bool:
    """What a player plays right now. False if it is not connected."""
    p = _hub.get(user_id, {}).get(player_id)
    if not p:
        return False
    p.state, p.state_at = state, time.time()
    return True


def devices(user_id: int, asker: str = "") -> list[dict]:
    """The other players of the account that have a song loaded, the ones that play first."""
    now, out = time.time(), []
    for pid, p in _hub.get(user_id, {}).items():
        if pid == asker or not p.state or not p.state.get("track"):
            continue
        age = now - p.state_at
        out.append({"player": pid, "name": p.name, "browser": p.browser, "system": p.system, **p.state,
                    "age": round(age, 1), "playing": bool(p.state.get("playing")) and age < STATE_TTL})
    return sorted(out, key=lambda d: (not d["playing"], d["age"]))


def send(user_id: int, target: str, command: dict) -> str:
    """Sends a command to a player of the account. "ok", "missing" (not connected) or "busy" (it does not keep up)."""
    p = _hub.get(user_id, {}).get(target)
    if not p:
        return "missing"
    return "ok" if _put(p, {"event": "command", "data": command}) else "busy"


def session_of(user_id: int, player_id: str) -> int | None:
    p = _hub.get(user_id, {}).get(player_id)
    return p.session if p else None


def reset() -> None:
    _hub.clear()
