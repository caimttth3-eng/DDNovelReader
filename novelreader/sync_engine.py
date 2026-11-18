# -*- coding: utf-8 -*-
"""WebDAV 数据同步：跨电脑同步书架信息、阅读进度与书签。

设计要点
--------
1. 元数据同步"能在别的机器上重新定位到同一本书"的信息；因此每本书需要
   一个与路径无关的稳定标识 `content_key`（书籍路径 MD5 的 book_id 会随
   机器变化，不能用于互通）。音频缓存、解析缓存仍不同步。
2. 远端按书分文件存放，缩小冲突面：
       <远端目录>/v1/books/<content_key>.json
       <远端目录>/v1/sources/<content_key>.<ext>   # 书源文件（用于拉取远端独有书）
       <远端目录>/v1/devices/<device_id>.json
       <远端目录>/v1/meta.json
   记录里的 `source` 字段描述源文件（ext / size / name）；对端只有记录、
   本地没有这本书时，按该字段下载源文件、本地解析分章后加入书架，
   再把远端进度与书签套上去。
3. 冲突策略：阅读进度按 updated_at 新者胜；书签按 id 取并集，不丢笔记。
4. 传输只用标准库 urllib（项目零新依赖），协议走 WebDAV 的
   PROPFIND / MKCOL / PUT / MOVE / GET。
"""
import base64
import hashlib
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .i18n import T as _T
# 指纹算法与音频缓存目录键共用同一实现（novelreader/content_key.py），
# 保证 WebDAV 里写的 content_key 与本地缓存一级目录名永远相等。
from .content_key import (
    CK_VERSION,
    CK_VERSION_LEGACY,
    compute_legacy_key,
    content_key_of_book,
    norm_text,
    sample_text_for_book,
)

SCHEMA_VERSION = 2
DEFAULT_REMOTE_DIR = "DDNovelReader"
USER_AGENT = "DDNovelReader-WebDAVSync/1"
SOURCES_DIR = "v1/sources"          # 远端书源文件目录
MAX_SOURCE_BYTES = 512 * 1024 * 1024   # 单个源文件上限：512MB
_DAV = "{DAV:}"

# 时间比较容差（秒）：两端时间戳差值在此以内视为"同一时刻"，避免反复推送
TIME_TOL = 0.5
# 本地删除墓碑保留时长：过期后不再向远端推删除事件（远端早已删掉就不用管了）
TOMBSTONE_TTL = 120 * 86400

_SYNC_LOCK = threading.Lock()


def _num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _bookmarks_of(book):
    bms = (book or {}).get("bookmarks")
    return [x for x in bms if isinstance(x, dict)] if isinstance(bms, list) else []


def progress_at_of(book):
    """书的"进度版本时间"。老数据没有 progress_at 时退回 last_read_at 做一次迁移。"""
    book = book or {}
    if book.get("progress_at") is not None:
        return _num(book.get("progress_at"))
    return _num(book.get("last_read_at"))


def bookmarks_at_of(book):
    t = 0.0
    for bm in _bookmarks_of(book):
        t = max(t, _num(bm.get("created_at")))
    return t


def book_activity_of(book):
    """本地书最后一次"内容发生变化"的时间（进度 / 书签 / 加入书架）。"""
    book = book or {}
    return max(progress_at_of(book), bookmarks_at_of(book), _num(book.get("added_at")))


def normalize_percent(v):
    """安卓端内部 percent 是 0~1，PC 是 0~100；远端统一按 PC 口径（0~100）存。

    入站时若拿到 ≤1 的值（旧安卓记录），按比例折算成百分比，避免书架上显示 "0.9%"。
    """
    p = _num(v)
    if p <= 0:
        return 0.0
    if p <= 1.0:
        return p * 100.0
    return min(100.0, p)

# 同步用的设置项（全部落在 library.json 的 settings 里）
CFG_KEYS = (
    "sync_server",      # 服务器地址，如 https://dav.jianguoyun.com/dav/
    "sync_user",        # 账号
    "sync_pass",        # 密码
    "sync_dir",         # 远端目录（相对服务器根）
    "sync_device_id",   # 本机设备 ID（自动生成，不用改）
    "sync_device_name", # 设备显示名
    "sync_auto",        # 启动时 + 定时自动同步
    "sync_verify_tls",  # 是否校验证书（自建服务常用自签名证书）
    "sync_last_time",   # 上次同步时间戳
)


# ---------------------------------------------------------------- 工具函数
# _norm_text / compute_content_key 由 content_key.py 提供（见文件头 import），
# 这里保留模块级名字供既有调用与外部引用，实现只有一份。
_norm_text = norm_text


def default_device_name():
    try:
        import platform
        return (platform.node() or "").strip() or "PC"
    except Exception:
        return "PC"


def new_device_id():
    return hashlib.md5(
        ("dev|%s|%s" % (default_device_name(), time.time())).encode("utf-8")
    ).hexdigest()[:12]


def ensure_device(storage, settings=None):
    """确保本机有稳定的 device_id / device_name，返回 (id, name)。"""
    st = settings if settings is not None else storage.settings()
    did = str(st.get("sync_device_id") or "").strip()
    if not did:
        did = new_device_id()
        st["sync_device_id"] = did
        try:
            storage.set_setting("sync_device_id", did)
        except Exception:
            pass
    name = str(st.get("sync_device_name") or "").strip() or default_device_name()
    if not st.get("sync_device_name"):
        st["sync_device_name"] = name
        try:
            storage.set_setting("sync_device_name", name)
        except Exception:
            pass
    return did, name


# ---------------------------------------------------------------- WebDAV 客户端
class WebDAVError(Exception):
    """WebDAV 传输/协议错误。status 为 HTTP 状态码（无响应时为 None）。"""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class WebDAVClient:
    def __init__(self, base_url, username="", password="", remote_dir=DEFAULT_REMOTE_DIR,
                 timeout=20, verify_tls=True):
        url = (base_url or "").strip()
        if not url:
            raise WebDAVError(_T("服务器地址为空"))
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
            url = "https://" + url
        self.base_url = url.rstrip("/") + "/"
        self.username = username or ""
        self.password = password or ""
        self.remote_dir = (remote_dir or DEFAULT_REMOTE_DIR).strip().strip("/")
        self.timeout = int(timeout)

        handlers = [urllib.request.ProxyHandler()]
        if str(self.base_url).lower().startswith("https"):
            if verify_tls:
                ctx = ssl.create_default_context()
            else:
                ctx = ssl._create_unverified_context()
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self._opener = urllib.request.build_opener(*handlers)

    # ---------- URL / 请求 ----------
    @staticmethod
    def _quote_seg(seg):
        return urllib.parse.quote(str(seg), safe="")

    def _url(self, rel=""):
        """拼接远端 URL；rel 用 / 分隔，逐段转义（兼容中文目录名）。"""
        parts = []
        if self.remote_dir:
            parts.append("/".join(self._quote_seg(s) for s in self.remote_dir.split("/") if s))
        if rel:
            parts.append("/".join(self._quote_seg(s) for s in str(rel).split("/") if s))
        tail = "/".join([p for p in parts if p])
        return self.base_url + (tail + "/" if tail and rel == "" else tail)

    def _auth_header(self):
        if not self.username:
            return None
        token = base64.b64encode(
            ("%s:%s" % (self.username, self.password)).encode("utf-8")
        ).decode("ascii")
        return "Basic " + token

    def request(self, method, rel="", data=None, headers=None, allow=()):
        """发一次 WebDAV 请求，返回 (status, headers, body bytes)。"""
        url = self._url(rel)
        hdrs = {"User-Agent": USER_AGENT}
        auth = self._auth_header()
        if auth:
            hdrs["Authorization"] = auth
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            resp = self._opener.open(req, timeout=self.timeout)
            body = resp.read()
            return resp.status if hasattr(resp, "status") else resp.getcode(), resp.headers, body
        except urllib.error.HTTPError as e:
            if e.code in allow:
                try:
                    body = e.read()
                except Exception:
                    body = b""
                return e.code, e.headers, body
            raise WebDAVError("HTTP %d %s：%s" % (e.code, method, _T(_http_hint(e.code))), e.code)
        except urllib.error.URLError as e:
            raise WebDAVError(_T("连接失败：{e}").format(e=getattr(e, "reason", e)), None)
        except Exception as e:
            raise WebDAVError(_T("请求失败：{e}").format(e=e), None)

    # ---------- 目录 ----------
    def propfind(self, rel="", depth="1"):
        """列目录，返回 [{name, rel, is_dir, size, mtime}]。"""
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<D:propfind xmlns:D="DAV:"><D:prop>'
            "<D:resourcetype/><D:getcontentlength/><D:getlastmodified/><D:getetag/>"
            "</D:prop></D:propfind>"
        ).encode("utf-8")
        status, _headers, raw = self.request(
            "PROPFIND", rel, data=body,
            headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
            allow=(404, 405),
        )
        if status in (404, 405):
            return []
        try:
            root = ET.fromstring(raw.decode("utf-8", "replace"))
        except Exception as e:
            raise WebDAVError(_T("目录列表解析失败：{e}").format(e=e), status)
        out = []
        base_path = urllib.parse.urlparse(self._url("")).path
        for node in root.iter(_DAV + "response"):
            href = ""
            for h in node.iter(_DAV + "href"):
                href = (h.text or "").strip()
                break
            if not href:
                continue
            path = urllib.parse.unquote(urllib.parse.urlparse(href).path)
            norm = path.rstrip("/")
            if norm == base_path.rstrip("/"):
                continue  # 目录自身
            name = norm.split("/")[-1]
            is_dir = False
            size = 0
            mtime = 0.0
            for prop in node.iter(_DAV + "prop"):
                for st in prop.iter(_DAV + "resourcetype"):
                    for _c in list(st):
                        if _c.tag == _DAV + "collection":
                            is_dir = True
                for cl in prop.iter(_DAV + "getcontentlength"):
                    try:
                        size = int((cl.text or "0").strip())
                    except Exception:
                        size = 0
                for lm in prop.iter(_DAV + "getlastmodified"):
                    mtime = _parse_http_date((lm.text or "").strip())
            sub = norm[len(base_path.rstrip("/")):].strip("/") if path.startswith(base_path.rstrip("/")) else norm
            sub = "/".join(s for s in sub.split("/") if s)
            out.append({"name": name, "rel": sub, "is_dir": is_dir, "size": size, "mtime": mtime})
        return out

    def mkdir(self, rel):
        """创建单级目录；已存在（405）视为成功。"""
        status, _h, _b = self.request(
            "MKCOL", rel, data=b"", headers={"Content-Type": "text/plain"},
            allow=(405, 301, 409),
        )
        return status in (200, 201, 204, 301, 405)

    def mkdirs(self, rel):
        """逐级创建目录（逐段 MKCOL，已存在跳过）。"""
        segs = [s for s in str(rel).split("/") if s]
        cur = ""
        for s in segs:
            cur = (cur + "/" + s).strip("/")
            self.mkdir(cur)
        return True

    def exists(self, rel):
        status, _h, _b = self.request("PROPFIND", rel, data=b"",
                                      headers={"Depth": "0"},
                                      allow=(404,))
        return status != 404 and status < 400

    # ---------- 文件 ----------
    def get(self, rel):
        status, _h, body = self.request("GET", rel, allow=(404,))
        if status == 404:
            return None
        return body

    def put(self, rel, data, atomic=True):
        """写文件；优先 .part + MOVE 保证中途失败不留半截 JSON。"""
        if not atomic:
            self.request("PUT", rel, data=data,
                         headers={"Content-Type": "application/json; charset=utf-8"},
                         allow=(200, 201, 204))
            return True
        tmp = rel + ".part"
        self.request("PUT", tmp, data=data,
                     headers={"Content-Type": "application/json; charset=utf-8"},
                     allow=(200, 201, 204, 207))
        try:
            self.request("MOVE", tmp, data=b"", headers={
                "Destination": self._url(rel),
                "Overwrite": "T",
            }, allow=(200, 201, 204, 207))
            return True
        except WebDAVError:
            # 服务器不支持 MOVE（部分轻量实现）时退回普通 PUT
            try:
                self.request("DELETE", tmp, allow=(404, 200, 204, 207))
            except WebDAVError:
                pass
            return self.put(rel, data, atomic=False)

    def delete(self, rel):
        try:
            self.request("DELETE", rel, allow=(404, 200, 204, 207))
            return True
        except WebDAVError:
            return False


def _parse_http_date(s):
    if not s:
        return 0.0
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        return 0.0


def _http_hint(code):
    return {
        401: "账号或密码错误，或服务器未开启 Basic 鉴权",
        403: "账号无权限或服务器要求其他鉴权方式",
        404: "远端路径不存在",
        405: "服务器不支持该 WebDAV 方法",
        507: "远端空间不足",
        509: "远端配额已满",
    }.get(code, "服务器返回错误")


# ---------------------------------------------------------------- 同步引擎
class SyncEngine:
    """把本机书架 / 进度 / 书签与 WebDAV 远端合并。

    用法：
        eng = SyncEngine(storage, settings)
        report = eng.sync()          # 双向
    所有方法都是阻塞调用，调用方自行放到后台线程。
    """

    def __init__(self, storage, settings=None, log=None):
        self.storage = storage
        self.settings = settings if settings is not None else storage.settings()
        self._log = log or (lambda _s: None)
        self._key_cache = {}

    # ---------- 配置 ----------
    def configured(self):
        return bool(str(self.settings.get("sync_server") or "").strip())

    def client(self):
        return WebDAVClient(
            self.settings.get("sync_server") or "",
            self.settings.get("sync_user") or "",
            self.settings.get("sync_pass") or "",
            self.settings.get("sync_dir") or DEFAULT_REMOTE_DIR,
            verify_tls=bool(self.settings.get("sync_verify_tls", True)),
        )

    # ---------- 指纹 ----------
    def _sample_text(self, book):
        """取开头正文样本：优先解析缓存，其次源文件前 64KB（共享实现）。"""
        return sample_text_for_book(self.storage, book)

    def content_key(self, book):
        bid = book.get("id") or ""
        if bid in self._key_cache:
            return self._key_cache[bid]
        key = str(book.get("content_key") or "").strip()
        if key:
            self._key_cache[bid] = key
            return key
        key = content_key_of_book(self.storage, book)
        self._key_cache[bid] = key
        return key

    def legacy_key(self, book):
        """ck1 旧指纹：只用于和远端历史记录对上，不参与目录命名。"""
        try:
            sample = self._sample_text(book)
            if not sample:
                return ""
            return compute_legacy_key(
                book.get("title") or "",
                book.get("total_chars") or 0,
                sample,
            )
        except Exception:
            return ""

    # ---------- 书源文件（拉取远端独有书用） ----------
    @staticmethod
    def _source_exts():
        try:
            from .book_loader import SUPPORTED_EXTS
            return [e.lower() for e in SUPPORTED_EXTS]
        except Exception:
            return [".txt", ".epub", ".pdf", ".docx"]

    def _local_source(self, book):
        """返回 (可上传的源文件绝对路径, 扩展名)；无可用源时 (None, "")。"""
        for field in ("source_bak", "path"):
            p = book.get(field) or ""
            if p and os.path.isfile(p):
                ext = os.path.splitext(p)[1].lower()
                if ext in self._source_exts():
                    return p, ext
        return None, ""

    def _source_meta(self, book):
        path, ext = self._local_source(book)
        if not path:
            return None
        try:
            size = os.path.getsize(path)
        except Exception:
            size = 0
        return {"ext": ext, "size": int(size), "name": os.path.basename(path)}

    @staticmethod
    def _remote_source_rel(key, ext):
        return "%s/%s%s" % (SOURCES_DIR, key, ext)

    def _drop_remote_source(self, cli, key, rec):
        """删除事件生效后清掉远端书源文件：省网盘空间，也避免正文残留在远端。"""
        src = rec.get("source") if isinstance(rec.get("source"), dict) else {}
        cands = []
        ext = str(src.get("ext") or "").lower()
        if ext:
            cands.append(ext)
        for e in self._source_exts():
            if e not in cands:
                cands.append(e)
        removed = 0
        for e in cands:
            try:
                cli.delete(self._remote_source_rel(key, e))
            except WebDAVError:
                pass
        return removed

    def upload_sources(self, cli, local):
        """批量上传本地书源：先列一次远端目录，只补缺失的文件。返回上传本数。"""
        try:
            ents = cli.propfind(SOURCES_DIR, depth="1")
            have = set((e.get("name") or "").lower() for e in ents if not e.get("is_dir"))
        except WebDAVError:
            have = set()
        books = self.storage.all_books()
        uploaded = 0
        for key, (bid, _lrec) in local.items():
            book = books.get(bid) or {}
            path, ext = self._local_source(book)
            if not path:
                continue
            if ("%s%s" % (key, ext)).lower() in have:
                continue
            try:
                if os.path.getsize(path) > MAX_SOURCE_BYTES:
                    continue
                with open(path, "rb") as f:
                    data = f.read()
                cli.put(self._remote_source_rel(key, ext), data, atomic=False)
                uploaded += 1
            except Exception:
                continue
        return uploaded

    def _import_remote_book(self, cli, key, rec):
        """下载远端独有书的源文件、解析入书架，并套上远端进度/书签。

        返回 {"bid":..., "title":..., "new":True} 或 None（跳过原因写日志）。
        """
        from .book_loader import parse_book
        from .content_key import content_key_of_chapters

        title = rec.get("title") or key
        src_meta = rec.get("source") if isinstance(rec.get("source"), dict) else {}
        size = 0
        try:
            size = int(src_meta.get("size") or 0)
        except Exception:
            size = 0
        if size and size > MAX_SOURCE_BYTES:
            self._log(_T("远端「{t}」源文件 {s} 字节，超过上限，已跳过").format(
                t=title, s=size))
            return None

        cands = []
        ext = str(src_meta.get("ext") or "").lower()
        if ext:
            cands.append(ext)
        for e in self._source_exts():
            if e not in cands:
                cands.append(e)

        raw = None
        used_ext = ""
        for e in cands:
            try:
                body = cli.get(self._remote_source_rel(key, e))
            except WebDAVError:
                body = None
            if body:
                raw, used_ext = body, e
                break
        if not raw:
            self._log(_T("远端「{t}」没有源文件，暂不能下载").format(t=title))
            return None

        dst_dir = self.storage.sources_dir()
        try:
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, "%s%s" % (key, used_ext))
            tmp = dst + ".part"
            with open(tmp, "wb") as f:
                f.write(raw)
            os.replace(tmp, dst)
        except Exception as e:
            self._log(_T("保存远端源文件失败：{e}").format(e=e))
            return None

        try:
            content = parse_book(dst)
            # 书名以远端记录为准：本地文件名是指纹，不能参与标题推导
            if rec.get("title"):
                content.title = str(rec.get("title"))
            ckey = content_key_of_chapters(content.title, content.chapters)
        except Exception as e:
            self._log(_T("解析远端「{t}」失败：{e}").format(t=title, e=e))
            return None
        if ckey != key:
            # 两端对同一份正文算出不同指纹，说明正文/解码口径不同，不落地以免产生重复记录
            self._log(_T("远端「{t}」内容指纹不一致，已跳过").format(t=title))
            return None

        bid = self.storage.book_id(dst)
        if self.storage.get_book(bid):
            return None

        t_now = time.time()
        p_at = _num(rec.get("progress_at")) or _num(rec.get("updated_at")) or t_now
        progress = rec.get("progress") if isinstance(rec.get("progress"), dict) else \
            {"chapter_idx": 0, "char_offset": 0, "percent": 0.0}
        progress = dict(progress)
        progress["percent"] = normalize_percent(progress.get("percent"))
        bms = [x for x in (rec.get("bookmarks") or []) if isinstance(x, dict)]
        b_at = 0.0
        for bm in bms:
            b_at = max(b_at, _num(bm.get("created_at")))
        meta = {
            "id": bid,
            "content_key": ckey,
            "title": content.title,
            "author": content.author,
            "format": content.format,
            "path": dst,
            "source_bak": dst,
            "added_at": t_now,
            "last_read_at": p_at,
            "progress_at": p_at,
            "bookmarks_at": b_at,
            "total_chars": content.total_chars,
            "chapter_titles": [c.title for c in content.chapters],
            "progress": progress,
        }
        if bms:
            meta["bookmarks"] = bms
        try:
            self.storage.add_book(meta)
            self.storage.write_cache(bid, content)
        except Exception as e:
            self._log(_T("写入书架失败：{e}").format(e=e))
            return None
        return {"bid": bid, "title": content.title, "new": True}

    # ---------- 本地记录 ----------
    @staticmethod
    def _bookmarks(book):
        bms = book.get("bookmarks")
        return [x for x in bms if isinstance(x, dict)] if isinstance(bms, list) else []

    def _record(self, book, device_name):
        bms = self._bookmarks(book)
        p_at = progress_at_of(book)
        b_at = bookmarks_at_of(book)
        rec = {
            "schema": SCHEMA_VERSION,
            "content_key": self.content_key(book),
            "content_key_version": CK_VERSION,
            "legacy_key": self.legacy_key(book),
            "title": book.get("title") or "",
            "author": book.get("author") or "",
            "format": book.get("format") or "",
            "total_chars": int(book.get("total_chars") or 0),
            "chapter_count": len(book.get("chapter_titles") or []),
            "progress": book.get("progress") or {},
            # 进度与书签各带自己的版本时间：只改书签不会把进度"变新"，
            # 只改进度也不会让对端误判书签更新（iCloud 式按字段合并）。
            "progress_at": round(p_at, 3),
            "bookmarks": bms,
            "bookmarks_at": round(b_at, 3),
            # 兼容旧客户端：整体时间取两者较大值
            "updated_at": round(max(p_at, b_at), 3),
            "device": device_name,
            "synced_at": round(time.time(), 3),
        }
        src = self._source_meta(book)
        if src:
            rec["source"] = src
        return rec

    def _tombstone_record(self, key, entry, device_name):
        """删除墓碑记录：与普通记录同目录同 key，多一个 deleted 标记。"""
        t = _num((entry or {}).get("deleted_at")) or time.time()
        return {
            "schema": SCHEMA_VERSION,
            "content_key": key,
            "content_key_version": CK_VERSION,
            "legacy_key": "",
            "title": (entry or {}).get("title") or "",
            "author": "",
            "format": "",
            "total_chars": 0,
            "chapter_count": 0,
            "progress": {},
            "progress_at": 0,
            "bookmarks": [],
            "bookmarks_at": 0,
            "deleted": True,
            "deleted_at": round(t, 3),
            "updated_at": round(t, 3),
            "device": device_name,
            "synced_at": round(time.time(), 3),
        }

    def local_records(self):
        """返回 {content_key: (bid, record)}；顺带把新算出的 key 写回书元数据。"""
        device_id, device_name = ensure_device(self.storage, self.settings)
        out = {}
        dirty = False
        for bid, book in list(self.storage.all_books().items()):
            if not isinstance(book, dict):
                continue
            key = self.content_key(book)
            if book.get("content_key") != key:
                book["content_key"] = key
                dirty = True
            out[key] = (bid, self._record(book, device_name))
        if dirty:
            try:
                self.storage.save()
            except Exception:
                pass
        return out

    # ---------- 远端读写 ----------
    @staticmethod
    def _books_dir():
        return "v1/books"

    def _remote_book_path(self, key):
        return "%s/%s.json" % (self._books_dir(), key)

    def prepare_remote(self, cli):
        cli.mkdirs(self._books_dir())
        cli.mkdirs("v1/devices")
        cli.mkdirs(SOURCES_DIR)
        meta = {
            "schema": SCHEMA_VERSION,
            "app": "DDNovelReader",
            "updated_at": round(time.time(), 3),
        }
        try:
            cli.put("v1/meta.json", json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))
        except WebDAVError:
            pass

    def read_remote_books(self, cli):
        """返回 {content_key: record}。"""
        out = {}
        for ent in cli.propfind(self._books_dir(), depth="1"):
            name = ent.get("name") or ""
            if ent.get("is_dir") or not name.lower().endswith(".json"):
                continue
            key = name[:-5]
            try:
                raw = cli.get(ent.get("rel") or self._remote_book_path(key))
            except WebDAVError as e:
                self._log(_T("读取远端 {name} 失败：{e}").format(name=key, e=e))
                continue
            if not raw:
                continue
            try:
                rec = json.loads(raw.decode("utf-8"))
            except Exception:
                self._log(_T("远端 {name} 不是合法 JSON，已跳过").format(name=key))
                continue
            if isinstance(rec, dict) and rec.get("content_key"):
                out[str(rec["content_key"])] = rec
        return out

    # ---------- 合并 ----------
    @staticmethod
    def _merge_bookmarks(local_bms, remote_bms):
        """按 id 取并集；同 id 取较新的 created_at 内容。返回 (合并列表, 是否变化)。"""
        merged = {}
        changed = False
        for src in (local_bms or []):
            mid = str(src.get("id") or "")
            if not mid:
                continue
            merged[mid] = src
        for rbm in (remote_bms or []):
            mid = str(rbm.get("id") or "")
            if not mid:
                continue
            cur = merged.get(mid)
            if cur is None:
                merged[mid] = rbm
                changed = True
                continue
            try:
                if float(rbm.get("created_at") or 0) > float(cur.get("created_at") or 0):
                    merged[mid] = rbm
                    changed = True
            except Exception:
                pass
        order = sorted(merged.values(), key=lambda x: float(x.get("created_at") or 0))
        return order, changed

    def apply_remote(self, bid, rec):
        """把远端记录合到本地书（进度/书签各按自己的时间新者胜）。

        返回 {"progress": bool, "bookmarks": bool, "progress_obj": dict|None}。
        """
        book = self.storage.all_books().get(bid)
        if not isinstance(book, dict):
            return {"progress": False, "bookmarks": False, "progress_obj": None}
        res = {"progress": False, "bookmarks": False, "progress_obj": None}
        remote_progress = rec.get("progress") if isinstance(rec.get("progress"), dict) else {}
        # schema 1 老记录没有 progress_at，用整体 updated_at 兜底
        r_pat = _num(rec.get("progress_at"))
        if r_pat <= 0:
            r_pat = _num(rec.get("updated_at"))
        l_pat = progress_at_of(book)
        if remote_progress and r_pat > l_pat + TIME_TOL:
            prog = dict(remote_progress)
            # 安卓内部 percent 是 0~1，PC 是 0~100（入站统一折成 PC 口径）
            prog["percent"] = normalize_percent(prog.get("percent"))
            book["progress"] = prog
            book["progress_at"] = r_pat
            # last_read_at 只影响书架排序，这里跟着往前走，避免列表把这本书排到旧位置
            book["last_read_at"] = max(_num(book.get("last_read_at")), r_pat)
            res["progress"] = True
            res["progress_obj"] = book["progress"]

        bms, bm_changed = self._merge_bookmarks(self._bookmarks(book), rec.get("bookmarks") or [])
        if bm_changed or (bms and not self._bookmarks(book)):
            book["bookmarks"] = bms
            book["bookmarks_at"] = max(bookmarks_at_of(book), _num(rec.get("bookmarks_at")) or _num(rec.get("updated_at")))
            res["bookmarks"] = True

        # 只在本地没有 key 时补写；已有 key 不覆盖，防止一条异常记录把 fingerprint 改掉
        if not str(book.get("content_key") or "").strip() and rec.get("content_key"):
            book["content_key"] = rec.get("content_key")
        return res

    # ---------- 主流程 ----------
    def sync(self):
        """双向同步，返回 report dict。

        合并规则（与安卓端 sync_engine.dart 完全一致）：
        - 进度：比 progress_at，新者胜（不看章节先后，回看也算新）
        - 书签：按 id 并集，同 id 取 created_at 较新的一份
        - 删除：墓碑记录（deleted/deleted_at），对端删除时间更新则本地也删
        """
        report = {"ok": False, "pushed": 0, "pulled": 0, "skipped": 0,
                  "imported": 0, "removed": 0, "deleted": 0,
                  "remote_only": [], "lines": [], "error": "",
                  "progress_applied": {}, "removed_bids": []}
        if not self.configured():
            report["error"] = _T("未配置 WebDAV 服务器地址")
            return report
        if not _SYNC_LOCK.acquire(blocking=False):
            report["error"] = _T("已有同步任务在进行")
            return report
        try:
            cli = self.client()
            self.prepare_remote(cli)
            device_id, device_name = ensure_device(self.storage, self.settings)
            local = self.local_records()
            remote = self.read_remote_books(cli)
            local_del = dict(self.storage.deletions() or {})

            local_updates = 0
            # ---- 1) 远端 → 本地 ----
            for key, rec in remote.items():
                if rec.get("deleted"):
                    bid_item = local.get(key)
                    tomb_t = _num(rec.get("deleted_at")) or _num(rec.get("updated_at"))
                    if bid_item is not None:
                        bid, _lrec = bid_item
                        book = self.storage.all_books().get(bid) or {}
                        # 本地这本书在删除之后又被读过/加了书签/重新导入 = 用户又用起来了，复活
                        # （时间相当时偏向"保留书籍"，删除是不可逆操作，不能因为 0.5 秒误差把书删掉）
                        if book_activity_of(book) >= tomb_t - TIME_TOL:
                            report["skipped"] += 1
                            continue
                        self.storage.remove_book(bid, deleted_at=tomb_t)
                        local.pop(key, None)
                        report["removed"] += 1
                        report["removed_bids"].append(bid)
                        report["lines"].append(_T("✕ 已删除 {t}（对端删除）").format(
                            t=_lrec.get("title") or rec.get("title") or key))
                        local_updates += 1
                    # 远端已删且本机也没有：记下墓碑，避免以后又把这本书拉回来
                    self.storage.mark_deleted(key, rec.get("title") or "",
                                              tomb_t, save=False)
                    local_del[key] = {"deleted_at": tomb_t, "title": rec.get("title") or ""}
                    self._drop_remote_source(cli, key, rec)
                    continue

                # 本机删过、且删除比远端记录更新 → 让本机的删除生效（推送墓碑）
                dent = local_del.get(key)
                if dent and _num(dent.get("deleted_at")) > _num(rec.get("updated_at")) + TIME_TOL:
                    continue

                item = local.get(key)
                if item is None:
                    # 远端独有：有源文件就下载落成书架条目，否则只报告
                    got = self._import_remote_book(cli, key, rec)
                    if got:
                        report["imported"] += 1
                        report["lines"].append(_T("↓ 新增 {t}").format(t=got.get("title") or key))
                    else:
                        report["remote_only"].append(rec.get("title") or key)
                    continue

                bid, lrec = item
                l_pat = _num(lrec.get("progress_at"))
                r_pat = _num(rec.get("progress_at")) or _num(rec.get("updated_at"))
                l_bat = _num(lrec.get("bookmarks_at"))
                r_bat = _num(rec.get("bookmarks_at")) or _num(rec.get("updated_at"))
                if r_pat > l_pat + TIME_TOL or r_bat > l_bat + TIME_TOL:
                    res = self.apply_remote(bid, rec)
                    if res.get("progress"):
                        report["pulled"] += 1
                        report["progress_applied"][bid] = {
                            "title": lrec.get("title") or key,
                            "progress": res.get("progress_obj") or {},
                            "progress_at": r_pat,
                        }
                        local_updates += 1
                        report["lines"].append(_T("↓ {t}（进度 → 第 {c} 章）").format(
                            t=lrec.get("title") or key,
                            c=int(_num((res.get("progress_obj") or {}).get("chapter_idx")))))
                    elif res.get("bookmarks"):
                        report["pulled"] += 1
                        local_updates += 1
                        report["lines"].append(_T("↓ {t}（书签）").format(t=lrec.get("title") or key))
                    else:
                        report["skipped"] += 1
                else:
                    report["skipped"] += 1

            if local_updates:
                try:
                    self.storage.save()
                except Exception:
                    pass
                # 拉取/删除后再算一次本地状态，供下面 push 判定
                local = self.local_records()
                local_del = dict(self.storage.deletions() or {})

            # ---- 2) 本地 → 远端（按字段判断，任一字段更新就推整条） ----
            for key, (bid, lrec) in local.items():
                rrec = remote.get(key)
                if rrec is not None and not rrec.get("deleted"):
                    l_pat = _num(lrec.get("progress_at"))
                    r_pat = _num(rrec.get("progress_at")) or _num(rrec.get("updated_at"))
                    l_bat = _num(lrec.get("bookmarks_at"))
                    r_bat = _num(rrec.get("bookmarks_at")) or _num(rrec.get("updated_at"))
                    if l_pat <= r_pat + TIME_TOL and l_bat <= r_bat + TIME_TOL:
                        continue
                payload = json.dumps(lrec, ensure_ascii=False, indent=1).encode("utf-8")
                cli.put(self._remote_book_path(key), payload)
                report["pushed"] += 1
                report["lines"].append("↑ %s" % (lrec.get("title") or key))

            # ---- 3) 本地删除 → 远端墓碑 ----
            for key, dent in list(local_del.items()):
                if key in local:
                    # 书还在本地（刚重新导入）= 撤销墓碑，不再推删除
                    self.storage.clear_deletion(key, save=False)
                    continue
                d_t = _num(dent.get("deleted_at"))
                if not d_t:
                    continue
                if time.time() - d_t > TOMBSTONE_TTL:
                    self.storage.clear_deletion(key, save=False)
                    continue
                rrec = remote.get(key)
                if rrec is not None:
                    if rrec.get("deleted"):
                        # 远端已经是墓碑：谁的删除更新就保留谁的时间
                        if d_t <= _num(rrec.get("deleted_at")) + TIME_TOL:
                            continue
                    elif _num(rrec.get("updated_at")) > d_t + TIME_TOL:
                        # 远端在本机删除之后还更新过（对端仍在读这本书）：以对端为准，不推删除
                        continue
                payload = json.dumps(self._tombstone_record(key, dent, device_name),
                                     ensure_ascii=False, indent=1).encode("utf-8")
                cli.put(self._remote_book_path(key), payload)
                report["deleted"] += 1
                report["lines"].append(_T("↑ 删除事件 {t}").format(t=dent.get("title") or key))
                self._drop_remote_source(cli, key, rrec or {})

            try:
                self.storage.save()
            except Exception:
                pass

            # 书源文件：另一台机器要能把本机独有的书拉过去，记录里得有源文件
            try:
                src_up = self.upload_sources(cli, local)
                if src_up:
                    report["lines"].append(_T("↑ 书源文件 {n} 个").format(n=src_up))
            except Exception:
                pass

            # 设备登记
            try:
                dev = {
                    "schema": SCHEMA_VERSION,
                    "device_id": device_id,
                    "device_name": device_name,
                    "last_sync": round(time.time(), 3),
                    "book_count": len(local),
                    "platform": _platform_tag(),
                }
                cli.put("v1/devices/%s.json" % device_id,
                        json.dumps(dev, ensure_ascii=False, indent=1).encode("utf-8"))
            except WebDAVError:
                pass

            now = time.time()
            self.settings["sync_last_time"] = now
            try:
                self.storage.set_setting("sync_last_time", now)
            except Exception:
                pass
            report["ok"] = True
            return report
        except WebDAVError as e:
            report["error"] = str(e)
            return report
        except Exception as e:
            report["error"] = _T("同步失败：{e}").format(e=e)
            return report
        finally:
            _SYNC_LOCK.release()

    def test_connection(self):
        """只做连接与权限检测。返回 (ok, message)。"""
        try:
            cli = self.client()
            self.prepare_remote(cli)
            entries = cli.propfind(self._books_dir(), depth="1")
            return True, _T("连接成功，远端已有 {n} 本书记录").format(n=len(
                [x for x in entries if not x.get("is_dir") and (x.get("name") or "").lower().endswith(".json")]))
        except WebDAVError as e:
            return False, str(e)
        except Exception as e:
            return False, _T("连接失败：{e}").format(e=e)


def _platform_tag():
    try:
        import platform
        return platform.system() or "unknown"
    except Exception:
        return "unknown"
