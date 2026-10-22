"""The places a backup can go: folder, S3, WebDAV, FTP and SFTP (no real services: stand-ins that behave like them)."""
import hashlib
import socket
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import paramiko
import pytest

import backupdrivers as bd

NAME = "nxtsound-20261022-030000.db"
NAME2 = "nxtsound-20261023-030000.db"


@pytest.fixture()
def dbfile(tmp_path):
    p = tmp_path / "source.db"
    p.write_bytes(b"SQLite format 3\x00" + b"x" * 5000)
    return p


# ---------- Folder ----------

def test_a_folder_takes_lists_returns_and_deletes_backups(tmp_path, dbfile):
    d = bd.make("local", {"path": str(tmp_path / "bk")})
    assert "writable" in d.test()
    d.put(NAME, dbfile)
    (tmp_path / "bk" / "holiday.jpg").write_bytes(b"not ours")  # a shared folder: other files are left alone
    (listing,) = d.list()
    assert listing["name"] == NAME and listing["size"] == dbfile.stat().st_size and listing["modified"] > 0
    out = tmp_path / "back.db"
    d.get(NAME, out)
    assert out.read_bytes() == dbfile.read_bytes()
    d.delete(NAME)
    assert d.list() == [] and (tmp_path / "bk" / "holiday.jpg").exists()


def test_a_folder_must_be_absolute_and_writable(tmp_path):
    with pytest.raises(bd.BackupError, match="absolute"):
        bd.make("local", {"path": "relative/dir"}).test()
    with pytest.raises(bd.BackupError, match="absolute"):
        bd.make("local", {"path": ""}).test()
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(bd.BackupError, match="Cannot write"):
        bd.make("local", {"path": str(f / "sub")}).test()


def test_only_backup_names_can_be_fetched_or_deleted(tmp_path):
    d = bd.make("local", {"path": str(tmp_path)})
    for bad in ("../etc/passwd", "secret.txt", "nxtsound-1.db"):
        with pytest.raises(bd.BackupError):
            d.get(bad, tmp_path / "x")
        with pytest.raises(bd.BackupError):
            d.delete(bad)


# ---------- S3 ----------

def test_the_signing_key_matches_the_documented_example():
    """From Amazon's documentation: secret wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY, 20150830, us-east-1, iam."""
    key = bd.sigv4_key("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20150830", "us-east-1", "iam")
    assert key.hex() == "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9"


def test_the_signature_matches_the_official_test_suite():
    """"get-vanilla" of the Signature Version 4 test suite."""
    signed, sig = bd.sigv4_signature("GET", "/", "", {"host": "example.amazonaws.com", "x-amz-date": "20150830T123600Z"}, hashlib.sha256(b"").hexdigest(),
                                     "20150830T123600Z", "us-east-1", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "service")
    assert signed == "host;x-amz-date"
    assert sig == "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"


class FakeS3:
    """A bucket behind httpx.MockTransport: checks that every request is signed and stores objects in a dict."""

    def __init__(self, page=1000):
        self.objects: dict[str, bytes] = {}
        self.page = page
        self.seen = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDTEST/") and "/us-east-1/s3/aws4_request" in auth and "Signature=" in auth
        assert request.headers["x-amz-date"] and request.headers["host"] == "s3.example.test"
        path = request.url.path
        assert path.startswith("/bucket")
        self.seen.append((request.method, path))
        key = path[len("/bucket/"):] if path != "/bucket" else ""
        if request.method == "PUT":
            body = request.read()
            assert request.headers["x-amz-content-sha256"] == hashlib.sha256(body).hexdigest()
            self.objects[key] = body
            return httpx.Response(200)
        if request.method == "GET" and not key:
            prefix = request.url.params.get("prefix", "")
            token = request.url.params.get("continuation-token")
            names = sorted(k for k in self.objects if k.startswith(prefix))
            start = int(token) if token else 0
            chunk = names[start:start + self.page]
            more = start + self.page < len(names)
            items = "".join(f"<Contents><Key>{k}</Key><LastModified>2026-10-22T03:00:00.000Z</LastModified><Size>{len(self.objects[k])}</Size></Contents>" for k in chunk)
            nxt = f"<NextContinuationToken>{start + self.page}</NextContinuationToken>" if more else ""
            xml = f'<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>{"true" if more else "false"}</IsTruncated>{items}{nxt}</ListBucketResult>'
            return httpx.Response(200, text=xml)
        if request.method == "GET":
            return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404, text="<Error><Code>NoSuchKey</Code></Error>")
        if request.method == "DELETE":
            self.objects.pop(key, None)
            return httpx.Response(204)
        raise AssertionError(request.method)


S3CFG = {"endpoint": "https://s3.example.test", "region": "us-east-1", "bucket": "bucket", "prefix": "nxtsound", "access_key": "AKIDTEST", "secret_key": "secret"}


def test_s3_puts_lists_gets_and_deletes(dbfile, tmp_path):
    fake = FakeS3()
    d = bd.S3Driver(S3CFG, transport=httpx.MockTransport(fake))
    d.put(NAME, dbfile)
    assert list(fake.objects) == ["nxtsound/" + NAME]  # the prefix gets its slash
    fake.objects["nxtsound/other.txt"] = b"not ours"
    (listing,) = d.list()
    assert listing["name"] == NAME and listing["size"] == dbfile.stat().st_size and listing["modified"] > 1.7e9
    out = tmp_path / "back.db"
    d.get(NAME, out)
    assert out.read_bytes() == dbfile.read_bytes()
    d.delete(NAME)
    assert d.list() == []
    assert "0 backups" in d.test()


def test_s3_listing_follows_the_pages(dbfile):
    fake = FakeS3(page=2)
    d = bd.S3Driver(S3CFG, transport=httpx.MockTransport(fake))
    for n in range(5):
        fake.objects[f"nxtsound/nxtsound-2026102{n}-030000.db"] = b"x"
    assert len(d.list()) == 5


def test_s3_says_what_is_wrong(dbfile):
    denied = httpx.MockTransport(lambda r: httpx.Response(403, text="<Error><Code>SignatureDoesNotMatch</Code></Error>"))
    with pytest.raises(bd.BackupError, match="refused the keys.*SignatureDoesNotMatch"):
        bd.S3Driver(S3CFG, transport=denied).test()
    missing = httpx.MockTransport(lambda r: httpx.Response(404, text="<Error><Code>NoSuchBucket</Code></Error>"))
    with pytest.raises(bd.BackupError, match="bucket was not found"):
        bd.S3Driver(S3CFG, transport=missing).test()

    def down(request):
        raise httpx.ConnectError("no route")
    with pytest.raises(bd.BackupError, match="Cannot reach"):
        bd.S3Driver(S3CFG, transport=httpx.MockTransport(down)).test()
    with pytest.raises(bd.BackupError, match="needed"):
        bd.S3Driver({"endpoint": "https://x"}).test()


# ---------- WebDAV ----------

class FakeDav:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.folders = {"/remote.php/dav/files/robin"}
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        path = request.url.path
        self.calls.append((request.method, path))
        if request.method == "MKCOL":
            if path in self.folders:
                return httpx.Response(405)
            self.folders.add(path)
            return httpx.Response(201)
        if request.method == "PUT":
            self.files[path] = request.read()
            return httpx.Response(201)
        if request.method == "PROPFIND":
            base = path.rstrip("/")
            if base not in self.folders:
                return httpx.Response(404)
            rows = "".join(f'<d:response><d:href>{p}</d:href><d:propstat><d:prop><d:getcontentlength>{len(b)}</d:getcontentlength>'
                           f'<d:getlastmodified>Thu, 22 Oct 2026 03:00:00 GMT</d:getlastmodified></d:prop></d:propstat></d:response>' for p, b in self.files.items() if p.startswith(base + "/"))
            return httpx.Response(207, text=f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"><d:response><d:href>{base}/</d:href></d:response>{rows}</d:multistatus>')
        if request.method == "GET":
            return httpx.Response(200, content=self.files[path]) if path in self.files else httpx.Response(404)
        if request.method == "DELETE":
            self.files.pop(path, None)
            return httpx.Response(204)
        raise AssertionError(request.method)


DAV = {"url": "https://cloud.example.test", "user": "robin", "password": "app-password", "folder": "/Backups/nxtsound"}


def test_webdav_builds_the_nextcloud_address_and_creates_the_folders(dbfile, tmp_path):
    fake = FakeDav()
    d = bd.WebDavDriver(DAV, transport=httpx.MockTransport(fake))
    assert d.base == "https://cloud.example.test/remote.php/dav/files/robin"  # a plain address of a Nextcloud or ownCloud
    assert d.list() == []  # nothing there yet is not an error
    d.put(NAME, dbfile)
    assert "/remote.php/dav/files/robin/Backups/nxtsound" in fake.folders
    fake.files["/remote.php/dav/files/robin/Backups/nxtsound/holiday.jpg"] = b"x"
    (listing,) = d.list()
    assert listing["name"] == NAME and listing["size"] == dbfile.stat().st_size and listing["modified"] > 1.7e9
    out = tmp_path / "back.db"
    d.get(NAME, out)
    assert out.read_bytes() == dbfile.read_bytes()
    d.delete(NAME)
    assert d.list() == []
    d.put(NAME, dbfile)  # a second upload finds the folders (405) and goes on
    assert "1 backups" in d.test()


def test_a_full_webdav_address_is_kept():
    d = bd.WebDavDriver({**DAV, "url": "https://dav.example.test/remote.php/webdav"})
    assert d.base == "https://dav.example.test/remote.php/webdav"
    assert bd.WebDavDriver({**DAV, "url": "cloud.example.test"}).base.startswith("https://cloud.example.test/remote.php/dav/files/")


def test_webdav_says_what_is_wrong():
    with pytest.raises(bd.BackupError, match="refused the login"):
        bd.WebDavDriver(DAV, transport=httpx.MockTransport(lambda r: httpx.Response(401))).test()
    with pytest.raises(bd.BackupError, match="error: 500"):
        bd.WebDavDriver(DAV, transport=httpx.MockTransport(lambda r: httpx.Response(500))).test()
    with pytest.raises(bd.BackupError, match="needed"):
        bd.WebDavDriver({"url": "https://x"}).test()


# ---------- FTP (a real server in a thread) ----------

@pytest.fixture()
def ftp_server(tmp_path):
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import ThreadedFTPServer
    root = tmp_path / "ftproot"
    root.mkdir()
    auth = DummyAuthorizer()
    auth.add_user("robin", "pw", str(root), perm="elradfmwMT")
    handler = type("H", (FTPHandler,), {"authorizer": auth, "passive_ports": range(60100, 60200)})
    server = ThreadedFTPServer(("127.0.0.1", 0), handler)
    stop = threading.Event()

    def run():
        while not stop.is_set():
            server.serve_forever(timeout=0.1, blocking=False)  # one round at a time, so that it can be stopped
        server.close_all()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    yield server.address[1], root
    stop.set()
    t.join(3)


def test_ftp_puts_lists_gets_and_deletes(ftp_server, dbfile, tmp_path):
    port, root = ftp_server
    d = bd.make("ftp", {"host": "127.0.0.1", "port": port, "user": "robin", "password": "pw", "tls": "none", "folder": "/backups/nxtsound"})
    assert "0 backups" in d.test()
    d.put(NAME, dbfile)
    assert (root / "backups" / "nxtsound" / NAME).read_bytes() == dbfile.read_bytes()
    (root / "backups" / "nxtsound" / "holiday.jpg").write_bytes(b"x")
    (listing,) = d.list()
    assert listing["name"] == NAME and listing["size"] == dbfile.stat().st_size and listing["modified"] > 1.7e9
    out = tmp_path / "back.db"
    d.get(NAME, out)
    assert out.read_bytes() == dbfile.read_bytes()
    d.delete(NAME)
    assert d.list() == []


def test_ftp_says_what_is_wrong(ftp_server):
    port, _ = ftp_server
    with pytest.raises(bd.BackupError, match="refused the login"):
        bd.make("ftp", {"host": "127.0.0.1", "port": port, "user": "robin", "password": "wrong", "tls": "none"}).test()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    closed = s.getsockname()[1]
    s.close()
    with pytest.raises(bd.BackupError, match="Cannot reach"):
        bd.make("ftp", {"host": "127.0.0.1", "port": closed, "user": "a", "password": "b", "tls": "none"}).test()
    with pytest.raises(bd.BackupError, match="needed"):
        bd.make("ftp", {"host": ""}).test()


# ---------- SFTP (paramiko replaced by a stand-in backed by a folder) ----------

class FakeKey:
    def __init__(self, data=b"server-key"):
        self.data = data

    def asbytes(self):
        return self.data


class FakeAttr:
    def __init__(self, p: Path):
        self.filename, self.st_size, self.st_mtime = p.name, p.stat().st_size, p.stat().st_mtime


def install_fake_sftp(monkeypatch, root: Path, key=FakeKey(), accept="pw"):
    class Transport:
        closed = 0

        def __init__(self, sock):
            pass

        def connect(self, username=None, password=None, pkey=None):
            if password != accept and pkey is None:
                raise paramiko.AuthenticationException("no")

        def get_remote_server_key(self):
            return key

        def close(self):
            Transport.closed += 1

    class SFTP:
        def __init__(self):
            self.cwd = root

        def chdir(self, p):
            q = self.cwd / p if not p.startswith("/") else root
            if not q.is_dir():
                raise IOError("no such folder")
            self.cwd = q

        def mkdir(self, p):
            (self.cwd / p).mkdir()

        def put(self, src, name):
            (self.cwd / name).write_bytes(Path(src).read_bytes())

        def get(self, name, dest):
            Path(dest).write_bytes((self.cwd / name).read_bytes())

        def remove(self, name):
            if not (self.cwd / name).exists():
                raise IOError("missing")
            (self.cwd / name).unlink()

        def rename(self, a, b):
            (self.cwd / a).rename(self.cwd / b)

        def listdir_attr(self, p="."):
            return [FakeAttr(f) for f in self.cwd.iterdir() if f.is_file()]

    monkeypatch.setattr(paramiko, "Transport", Transport)
    monkeypatch.setattr(paramiko.SFTPClient, "from_transport", staticmethod(lambda t: SFTP()))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: object())
    return Transport


SFTP = {"host": "nas.example.test", "port": 22, "user": "robin", "password": "pw", "folder": "backups/nxtsound"}


def test_sftp_puts_lists_gets_and_deletes(monkeypatch, tmp_path, dbfile):
    root = tmp_path / "sftproot"
    root.mkdir()
    transport = install_fake_sftp(monkeypatch, root)
    learned = []
    d = bd.make("sftp", dict(SFTP), learn=learned.append)
    d.put(NAME, dbfile)
    assert (root / "backups" / "nxtsound" / NAME).read_bytes() == dbfile.read_bytes()
    assert not list((root / "backups" / "nxtsound").glob("*.part"))
    (root / "backups" / "nxtsound" / "other.txt").write_bytes(b"x")
    (listing,) = d.list()
    assert listing["name"] == NAME and listing["size"] == dbfile.stat().st_size
    out = tmp_path / "back.db"
    d.get(NAME, out)
    assert out.read_bytes() == dbfile.read_bytes()
    d.put(NAME, dbfile)  # replaces the old file of the same name
    d.delete(NAME)
    assert d.list() == []
    assert transport.closed >= 5 and learned and learned[0].startswith("SHA256:")  # every connection is closed, the key was learned


def test_sftp_pins_the_server_key(monkeypatch, tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    install_fake_sftp(monkeypatch, root)
    learned = []
    bd.make("sftp", dict(SFTP), learn=learned.append).test()
    pinned = learned[0]
    bd.make("sftp", {**SFTP, "host_key": pinned}).test()  # the same key: fine
    with pytest.raises(bd.BackupError, match="key has changed"):
        bd.make("sftp", {**SFTP, "host_key": "SHA256:" + "A" * 43}).test()
    install_fake_sftp(monkeypatch, root, key=FakeKey(b"another server"))
    with pytest.raises(bd.BackupError, match="key has changed"):
        bd.make("sftp", {**SFTP, "host_key": pinned}).test()  # the server's key is not the one we know


def test_sftp_says_what_is_wrong(monkeypatch, tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    install_fake_sftp(monkeypatch, root)
    with pytest.raises(bd.BackupError, match="refused the login"):
        bd.make("sftp", {**SFTP, "password": "nope"}).test()
    with pytest.raises(bd.BackupError, match="needed"):
        bd.make("sftp", {"host": "x", "user": "y"}).test()
    with pytest.raises(bd.BackupError, match="cannot be read"):
        bd.make("sftp", {**SFTP, "password": "", "private_key": "not a key"}).test()


def test_the_types_describe_their_fields():
    t = bd.types()
    assert set(t) == {"local", "s3", "webdav", "ftp", "sftp"}
    assert bd.secret_keys("s3") == {"secret_key"} and bd.secret_keys("sftp") == {"password", "private_key", "key_passphrase"}
    assert bd.secret_keys("local") == set() and bd.secret_keys("webdav") == {"password"}
    assert all(f["key"] and f["label"] and f["type"] in ("text", "password", "int", "choice", "textarea") for kind in t.values() for f in kind["fields"])
