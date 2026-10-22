"""Where backups can go: a folder (which also covers NFS and any mounted drive), S3 (AWS, MinIO, Backblaze, Wasabi ...), WebDAV (Nextcloud, ownCloud ...), FTP/FTPS and SFTP.

Every driver offers the same few things: test(), put(name, path), list(), get(name, dest) and delete(name). They only ever touch files that are named like our
backups (BACKUP_NAME), so a shared folder is safe. The settings of each type are described in TYPES (the admin page draws its form from that)."""
import base64
import datetime
import ftplib
import hashlib
import hmac
import io
import posixpath
import re
import socket
import ssl
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from i18n import tr

BACKUP_NAME = re.compile(r"^nxtsound-\d{8}-\d{6}\.db(\.enc)?$")
TIMEOUT = 60


class BackupError(Exception):
    """A problem the admin can act on; the text is ready to be shown."""


def _fields_local():
    return [{"key": "path", "label": tr("Folder"), "type": "text", "required": True, "placeholder": "/backups",
             "help": tr("A folder as the server sees it. In Docker, mount a host folder or an NFS share into the container and enter its path here.")}]


def _fields_s3():
    return [
        {"key": "endpoint", "label": tr("Endpoint"), "type": "text", "default": "https://s3.amazonaws.com", "required": True,
         "help": tr("The address of the service, for example https://s3.eu-central-1.amazonaws.com or your MinIO server.")},
        {"key": "region", "label": tr("Region"), "type": "text", "default": "us-east-1", "required": True},
        {"key": "bucket", "label": tr("Bucket"), "type": "text", "required": True},
        {"key": "prefix", "label": tr("Folder in the bucket"), "type": "text", "placeholder": "nxtsound/"},
        {"key": "access_key", "label": tr("Access key"), "type": "text", "required": True},
        {"key": "secret_key", "label": tr("Secret key"), "type": "password", "required": True},
    ]


def _fields_webdav():
    return [
        {"key": "url", "label": tr("Address"), "type": "text", "required": True, "placeholder": "https://cloud.example.com",
         "help": tr("The address of your Nextcloud or ownCloud (https://cloud.example.com), or a full WebDAV address.")},
        {"key": "user", "label": tr("User name"), "type": "text", "required": True},
        {"key": "password", "label": tr("App password"), "type": "password", "required": True,
         "help": tr("Create an app password in the cloud's security settings; do not use your main password.")},
        {"key": "folder", "label": tr("Folder"), "type": "text", "placeholder": "/Backups/nxtsound"},
    ]


def _fields_ftp():
    return [
        {"key": "host", "label": tr("Server"), "type": "text", "required": True},
        {"key": "port", "label": tr("Port"), "type": "int", "default": 21, "min": 1, "max": 65535},
        {"key": "tls", "label": tr("Encryption"), "type": "choice", "default": "explicit",
         "choices": [{"value": "explicit", "label": tr("FTPS (explicit TLS, recommended)")}, {"value": "explicit_any", "label": tr("FTPS, accept any certificate (self-signed)")},
                     {"value": "none", "label": tr("None (plain FTP)")}]},
        {"key": "user", "label": tr("User name"), "type": "text", "required": True},
        {"key": "password", "label": tr("Password"), "type": "password", "required": True},
        {"key": "folder", "label": tr("Folder"), "type": "text", "placeholder": "/backups"},
    ]


def _fields_sftp():
    return [
        {"key": "host", "label": tr("Server"), "type": "text", "required": True},
        {"key": "port", "label": tr("Port"), "type": "int", "default": 22, "min": 1, "max": 65535},
        {"key": "user", "label": tr("User name"), "type": "text", "required": True},
        {"key": "password", "label": tr("Password"), "type": "password",
         "help": tr("Either a password or a private key.")},
        {"key": "private_key", "label": tr("Private key (PEM)"), "type": "textarea", "secret": True},
        {"key": "key_passphrase", "label": tr("Passphrase of the key"), "type": "password"},
        {"key": "folder", "label": tr("Folder"), "type": "text", "placeholder": "backups"},
        {"key": "host_key", "label": tr("Server fingerprint"), "type": "text",
         "help": tr("SHA256 fingerprint of the server's key. Left empty it is remembered at the first connection and checked from then on.")},
    ]


def types() -> dict:
    """type -> {label, fields}; in the language of the request."""
    return {
        "local": {"label": tr("Folder on the server (also NFS)")+" (tested)", "fields": _fields_local()},
        "s3": {"label": tr("S3 (Amazon, MinIO, Backblaze ...)")+" (tested)", "fields": _fields_s3()},
        "webdav": {"label": tr("Nextcloud / ownCloud / WebDAV")+" (untested)", "fields": _fields_webdav()},
        "ftp": {"label": tr("FTP / FTPS")+" (untested)", "fields": _fields_ftp()},
        "sftp": {"label": tr("SFTP (SSH)")+" (untested)", "fields": _fields_sftp()},
    }


def secret_keys(kind: str) -> set[str]:
    return {f["key"] for f in types()[kind]["fields"] if f["type"] == "password" or f.get("secret")}


def _entry(name: str, size: int, modified: float) -> dict:
    return {"name": name, "size": int(size), "modified": float(modified)}


def _parse_http_date(s: str) -> float:
    try:
        return datetime.datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return 0.0


# ---------- Folder ----------

class LocalDriver:
    def __init__(self, cfg: dict):
        self.dir = Path(cfg.get("path", "").strip())

    def _ready(self) -> Path:
        if not str(self.dir) or not self.dir.is_absolute():
            raise BackupError(tr("The folder must be an absolute path, for example /backups."))
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            probe = self.dir / ".nxtsound-write-test"
            probe.write_text("ok")
            probe.unlink()
        except OSError as e:
            raise BackupError(tr("Cannot write to the folder: {detail}", detail=e.strerror or str(e)))
        return self.dir

    def test(self) -> str:
        self._ready()
        return tr("The folder is writable.")

    def put(self, name: str, path: Path) -> None:
        d = self._ready()
        tmp = d / (name + ".part")
        try:
            tmp.write_bytes(Path(path).read_bytes())
            tmp.replace(d / name)
        except OSError as e:
            raise BackupError(tr("Cannot write to the folder: {detail}", detail=e.strerror or str(e)))

    def list(self) -> list[dict]:
        d = self._ready()
        return [_entry(p.name, p.stat().st_size, p.stat().st_mtime) for p in d.iterdir() if p.is_file() and BACKUP_NAME.match(p.name)]

    def get(self, name: str, dest: Path) -> None:
        Path(dest).write_bytes((self._ready() / _safe(name)).read_bytes())

    def delete(self, name: str) -> None:
        (self._ready() / _safe(name)).unlink(missing_ok=True)


def _safe(name: str) -> str:
    if not BACKUP_NAME.match(name):
        raise BackupError(tr("That is not a backup file of this server."))
    return name


# ---------- S3 (Signature Version 4, path-style addressing) ----------

def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_key(secret: str, date: str, region: str, service: str) -> bytes:
    return _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode(), date), region), service), "aws4_request")


def sigv4_signature(method: str, canonical_uri: str, canonical_query: str, headers: dict, payload_hash: str, amzdate: str, region: str, secret_key: str, service: str = "s3") -> tuple[str, str]:
    """(signed header names, signature) for one request; headers are the lower-case names and values that are signed."""
    signed = ";".join(sorted(headers))
    canonical = "\n".join([method, canonical_uri, canonical_query, "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), signed, payload_hash])
    scope = f"{amzdate[:8]}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    return signed, hmac.new(sigv4_key(secret_key, amzdate[:8], region, service), to_sign.encode(), hashlib.sha256).hexdigest()


def sigv4_headers(method: str, url: str, region: str, access_key: str, secret_key: str, payload_hash: str, now: datetime.datetime | None = None, service: str = "s3") -> dict:
    """The headers (Host, x-amz-date, x-amz-content-sha256, Authorization) that sign one request."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    u = urllib.parse.urlsplit(url)
    canonical_uri = urllib.parse.quote(urllib.parse.unquote(u.path) or "/", safe="/-_.~")
    query = sorted((urllib.parse.quote(k, safe="-_.~"), urllib.parse.quote(v, safe="-_.~")) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True))
    canonical_query = "&".join(f"{k}={v}" for k, v in query)
    signed_headers = {"host": u.netloc, "x-amz-content-sha256": payload_hash, "x-amz-date": amzdate}
    signed, sig = sigv4_signature(method, canonical_uri, canonical_query, signed_headers, payload_hash, amzdate, region, secret_key, service)
    scope = f"{amzdate[:8]}/{region}/{service}/aws4_request"
    return {"Host": u.netloc, "x-amz-date": amzdate, "x-amz-content-sha256": payload_hash,
            "Authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"}


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class S3Driver:
    def __init__(self, cfg: dict, transport: httpx.BaseTransport | None = None):
        self.endpoint = cfg.get("endpoint", "").strip().rstrip("/")
        self.region = cfg.get("region", "").strip() or "us-east-1"
        self.bucket = cfg.get("bucket", "").strip()
        self.prefix = cfg.get("prefix", "").strip().lstrip("/")
        if self.prefix and not self.prefix.endswith("/"):
            self.prefix += "/"
        self.ak, self.sk = cfg.get("access_key", "").strip(), cfg.get("secret_key", "")
        self.transport = transport

    def _url(self, key: str = "", query: str = "") -> str:
        path = f"/{self.bucket}" + (f"/{urllib.parse.quote(key, safe='/-_.~')}" if key else "")
        return f"{self.endpoint}{path}" + (f"?{query}" if query else "")

    def _signed(self, method: str, url: str, payload_hash: str = hashlib.sha256(b"").hexdigest()) -> dict:
        if not (self.endpoint.startswith(("http://", "https://")) and self.bucket and self.ak and self.sk):
            raise BackupError(tr("Endpoint, bucket and keys are needed."))
        return sigv4_headers(method, url, self.region, self.ak, self.sk, payload_hash)

    def _request(self, method: str, url: str, payload_hash: str = hashlib.sha256(b"").hexdigest(), content=None) -> httpx.Response:
        headers = self._signed(method, url, payload_hash)
        try:
            with httpx.Client(timeout=TIMEOUT, transport=self.transport) as c:
                r = c.request(method, url, headers=headers, content=content)
        except httpx.HTTPError as e:
            raise BackupError(tr("Cannot reach the server: {detail}", detail=str(e) or e.__class__.__name__))
        if r.status_code >= 400:
            raise BackupError(_s3_error(r))
        return r

    def test(self) -> str:
        n = len(self.list())
        return tr("Connected. {n} backups found.", n=n)

    def put(self, name: str, path: Path) -> None:
        digest = _file_sha256(path)
        with open(path, "rb") as f:
            self._request("PUT", self._url(self.prefix + name), digest, content=f)

    def list(self) -> list[dict]:
        out, token = [], ""
        while True:
            q = "list-type=2&prefix=" + urllib.parse.quote(self.prefix, safe="") + (f"&continuation-token={urllib.parse.quote(token, safe='')}" if token else "")
            r = self._request("GET", self._url("", q))
            root = ET.fromstring(r.text)
            ns = {"s": root.tag.split("}")[0][1:]} if root.tag.startswith("{") else {}
            find = (lambda e, t: e.find(f"s:{t}", ns)) if ns else (lambda e, t: e.find(t))
            findall = (lambda e, t: e.findall(f"s:{t}", ns)) if ns else (lambda e, t: e.findall(t))
            for c in findall(root, "Contents"):
                key = find(c, "Key").text or ""
                name = key[len(self.prefix):]
                if BACKUP_NAME.match(name):
                    mod = find(c, "LastModified").text or ""
                    try:
                        ts = datetime.datetime.fromisoformat(mod.replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        ts = 0.0
                    out.append(_entry(name, int(find(c, "Size").text or 0), ts))
            trunc = find(root, "IsTruncated")
            nxt = find(root, "NextContinuationToken")
            if trunc is not None and trunc.text == "true" and nxt is not None and nxt.text:
                token = nxt.text
            else:
                return out

    def get(self, name: str, dest: Path) -> None:
        url = self._url(self.prefix + _safe(name))
        headers = self._signed("GET", url)
        try:
            with httpx.Client(timeout=TIMEOUT, transport=self.transport) as c, c.stream("GET", url, headers=headers) as r:
                if r.status_code >= 400:
                    r.read()
                    raise BackupError(_s3_error(r))
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        except httpx.HTTPError as e:
            raise BackupError(tr("Cannot reach the server: {detail}", detail=str(e) or e.__class__.__name__))

    def delete(self, name: str) -> None:
        self._request("DELETE", self._url(self.prefix + _safe(name)))


def _s3_error(r: httpx.Response) -> str:
    code = ""
    try:
        e = ET.fromstring(r.text)
        code = (e.findtext("Code") or e.findtext("{*}Code") or "").strip()
    except ET.ParseError:
        pass
    if r.status_code in (401, 403):
        return tr("The service refused the keys ({code}). Check access key, secret key, region and bucket.", code=code or r.status_code)
    if r.status_code == 404:
        return tr("The bucket was not found ({code}).", code=code or 404)
    return tr("The service answered with an error: {code}", code=code or r.status_code)


# ---------- WebDAV (Nextcloud, ownCloud, ...) ----------

class WebDavDriver:
    def __init__(self, cfg: dict, transport: httpx.BaseTransport | None = None):
        url = cfg.get("url", "").strip().rstrip("/")
        user = cfg.get("user", "").strip()
        if url and "://" not in url:
            url = "https://" + url
        if url and "/remote.php/" not in url and urllib.parse.urlsplit(url).path in ("", "/"):
            url = f"{url}/remote.php/dav/files/{urllib.parse.quote(user)}"  # a plain Nextcloud / ownCloud address
        self.base, self.user, self.password = url, user, cfg.get("password", "")
        folder = cfg.get("folder", "").strip().strip("/")
        self.folder = [p for p in folder.split("/") if p]
        self.transport = transport

    def _url(self, name: str = "") -> str:
        parts = [urllib.parse.quote(p) for p in self.folder] + ([urllib.parse.quote(name)] if name else [])
        return self.base + ("/" + "/".join(parts) if parts else "")

    def _client(self) -> httpx.Client:
        if not (self.base and self.user and self.password):
            raise BackupError(tr("Address, user name and password are needed."))
        return httpx.Client(timeout=TIMEOUT, auth=(self.user, self.password), transport=self.transport, follow_redirects=False)

    def _call(self, method: str, url: str, ok: tuple, **kw) -> httpx.Response:
        try:
            with self._client() as c:
                r = c.request(method, url, **kw)
        except httpx.HTTPError as e:
            raise BackupError(tr("Cannot reach the server: {detail}", detail=str(e) or e.__class__.__name__))
        if r.status_code in (401, 403):
            raise BackupError(tr("The server refused the login. Check user name and app password."))
        if r.status_code not in ok:
            raise BackupError(tr("The server answered with an error: {code}", code=r.status_code))
        return r

    def _ensure_folder(self) -> None:
        path = ""
        for part in self.folder:
            path += "/" + urllib.parse.quote(part)
            self._call("MKCOL", self.base + path, (201, 301, 405))  # 405: it is there already

    def test(self) -> str:
        self._ensure_folder()
        return tr("Connected. {n} backups found.", n=len(self.list()))

    def put(self, name: str, path: Path) -> None:
        self._ensure_folder()
        with open(path, "rb") as f:
            self._call("PUT", self._url(name), (200, 201, 204), content=f)

    def list(self) -> list[dict]:
        r = self._call("PROPFIND", self._url() + "/", (207, 404), headers={"Depth": "1"})
        if r.status_code == 404:
            return []  # nothing was uploaded yet
        try:
            root = ET.fromstring(r.text)
        except ET.ParseError:
            raise BackupError(tr("The server's answer was not understood."))
        out = []
        for resp in root.iter("{DAV:}response"):
            href = urllib.parse.unquote(resp.findtext("{DAV:}href") or "")
            name = posixpath.basename(href.rstrip("/"))
            if not BACKUP_NAME.match(name):
                continue
            size = resp.findtext(".//{DAV:}getcontentlength") or "0"
            out.append(_entry(name, int(size) if size.isdigit() else 0, _parse_http_date(resp.findtext(".//{DAV:}getlastmodified") or "")))
        return out

    def get(self, name: str, dest: Path) -> None:
        r = self._call("GET", self._url(_safe(name)), (200,))
        Path(dest).write_bytes(r.content)

    def delete(self, name: str) -> None:
        self._call("DELETE", self._url(_safe(name)), (200, 202, 204, 404))


# ---------- FTP / FTPS ----------

class FtpDriver:
    def __init__(self, cfg: dict, factory=None):
        self.cfg = cfg
        self.factory = factory  # tests

    def _connect(self) -> ftplib.FTP:
        c = self.cfg
        host, user, password = c.get("host", "").strip(), c.get("user", ""), c.get("password", "")
        if not (host and user):
            raise BackupError(tr("Server and user name are needed."))
        try:
            mode = c.get("tls", "explicit")
            if self.factory:
                ftp = self.factory(timeout=TIMEOUT)
            elif mode == "none":
                ftp = ftplib.FTP(timeout=TIMEOUT)
            else:
                ctx = ssl.create_default_context()
                if mode == "explicit_any":
                    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
                ftp = ftplib.FTP_TLS(context=ctx, timeout=TIMEOUT)
            ftp.connect(host, int(c.get("port") or 21))
            if isinstance(ftp, ftplib.FTP_TLS):
                ftp.auth()
            ftp.login(user, password)
            if isinstance(ftp, ftplib.FTP_TLS):
                ftp.prot_p()
            self._folder(ftp)
            return ftp
        except ftplib.error_perm as e:
            raise BackupError(tr("The server refused the login or the folder: {detail}", detail=str(e)))
        except (OSError, ftplib.Error, ssl.SSLError) as e:
            raise BackupError(tr("Cannot reach the server: {detail}", detail=str(e) or e.__class__.__name__))

    def _folder(self, ftp: ftplib.FTP) -> None:
        folder = self.cfg.get("folder", "").strip()
        if folder.startswith("/"):
            ftp.cwd("/")
        for part in [p for p in folder.split("/") if p]:
            try:
                ftp.cwd(part)
            except ftplib.error_perm:
                ftp.mkd(part)
                ftp.cwd(part)

    def test(self) -> str:
        ftp = self._connect()
        try:
            return tr("Connected. {n} backups found.", n=len(self._names(ftp)))
        finally:
            _quit(ftp)

    def _names(self, ftp: ftplib.FTP) -> list[str]:
        try:
            names = [posixpath.basename(n) for n in ftp.nlst()]
        except ftplib.error_perm:
            names = []  # an empty folder answers 550 on some servers
        return [n for n in names if BACKUP_NAME.match(n)]

    def put(self, name: str, path: Path) -> None:
        ftp = self._connect()
        try:
            with open(path, "rb") as f:
                ftp.storbinary(f"STOR {name}.part", f)
            ftp.rename(f"{name}.part", name)
        except ftplib.Error as e:
            raise BackupError(tr("Uploading failed: {detail}", detail=str(e)))
        finally:
            _quit(ftp)

    def list(self) -> list[dict]:
        ftp = self._connect()
        try:
            out = []
            for n in self._names(ftp):
                try:
                    ftp.voidcmd("TYPE I")  # listing switches to text mode, but SIZE only works in binary mode
                    size = ftp.size(n) or 0
                except ftplib.Error:
                    size = 0
                ts = 0.0
                try:
                    stamp = ftp.voidcmd(f"MDTM {n}")[4:].strip()
                    ts = datetime.datetime.strptime(stamp[:14], "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc).timestamp()
                except (ftplib.Error, ValueError):
                    pass
                out.append(_entry(n, size, ts))
            return out
        finally:
            _quit(ftp)

    def get(self, name: str, dest: Path) -> None:
        ftp = self._connect()
        try:
            with open(dest, "wb") as f:
                ftp.retrbinary(f"RETR {_safe(name)}", f.write)
        except ftplib.Error as e:
            raise BackupError(tr("Download failed: {detail}", detail=str(e)))
        finally:
            _quit(ftp)

    def delete(self, name: str) -> None:
        ftp = self._connect()
        try:
            ftp.delete(_safe(name))
        except ftplib.error_perm:
            pass
        finally:
            _quit(ftp)


def _quit(ftp) -> None:
    try:
        ftp.quit()
    except (OSError, ftplib.Error):
        try:
            ftp.close()
        except OSError:
            pass


# ---------- SFTP ----------

def _fingerprint(key) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


class SftpDriver:
    def __init__(self, cfg: dict, learn=None):
        self.cfg = cfg
        self.learn = learn  # called with the fingerprint when it was seen for the first time (the caller keeps it)

    def _connect(self):
        import paramiko
        c = self.cfg
        host, user = c.get("host", "").strip(), c.get("user", "")
        if not (host and user and (c.get("password") or c.get("private_key"))):
            raise BackupError(tr("Server, user name and a password or key are needed."))
        pkey = None
        if c.get("private_key"):
            for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
                try:
                    pkey = cls.from_private_key(io.StringIO(c["private_key"]), password=c.get("key_passphrase") or None)
                    break
                except (paramiko.SSHException, ValueError):
                    continue
            if pkey is None:
                raise BackupError(tr("The private key cannot be read (wrong format or passphrase)."))
        try:
            sock = socket.create_connection((host, int(c.get("port") or 22)), timeout=TIMEOUT)
            t = paramiko.Transport(sock)
            t.connect(username=user, password=c.get("password") or None, pkey=pkey)
            seen = _fingerprint(t.get_remote_server_key())
            pinned = (c.get("host_key") or "").strip()
            if pinned and pinned.rstrip("=") != seen:
                t.close()
                raise BackupError(tr("The server's key has changed (expected {expected}, got {got}). If that is intended, clear the fingerprint in the settings.", expected=pinned, got=seen))
            if not pinned and self.learn:
                self.learn(seen)
            sftp = paramiko.SFTPClient.from_transport(t)
            self._folder(sftp)
            return t, sftp
        except BackupError:
            raise
        except paramiko.AuthenticationException:
            raise BackupError(tr("The server refused the login. Check user name, password or key."))
        except (OSError, paramiko.SSHException, EOFError) as e:
            raise BackupError(tr("Cannot reach the server: {detail}", detail=str(e) or e.__class__.__name__))

    def _folder(self, sftp) -> None:
        folder = self.cfg.get("folder", "").strip()
        if folder.startswith("/"):
            sftp.chdir("/")
        for part in [p for p in folder.split("/") if p]:
            try:
                sftp.chdir(part)
            except IOError:
                sftp.mkdir(part)
                sftp.chdir(part)

    def test(self) -> str:
        t, sftp = self._connect()
        try:
            return tr("Connected. {n} backups found.", n=len([a for a in sftp.listdir_attr(".") if BACKUP_NAME.match(a.filename)]))
        finally:
            t.close()

    def put(self, name: str, path: Path) -> None:
        t, sftp = self._connect()
        try:
            sftp.put(str(path), name + ".part")
            try:
                sftp.remove(name)
            except IOError:
                pass
            sftp.rename(name + ".part", name)
        except (OSError, EOFError) as e:
            raise BackupError(tr("Uploading failed: {detail}", detail=str(e)))
        finally:
            t.close()

    def list(self) -> list[dict]:
        t, sftp = self._connect()
        try:
            return [_entry(a.filename, a.st_size or 0, a.st_mtime or 0) for a in sftp.listdir_attr(".") if BACKUP_NAME.match(a.filename)]
        finally:
            t.close()

    def get(self, name: str, dest: Path) -> None:
        t, sftp = self._connect()
        try:
            sftp.get(_safe(name), str(dest))
        except (OSError, EOFError) as e:
            raise BackupError(tr("Download failed: {detail}", detail=str(e)))
        finally:
            t.close()

    def delete(self, name: str) -> None:
        t, sftp = self._connect()
        try:
            sftp.remove(_safe(name))
        except IOError:
            pass
        finally:
            t.close()


def make(kind: str, cfg: dict, **kw):
    """The driver for a type of destination."""
    return {"local": LocalDriver, "s3": S3Driver, "webdav": WebDavDriver, "ftp": FtpDriver, "sftp": SftpDriver}[kind](cfg, **kw)
