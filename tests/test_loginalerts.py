"""Login alerts: a message in the bell when an account is logged in from a device or network not used before."""
import loginalerts
import main
from conftest import PW, Client

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


def _login(name="Alice", ua=WINDOWS, ip=None):
    c = Client(main.app)
    headers = {"User-Agent": ua}
    if ip:
        headers["X-Forwarded-For"] = ip
    assert c.post("/api/login", json={"name": name, "password": PW}, headers=headers).status_code == 200
    return c


def _alerts(c):
    return [n for n in c.get("/api/notifications").json()["items"] if n["kind"] == "new_login"]


def _usual(c, **kw):
    """The usual way this user logs in (Chrome on Windows): remembered, and the message the test client's own first login left is cleared."""
    _login(**kw)
    for n in _alerts(c):
        c.delete(f"/api/notifications/{n['id']}")


def test_the_first_login_raises_nothing(alice):
    assert _alerts(alice) == []  # alice's fixture login is the first: nothing to compare with


def test_the_same_device_and_network_raise_nothing(alice):
    _usual(alice)
    _login()
    _login()
    assert _alerts(alice) == []


def test_a_new_device_is_reported(alice):
    _usual(alice)
    _login(ua=IPHONE)
    (n,) = _alerts(alice)
    assert n["title"] == "New login to your account" and n["nav"] == "#/account/devices" and n["unread"]
    assert n["body"] == "Safari on iPhone (testclient): a device you have not used before."


def test_a_new_network_is_reported(alice, monkeypatch):
    monkeypatch.setattr(main, "TRUST_PROXY", True)
    _usual(alice, ip="203.0.113.5")
    _login(ip="198.51.100.7")
    (n,) = _alerts(alice)
    assert n["body"] == "Chrome on Windows (198.51.100.7): a network you have not used before."


def test_a_new_device_in_a_new_network_is_one_message(alice, monkeypatch):
    monkeypatch.setattr(main, "TRUST_PROXY", True)
    _usual(alice, ip="203.0.113.5")
    _login(ua=IPHONE, ip="198.51.100.7")
    (n,) = _alerts(alice)
    assert n["body"] == "Safari on iPhone (198.51.100.7): a device and a network you have not used before."


def test_a_login_is_only_new_once(alice):
    _usual(alice)
    _login(ua=IPHONE)
    assert len(_alerts(alice)) == 1
    _login(ua=IPHONE)
    assert len(_alerts(alice)) == 1  # the second one from the phone is usual by now


def test_the_same_address_group_is_one_network(alice, monkeypatch):
    monkeypatch.setattr(main, "TRUST_PROXY", True)
    _usual(alice, ip="203.0.113.5")
    _login(ip="203.0.113.77")  # same /24: a provider that hands out another address
    _login(ip="2001:db8:1::5")
    _login(ip="2001:db8:1:ffff::9")  # same IPv6 /48
    assert [n["body"] for n in _alerts(alice)] == ["Chrome on Windows (2001:db8:1::5): a network you have not used before."]


def test_an_unknown_browser_alone_is_no_reason_to_warn(alice):
    _usual(alice)
    _login(ua="curl/8.0")  # nothing recognizable, the network is the usual one
    assert _alerts(alice) == []


def test_every_user_gets_only_their_own_warnings(alice, bob):
    _usual(alice)
    _usual(bob, name="Bob")
    _login("Bob", ua=IPHONE)
    assert _alerts(alice) == [] and len(_alerts(bob)) == 1


def test_turning_it_off_silences_the_warnings_but_keeps_the_history(alice):
    _usual(alice)
    assert alice.put("/api/admin/login-alerts", json={"enabled": False}).json()["enabled"] is False
    _login(ua=IPHONE)
    assert _alerts(alice) == []
    alice.put("/api/admin/login-alerts", json={"enabled": True})
    _login(ua=IPHONE)
    assert _alerts(alice) == []  # the phone was remembered while the warnings were off


def test_the_default_comes_from_the_environment(alice, monkeypatch):
    assert alice.get("/api/admin/login-alerts").json() == {"enabled": True, "default": True}
    monkeypatch.setattr(loginalerts, "DEFAULT_ON", False)
    assert alice.get("/api/admin/login-alerts").json()["enabled"] is False
    alice.put("/api/admin/login-alerts", json={"enabled": True})
    assert alice.get("/api/admin/login-alerts").json()["enabled"] is True  # the admin's choice wins


def test_the_text_is_translated(alice):
    _usual(alice)
    _login(ua=IPHONE)
    alice.cookies.set("nx_lang", "de")
    (n,) = _alerts(alice)
    assert n["title"] == "Neue Anmeldung bei deinem Konto" and "Safari auf iPhone" in n["body"] and "noch nicht benutzt" in n["body"]


def test_a_warning_can_be_dismissed(alice):
    _usual(alice)
    _login(ua=IPHONE)
    (n,) = _alerts(alice)
    assert alice.delete(f"/api/notifications/{n['id']}").status_code == 200
    assert _alerts(alice) == []


def test_only_the_last_logins_are_remembered(alice, monkeypatch):
    monkeypatch.setattr(main.db, "LOGIN_HISTORY_KEEP", 3)
    uid = main.db.find_user("Alice")["id"]
    for i in range(6):
        main.db.record_login(uid, f"10.0.{i}.1", f"10.0.{i}.0/24", "Chrome", "Windows")
    with main.db.db() as c:
        assert c.execute("SELECT COUNT(*) FROM login_history WHERE user_id = ?", (uid,)).fetchone()[0] == 3


def test_networks():
    assert loginalerts.network("203.0.113.77") == "203.0.113.0/24"
    assert loginalerts.network("2001:db8:1:ffff::9") == "2001:db8:1::/48"
    assert loginalerts.network("testclient") == "testclient"


def test_only_admins_set_it(alice, bob, anon):
    assert bob.get("/api/admin/login-alerts").status_code == 403
    assert bob.put("/api/admin/login-alerts", json={"enabled": False}).status_code == 403
    assert anon.get("/api/admin/login-alerts").status_code == 401
