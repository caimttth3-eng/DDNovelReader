# -*- coding: utf-8 -*-
"""局域网本地同步：软件内嵌的 WebDAV 子集服务端（v2.2.9）。

设计约束（与开发指南坑 16 / 坑 24 一致）：
- **根目录锁死** `<数据目录>/lan/`，只暴露同步数据，绝不暴露 `library.json`（里面有账号密码）
- **只实现客户端真正用到的方法**：PROPFIND(0/1) / GET / HEAD / PUT / MKCOL / MOVE / DELETE / OPTIONS
  不做通用 WebDAV（无 LOCK/PROPPATCH/COPY），不能被资源管理器当网盘挂载
- **随软件启停**：不注册系统服务；退出时 `stop()` 释放端口与线程
- **后台线程绝不碰 Tk**：只更新加锁的状态快照，UI 自己轮询读取
- Basic 鉴权：用户名固定，密码为配对码（由 UI 生成，可重新生成作废）

线程模型：1 个 acceptor 线程（阻塞在 accept，CPU≈0）+ 按需 per-request 线程（daemon）。
"""
import base64
import io
import json
import os
import shutil
import socket
import threading
import time
import urllib.parse
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8930
DEFAULT_USER = "duoduo"
DEFAULT_REMOTE_DIR = "DDNovelReader"
LAN_ROOT_DIRNAME = "lan"
MAX_UPLOAD_BYTES = 256 * 1024 * 1024      # 单文件上限，防止被当网盘写爆
LOG_LIMIT = 200

ST_STOPPED = "stopped"
ST_STARTING = "starting"
ST_RUNNING = "running"
ST_ERROR = "error"

_CONTENT_TYPES = {
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".epub": "application/epub+zip",
    ".mobi": "application/x-mobipocket-ebook",
    ".azw3": "application/octet-stream",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".html": "text/html; charset=utf-8",
    ".chm": "application/vnd.ms-htmlhelp",
    ".zip": "application/zip",
}


def local_addresses():
    """本机局域网 IPv4 列表（优先常见私网段）。"""
    cand = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            cand.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in cand:
                cand.append(ip)
    except OSError:
        pass
    out = [ip for ip in cand if ip and not ip.startswith("127.") and not ip.startswith("169.254.")]

    def rank(ip):
        if ip.startswith("192.168."):
            return 0
        if ip.startswith("10."):
            return 1
        if ip.startswith("172."):
            return 2
        return 3

    return sorted(out, key=rank)


def new_token():
    """配对码：12 位小写十六进制，方便手机手填。"""
    return os.urandom(6).hex()


class _LanHTTPServer(ThreadingHTTPServer):
    """注意：Windows 上 SO_REUSEADDR 允许**同端口重复绑定**（第二个实例会静默抢走端口），
    所以 Windows 必须禁止 reuse；POSIX 保留 reuse 以便快速重启。"""

    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def _port_in_use(port):
    """探测本机端口是否已被监听（用于给出「已有实例在运行」这种可读提示）。"""
    if not port:
        return False
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.35)
    try:
        return probe.connect_ex(("127.0.0.1", int(port))) == 0
    finally:
        probe.close()


class LanServer:
    """内嵌同步服务。所有公开方法线程安全，可在 Tk 主线程调用。"""

    def __init__(self, root, port=DEFAULT_PORT, username=DEFAULT_USER, password="",
                 remote_dir=DEFAULT_REMOTE_DIR, logger=None):
        self.root = os.path.abspath(root)
        self.port = int(port)
        self.username = username or DEFAULT_USER
        self.password = password or ""
        self.remote_dir = (remote_dir or DEFAULT_REMOTE_DIR).strip().strip("/")
        self._logger = logger or (lambda _m: None)
        self._lock = threading.Lock()
        self._httpd = None
        self._thread = None
        self._state = {
            "state": ST_STOPPED,
            "port": self.port,
            "error": "",
            "started_at": 0.0,
            "requests": 0,
            "errors": 0,
            "last_error": "",
            "last_request_at": 0.0,
            "last_request": "",
            "bytes_out": 0,
            "bytes_in": 0,
            "active": 0,
            "clients": {},
            "dirty": False,
            "dirty_at": 0.0,
        }
        self._log_lines = []

    # ------------------------------------------------ 生命周期
    def start(self):
        """启动服务；返回 (ok, message)。重复调用幂等。"""
        with self._lock:
            if self._state["state"] == ST_RUNNING and self._httpd is not None:
                return True, ""
            self._state["state"] = ST_STARTING
            self._state["error"] = ""
        try:
            os.makedirs(self.root, exist_ok=True)
            os.makedirs(os.path.join(self.root, self.remote_dir), exist_ok=True)
            if _port_in_use(self.port):
                raise OSError(10048, "端口 %d 已被占用（是否已有一个多多朗读实例在运行？请换端口）" % self.port)
            handler = _make_handler(self)
            httpd = _LanHTTPServer(("", self.port), handler)
        except OSError as e:
            with self._lock:
                self._state["state"] = ST_ERROR
                self._state["error"] = _port_error_text(e)
                self._state["last_error"] = self._state["error"]
            self._append_log("启动失败：%s" % e)
            return False, self._state["error"]
        except Exception as e:                                   # noqa: BLE001
            with self._lock:
                self._state["state"] = ST_ERROR
                self._state["error"] = str(e)
                self._state["last_error"] = str(e)
            self._append_log("启动失败：%s" % e)
            return False, str(e)

        thread = threading.Thread(target=self._serve, args=(httpd,), name="lan-sync-server", daemon=True)
        with self._lock:
            self._httpd = httpd
            self._thread = thread
            self._state.update({
                "state": ST_RUNNING,
                "port": int(httpd.server_address[1]),
                "started_at": time.time(),
                "error": "",
            })
            self._log_lines = []
        thread.start()
        self._append_log("服务已启动，端口 %s，根目录 %s" % (self._state["port"], self.root))
        return True, ""

    def _serve(self, httpd):
        try:
            httpd.serve_forever(poll_interval=0.5)
        except Exception as e:                                   # noqa: BLE001
            with self._lock:
                self._state["state"] = ST_ERROR
                self._state["error"] = str(e)
            self._append_log("服务异常退出：%s" % e)

    def stop(self, timeout=2.0):
        """停止服务；返回是否干净停止。"""
        with self._lock:
            httpd = self._httpd
            thread = self._thread
            self._httpd = None
            self._thread = None
            if httpd is None:
                self._state["state"] = ST_STOPPED
                self._state["started_at"] = 0.0
                return True
        try:
            httpd.shutdown()
        except Exception:                                        # noqa: BLE001
            pass
        try:
            httpd.server_close()
        except Exception:                                        # noqa: BLE001
            pass
        if thread is not None:
            thread.join(timeout)
            clean = not thread.is_alive()
        else:
            clean = True
        with self._lock:
            self._state["state"] = ST_STOPPED
            self._state["started_at"] = 0.0
            self._state["active"] = 0
        self._append_log("服务已停止")
        return clean

    def running(self):
        with self._lock:
            return self._state["state"] == ST_RUNNING

    # ------------------------------------------------ 状态与配对信息
    def status(self):
        with self._lock:
            snap = dict(self._state)
            snap["clients"] = dict(self._state["clients"])
            snap["uptime"] = (time.time() - snap["started_at"]) if snap["started_at"] else 0.0
        snap["root"] = self.root
        snap["addresses"] = local_addresses() if snap["state"] == ST_RUNNING else []
        snap["log"] = list(self._log_lines[-8:])
        return snap

    def pairing_info(self):
        """给手机端手填的四项（与云端 WebDAV 完全同构，便于同一份安卓指南）。"""
        port = self.status().get("port") or self.port
        ips = local_addresses()
        host = ips[0] if ips else "127.0.0.1"
        return {
            "url": "http://%s:%s/" % (host, port),
            "urls": ["http://%s:%s/" % (ip, port) for ip in ips] or ["http://127.0.0.1:%s/" % port],
            "user": self.username,
            "pass": self.password,
            "dir": self.remote_dir,
            "port": port,
        }

    # ------------------------------------------------ 变更通知（事件驱动合并）
    def take_dirty(self):
        """取出并清空"远端有写入"标记；供 UI 决定是否触发一次合并（0 轮询网络）。"""
        with self._lock:
            if not self._state["dirty"]:
                return False
            self._state["dirty"] = False
            self._state["dirty_at"] = time.time()
            return True

    def mark_dirty(self):
        with self._lock:
            self._state["dirty"] = True

    # ------------------------------------------------ 统计（由 handler 调用）
    def _note_request(self, method, path, code, nbytes=0, incoming=0):
        with self._lock:
            st = self._state
            st["requests"] += 1
            st["last_request_at"] = time.time()
            st["last_request"] = "%s %s → %s" % (method, path, code)
            st["bytes_out"] += int(nbytes or 0)
            st["bytes_in"] += int(incoming or 0)
            if code >= 400:
                st["errors"] += 1
                if code >= 500:
                    st["last_error"] = "%s %s → %s" % (method, path, code)
            ip = getattr(self, "_cur_ip", "") or ""
            if ip:
                st["clients"][ip] = time.time()
        self._append_log("%s %s → %s" % (method, path, code))

    def _enter(self):
        with self._lock:
            self._state["active"] += 1

    def _leave(self):
        with self._lock:
            if self._state["active"] > 0:
                self._state["active"] -= 1

    def _append_log(self, line):
        stamp = time.strftime("%H:%M:%S")
        item = "[%s] %s" % (stamp, line)
        with self._lock:
            self._log_lines.append(item)
            if len(self._log_lines) > LOG_LIMIT:
                del self._log_lines[:len(self._log_lines) - LOG_LIMIT]
        try:
            self._logger(item)
        except Exception:                                        # noqa: BLE001
            pass

    # ------------------------------------------------ 路径安全
    def resolve(self, url_path):
        """URL 路径 → 本地绝对路径；越界/异常返回 None。"""
        path = urllib.parse.unquote(urllib.parse.urlparse(url_path).path or "")
        segs = [s for s in path.split("/") if s]
        for seg in segs:
            if seg in (".", "..") or "\\" in seg or "\x00" in seg:
                return None
        target = os.path.abspath(os.path.join(self.root, *segs)) if segs else self.root
        root = self.root.rstrip(os.sep) + os.sep
        if target != self.root and not target.startswith(root):
            return None
        return target


def _port_error_text(e):
    code = getattr(e, "errno", None) or getattr(e, "winerror", None)
    if code in (10048, 98, 48):     # WSAEADDRINUSE / EADDRINUSE
        return "端口已被占用，请换一个端口（或先退出已在运行的多多朗读）"
    if code in (10013, 1):          # WSAEACCES / EPERM
        return "端口无权限监听（可能被防火墙或安全软件拦截）"
    return "启动失败：%s" % e


def _make_handler(server):
    """为每个服务实例生成 handler 类（避免全局状态）。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "DDNovelReader-LAN/1.0"
        protocol_version = "HTTP/1.1"

        # ---------- 基础设施 ----------
        def log_message(self, fmt, *args):     # 屏蔽默认 stderr 输出
            return

        def _authed(self):
            header = self.headers.get("Authorization") or ""
            if not header.lower().startswith("basic "):
                return False
            try:
                raw = base64.b64decode(header.split(None, 1)[1]).decode("utf-8", "replace")
            except Exception:                    # noqa: BLE001
                return False
            user, _, pwd = raw.partition(":")
            return user == server.username and pwd == server.password

        def _deny(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="DDNovelReader"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None, count=True):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body and self.command != "HEAD":
                try:
                    self.wfile.write(body)
                except Exception:                # noqa: BLE001
                    pass
            if count:
                server._cur_ip = self.client_address[0] if self.client_address else ""
                server._note_request(self.command, self.path, code,
                                     nbytes=len(body) if self.command != "HEAD" else 0)

        def _fail(self, code, text=""):
            self._send(code, text or _status_text(code))

        # ---------- 5 个 WebDAV + GET/HEAD ----------
        def do_OPTIONS(self):                    # noqa: N802
            if not self._authed():
                return self._deny()
            self._send(200, b"", extra={
                "DAV": "1",
                "Allow": "OPTIONS, PROPFIND, GET, HEAD, PUT, MKCOL, MOVE, DELETE",
            })

        def do_PROPFIND(self):                   # noqa: N802
            server._enter()
            try:
                if not self._authed():
                    return self._deny()
                target = server.resolve(self.path)
                if target is None:
                    return self._fail(403)
                if not os.path.exists(target):
                    return self._fail(404)
                depth = str(self.headers.get("Depth", "1")).strip().lower()
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if length:
                    try:
                        self.rfile.read(length)
                    except Exception:            # noqa: BLE001
                        pass
                self._send(207, _multistatus(self.path, target, depth),
                           ctype="application/xml; charset=utf-8")
            finally:
                server._leave()

        def do_GET(self):                        # noqa: N802
            server._enter()
            try:
                if not self._authed():
                    return self._deny()
                target = server.resolve(self.path)
                if target is None:
                    return self._fail(403)
                if os.path.isdir(target):
                    return self._fail(404)
                if not os.path.isfile(target):
                    return self._fail(404)
                size = os.path.getsize(target)
                start, end = 0, size - 1
                extra = {"Accept-Ranges": "bytes",
                         "ETag": _etag(target),
                         "Last-Modified": formatdate(os.path.getmtime(target), usegmt=True)}
                rng = (self.headers.get("Range") or "").strip()
                code = 200
                if rng.startswith("bytes=") and size > 0:
                    spec = rng[6:].split(",")[0].strip()
                    a, _, b = spec.partition("-")
                    try:
                        if a == "":
                            start = max(0, size - int(b))
                            end = size - 1
                        else:
                            start = max(0, int(a))
                            end = int(b) if b else size - 1
                        end = min(end, size - 1)
                        if start > end:
                            raise ValueError
                        code = 206
                        extra["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
                    except ValueError:
                        start, end, code = 0, size - 1, 200
                        extra.pop("Content-Range", None)
                with open(target, "rb") as f:
                    f.seek(start)
                    body = f.read(end - start + 1)
                self._send(code, body, ctype=_CONTENT_TYPES.get(
                    os.path.splitext(target)[1].lower(), "application/octet-stream"), extra=extra)
            except OSError as e:
                self._fail(500, str(e))
            finally:
                server._leave()

        def do_HEAD(self):                       # noqa: N802
            return self.do_GET()

        def do_PUT(self):                        # noqa: N802
            server._enter()
            try:
                if not self._authed():
                    return self._deny()
                target = server.resolve(self.path)
                if target is None:
                    return self._fail(403)
                if os.path.isdir(target):
                    return self._fail(405)
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return self._fail(411)
                if length > MAX_UPLOAD_BYTES:
                    return self._fail(507)
                existed = os.path.exists(target)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                tmp = "%s.part-%d" % (target, os.getpid())
                got = 0
                try:
                    with open(tmp, "wb") as f:
                        while got < length:
                            chunk = self.rfile.read(min(65536, length - got))
                            if not chunk:
                                break
                            f.write(chunk)
                            got += len(chunk)
                    if got != length:
                        raise IOError("上传中断：%d/%d 字节" % (got, length))
                    os.replace(tmp, target)
                finally:
                    if os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
                server.mark_dirty()
                server._cur_ip = self.client_address[0] if self.client_address else ""
                server._note_request("PUT", self.path, 201 if not existed else 204, incoming=got)
                self.send_response(201 if not existed else 204)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except OSError as e:
                self._fail(500, str(e))
            finally:
                server._leave()

        def do_MKCOL(self):                      # noqa: N802
            server._enter()
            try:
                if not self._authed():
                    return self._deny()
                target = server.resolve(self.path)
                if target is None:
                    return self._fail(403)
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if length:
                    try:
                        self.rfile.read(length)
                    except Exception:            # noqa: BLE001
                        pass
                if os.path.isdir(target):
                    return self._fail(405)
                if os.path.exists(target):
                    return self._fail(405)
                os.makedirs(target, exist_ok=True)
                self._send(201)
            except OSError as e:
                self._fail(500, str(e))
            finally:
                server._leave()

        def do_MOVE(self):                       # noqa: N802
            server._enter()
            try:
                if not self._authed():
                    return self._deny()
                src = server.resolve(self.path)
                dest_raw = self.headers.get("Destination") or ""
                dest = server.resolve(dest_raw)
                if src is None or dest is None:
                    return self._fail(403)
                if not os.path.exists(src):
                    return self._fail(404)
                overwrite = str(self.headers.get("Overwrite", "T")).strip().upper() != "F"
                if os.path.exists(dest) and not overwrite:
                    return self._fail(412)
                existed = os.path.exists(dest)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if existed:
                    if os.path.isdir(dest) and not os.path.islink(dest):
                        shutil.rmtree(dest, ignore_errors=True)
                    else:
                        try:
                            os.remove(dest)
                        except OSError:
                            pass
                shutil.move(src, dest)
                server.mark_dirty()
                self._send(204 if existed else 201)
            except OSError as e:
                self._fail(500, str(e))
            finally:
                server._leave()

        def do_DELETE(self):                     # noqa: N802
            server._enter()
            try:
                if not self._authed():
                    return self._deny()
                target = server.resolve(self.path)
                if target is None:
                    return self._fail(403)
                if target == server.root:
                    return self._fail(403)
                if not os.path.exists(target):
                    return self._fail(404)
                if os.path.isdir(target):
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    os.remove(target)
                server.mark_dirty()
                self._send(204)
            except OSError as e:
                self._fail(500, str(e))
            finally:
                server._leave()

    return Handler


def _etag(path):
    try:
        st = os.stat(path)
        return '"%x-%x"' % (int(st.st_mtime), st.st_size)
    except OSError:
        return '""'


def _status_text(code):
    return {301: "Moved", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
            404: "Not Found", 405: "Method Not Allowed", 409: "Conflict", 411: "Length Required",
            412: "Precondition Failed", 507: "Insufficient Storage"}.get(code, "Error")


def _xml_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _quote_path(path):
    return "/".join(urllib.parse.quote(seg, safe="") for seg in str(path).split("/"))


def _prop_xml(url_path, local_path):
    """单个 <D:response>；collection 用 resourcetype 表达。"""
    href = _quote_path(url_path)
    if os.path.isdir(local_path):
        if not href.endswith("/"):
            href += "/"
        rtype = "<D:resourcetype><D:collection/></D:resourcetype>"
        size = ""
    else:
        rtype = "<D:resourcetype/>"
        try:
            size = "<D:getcontentlength>%d</D:getcontentlength>" % os.path.getsize(local_path)
        except OSError:
            size = "<D:getcontentlength>0</D:getcontentlength>"
    try:
        mtime = formatdate(os.path.getmtime(local_path), usegmt=True)
    except OSError:
        mtime = formatdate(time.time(), usegmt=True)
    return (
        "<D:response><D:href>%s</D:href><D:propstat><D:prop>%s%s"
        "<D:getlastmodified>%s</D:getlastmodified><D:getetag>%s</D:getetag>"
        "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
        % (href, rtype, size, mtime, _etag(local_path))
    )


def _multistatus(url_path, local_path, depth):
    """注意：整个文档只允许一个 XML 声明（多声明会让 ElementTree 报 junk after document element）。"""
    parts = ['<?xml version="1.0" encoding="utf-8"?>',
             '<D:multistatus xmlns:D="DAV:">']
    base = url_path if url_path.startswith("/") else "/" + url_path
    base = base.split("?")[0]
    if os.path.isdir(local_path) and not base.endswith("/"):
        base += "/"
    parts.append(_prop_xml(base, local_path))
    if depth != "0" and os.path.isdir(local_path):
        try:
            names = sorted(os.listdir(local_path))
        except OSError:
            names = []
        for name in names:
            child = os.path.join(local_path, name)
            parts.append(_prop_xml(base + name, child))
    parts.append("</D:multistatus>")
    return "".join(parts)
