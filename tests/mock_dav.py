# -*- coding: utf-8 -*-
"""测试用极简 WebDAV 服务器（只在 127.0.0.1 上跑，够 PC/安卓两端读写即可）。

覆盖同步用到的全部方法：OPTIONS / PROPFIND / GET / PUT / DELETE / MKCOL / MOVE。
根目录挂在 `<root>/dav/` 下，与 mock_webdav.py 的 URL 约定一致。
"""
import base64
import os
import shutil
import threading
import urllib.parse
import xml.sax.saxutils as sx
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class DavHandler(BaseHTTPRequestHandler):
    root = ""
    user = "test"
    pw = "test"
    prefix = "/dav"

    # ---------- 工具 ----------
    def _rel(self):
        path = urllib.parse.urlparse(self.path).path
        path = urllib.parse.unquote(path)
        if self.prefix and path.startswith(self.prefix + "/"):
            path = path[len(self.prefix) + 1:]
        elif self.prefix and path == self.prefix:
            path = ""
        return path.strip("/")

    def _abs(self):
        return os.path.join(self.root, self._rel())

    def _href(self, full, is_dir):
        rel = os.path.relpath(full, self.root).replace(os.sep, "/")
        if rel == ".":
            rel = ""
        url = self.prefix + "/" + urllib.parse.quote(rel)
        if is_dir and not url.endswith("/"):
            url += "/"
        return url

    def _auth_ok(self):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return False
        try:
            u, _, p = base64.b64decode(h[6:]).decode().partition(":")
        except Exception:
            return False
        return u == self.user and p == self.pw

    def _deny(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="mock"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def _body(self):
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            buf = []
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    break
                size = int(line.split(b";")[0], 16)
                if size == 0:
                    while True:
                        t = self.rfile.readline()
                        if not t or t in (b"\r\n", b"\n"):
                            break
                    break
                buf.append(self.rfile.read(size))
                self.rfile.read(2)
            return b"".join(buf)
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    # ---------- 方法 ----------
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("DAV", "1,2")
        self.send_header("Allow", "OPTIONS,PROPFIND,GET,PUT,DELETE,MKCOL,MOVE")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PROPFIND(self):
        if not self._auth_ok():
            return self._deny()
        full = self._abs()
        if not os.path.exists(full):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        items = []
        if os.path.isdir(full):
            items.append((full, True))
            for name in sorted(os.listdir(full)):
                p = os.path.join(full, name)
                items.append((p, os.path.isdir(p)))
        else:
            items.append((full, False))
        parts = ['<?xml version="1.0" encoding="utf-8"?>', '<D:multistatus xmlns:D="DAV:">']
        for p, is_dir in items:
            st = os.stat(p)
            parts.append("<D:response>")
            parts.append("<D:href>%s</D:href>" % sx.escape(self._href(p, is_dir)))
            parts.append("<D:propstat><D:prop>")
            if is_dir:
                parts.append("<D:resourcetype><D:collection/></D:resourcetype>")
            else:
                parts.append("<D:resourcetype/>")
                parts.append("<D:getcontentlength>%d</D:getcontentlength>" % st.st_size)
            parts.append("</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>")
            parts.append("</D:response>")
        parts.append("</D:multistatus>")
        body = "".join(parts).encode("utf-8")
        self.send_response(207)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._auth_ok():
            return self._deny()
        full = self._abs()
        if not os.path.isfile(full):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = open(full, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_PUT(self):
        if not self._auth_ok():
            return self._deny()
        full = self._abs()
        os.makedirs(os.path.dirname(full), exist_ok=True)
        data = self._body()
        existed = os.path.exists(full)
        with open(full, "wb") as f:
            f.write(data)
        self.send_response(204 if existed else 201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):
        if not self._auth_ok():
            return self._deny()
        full = self._abs()
        if os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)
        elif os.path.exists(full):
            os.remove(full)
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_MKCOL(self):
        if not self._auth_ok():
            return self._deny()
        os.makedirs(self._abs(), exist_ok=True)
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_MOVE(self):
        if not self._auth_ok():
            return self._deny()
        dest = self.headers.get("Destination", "")
        if not dest:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        src = self._abs()
        path = urllib.parse.unquote(urllib.parse.urlparse(dest).path)
        if self.prefix and path.startswith(self.prefix + "/"):
            path = path[len(self.prefix) + 1:]
        dst = os.path.join(self.root, path.strip("/"))
        if not os.path.exists(src):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        existed = os.path.exists(dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if existed:
            os.remove(dst)
        shutil.move(src, dst)
        self.send_response(204 if existed else 201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


class MockDav:
    """with MockDav() as dav: dav.url → http://127.0.0.1:<port>/dav"""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def __enter__(self):
        handler = type("H", (DavHandler,), {"root": self.root})
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d/dav" % self.port
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
        return False
