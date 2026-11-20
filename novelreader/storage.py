# -*- coding: utf-8 -*-
"""数据层：书架、阅读进度自动保存、全局设置、解析缓存。"""
import hashlib
import json
import os
import threading
import time


def data_dir():
    base = os.environ.get("DOUBAO_NOVEL_DATA")
    if base:
        os.makedirs(base, exist_ok=True)
        return base
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(appdata, "DDNovelReader")
    os.makedirs(d, exist_ok=True)
    return d


def cache_dir():
    d = os.path.join(data_dir(), "cache")
    os.makedirs(d, exist_ok=True)
    return d


def resolve_cache_dir(custom):
    """返回实际使用的正文解析缓存根目录。

    custom 非空时用自定义目录；为空则回退到默认 `cache_dir()`。
    """
    if custom and str(custom).strip():
        d = str(custom).strip().rstrip("\\/")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = cache_dir()
        return d
    return cache_dir()


def art_dir(custom=None):
    """插图缓存目录：`<正文解析缓存根>/art/`。

    插图是派生数据（可从源文件重新解压），随正文缓存一起转移/清除，
    因此放在缓存根下的 art 子目录，不单独占用数据目录。
    """
    return os.path.join(resolve_cache_dir(custom), "art")


def tts_cache_dir():
    """整本语音缓存默认根目录：`<数据目录>/tts_cache/<语音>/<语速>/<book_id>/`。"""
    d = os.path.join(data_dir(), "tts_cache")
    os.makedirs(d, exist_ok=True)
    return d


def resolve_tts_cache_dir(custom):
    """返回实际使用的整本语音缓存根目录。

    custom 非空时用自定义目录（便于把大体积音频缓存移出 C 盘）；
    为空则回退到默认 `tts_cache_dir()`。
    """
    if custom and str(custom).strip():
        d = str(custom).strip().rstrip("\\/")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = tts_cache_dir()
        return d
    return tts_cache_dir()


# ---------------------------------------------------------------- 局域网同步数据目录
# v2.2.9：局域网同步数据从 <数据目录>/lan 迁到「正文缓存根/lan」，
# 目的是让所有"缓存类"文件都落在一个可自定义位置的目录里，用户删缓存时能一次清干净。
# 注意：library.json / stats.json / custom.ico 等仍留在 <数据目录>，它们是用户数据，不是缓存。

def legacy_lan_dir():
    """旧位置（v2.2.9 之前/首发版本）：<数据目录>/lan。"""
    return os.path.join(data_dir(), "lan")


def lan_dir(custom_cache=None):
    """局域网同步数据目录：`<正文缓存根>/lan`（跟着 cache_dir 自定义一起走）。"""
    return os.path.join(resolve_cache_dir(custom_cache), "lan")


def migrate_legacy_lan_dir(custom_cache=None):
    """把旧位置 <数据目录>/lan 迁到 <缓存根>/lan。

    幂等：新位置已存在的同名文件保留（视为更新），旧位置迁完即删。
    返回 {"moved": bool, "files": n, "from": ..., "to": ..., "reason": ...}
    """
    old = legacy_lan_dir()
    new = lan_dir(custom_cache)
    same = os.path.normcase(os.path.abspath(old)) == os.path.normcase(os.path.abspath(new))
    if same:
        return {"moved": False, "files": 0, "from": old, "to": new, "reason": "same-dir"}
    if not os.path.isdir(old):
        return {"moved": False, "files": 0, "from": old, "to": new, "reason": "no-legacy"}
    moved = 0
    failed = 0
    try:
        os.makedirs(new, exist_ok=True)
        for base, _dirs, files in os.walk(old):
            rel = os.path.relpath(base, old)
            target_base = new if rel == "." else os.path.join(new, rel)
            try:
                os.makedirs(target_base, exist_ok=True)
            except OSError:
                failed += len(files)
                continue
            for name in files:
                src = os.path.join(base, name)
                dst = os.path.join(target_base, name)
                if os.path.exists(dst):
                    continue          # 新位置已有：保留更新的那份
                try:
                    import shutil
                    shutil.move(src, dst)
                    moved += 1
                except Exception:
                    failed += 1
        if not failed:
            import shutil
            shutil.rmtree(old, ignore_errors=True)
    except Exception as e:                      # noqa: BLE001
        return {"moved": bool(moved), "files": moved, "from": old, "to": new, "reason": str(e)}
    return {"moved": True, "files": moved, "from": old, "to": new,
            "reason": "" if not failed else "%d 个文件迁移失败" % failed}


def dir_size(path):
    """递归统计目录内文件总字节数；目录不存在返回 0。"""
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
    except Exception:
        pass
    return total


def audio_cache_dirs(root, cache_key):
    """返回某本书在 `root/<content_key>/<语音>/<语速百分比>` 下的全部缓存目录列表。

    一级目录是 content_key（跨端/跨机器稳定），内部再按语音/语速分子目录；
    调用方传 shelf 的 book_id 时须先映射成 content_key（SpeechController.cache_key_for）。
    """
    out = []
    bd = os.path.join(root, str(cache_key))
    try:
        if not os.path.isdir(bd):
            return out
        for voice in os.listdir(bd):
            vd = os.path.join(bd, voice)
            if not os.path.isdir(vd):
                continue
            for rate in os.listdir(vd):
                rd = os.path.join(vd, rate)
                if os.path.isdir(rd):
                    out.append(rd)
    except Exception:
        pass
    return out


DEFAULT_SETTINGS = {
    "font_family": "微软雅黑",
    "font_size": 17,
    "line_spacing": 1.5,
    "theme": "护眼",
    "auto_open_last": True,
    "tts_rate": 200,
    "tts_voice": "",
    "window_geometry": "",
    "paragraph_mode": 1,       # 压缩空行：1 不压缩 / 2 合并为一行 / 3 清理所有行
    "first_line_indent": True, # 段落首行缩进二个字
    "tts_sentence_gap": 0.10,  # 句子停顿间隔（秒）
    "tts_cache_dir": "",       # 自定义整本语音缓存根目录（空=默认 %APPDATA%\\DDNovelReader\\tts_cache）
    "tts_cache_workers": 6,   # 整本缓存并发下载线程数（3~12，超过 6 易被微软限流）
    "skip_update_version": "", # 用户选择「跳过此版本」的版本号（更新提示不再提醒该版本）
    # ---- WebDAV 数据同步（书架/进度/书签，不含正文与音频缓存）----
    "sync_server": "",         # 服务器地址，如 https://dav.jianguoyun.com/dav/
    "sync_user": "",           # WebDAV 账号
    "sync_pass": "",           # WebDAV 密码（仅保存在本机 library.json）
    "sync_dir": "DDNovelReader",  # 远端目录
    "sync_device_id": "",      # 本机设备 ID（首次同步自动生成）
    "sync_device_name": "",    # 设备显示名（默认取主机名）
    "sync_auto": False,        # 启动时自动同步 + 每 10 分钟一次
    "sync_verify_tls": True,   # 是否校验证书
    "sync_last_time": 0,       # 上次成功同步时间
}


class Storage:
    def __init__(self, path=None):
        self.path = path or os.path.join(data_dir(), "library.json")
        self.data = {"books": {}, "settings": dict(DEFAULT_SETTINGS)}
        # 写锁：主线程（阅读落盘）与后台线程（缓存大小维护）都会触发全量保存，
        # 两者共用同一个 "<path>.tmp"，无锁时并发 os.replace 会互相打断并走 0.12s 重试。
        self._save_lock = threading.RLock()
        self.load()

    # ---------- 基础读写 ----------
    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.data = loaded
            except Exception:
                self.data = {"books": {}, "settings": dict(DEFAULT_SETTINGS)}
        self.data.setdefault("books", {})
        self.data.setdefault("settings", {})
        # 删除墓碑：{content_key: {"deleted_at": 秒, "title": 书名}}
        # 本机删掉的书不再留条目，但必须留一条"删除事件"给同步，
        # 否则对端只看到"本机没有这本书"，会把书又推回来（iCloud 同款墓碑逻辑）。
        if not isinstance(self.data.get("deletions"), dict):
            self.data["deletions"] = {}
        s = dict(DEFAULT_SETTINGS)
        s.update(self.data["settings"])
        self.data["settings"] = s

    def save(self):
        with self._save_lock:
            tmp = self.path + ".tmp"
            for _attempt in range(4):
                try:
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(self.data, f, ensure_ascii=False, indent=1)
                    os.replace(tmp, self.path)
                    return
                except (PermissionError, OSError):
                    if _attempt == 3:
                        raise
                    time.sleep(0.12)

    # ---------- 设置 ----------
    def get_setting(self, key, default=None):
        return self.data["settings"].get(key, default)

    def set_setting(self, key, value):
        self.data["settings"][key] = value
        self.save()

    def settings(self):
        return self.data["settings"]

    # ---------- 书架 ----------
    @staticmethod
    def book_id(path):
        key = os.path.normcase(os.path.abspath(path))
        return hashlib.md5(key.encode("utf-8")).hexdigest()[:16]

    def add_book(self, meta):
        self.data["books"][meta["id"]] = meta
        # 重新导入同一本书 = 撤销它之前的删除墓碑，否则同步会把这本书再删掉一次
        ck = str(meta.get("content_key") or "").strip()
        if ck:
            self.clear_deletion(ck, save=False)
        self.save()
        return meta["id"]

    def get_book(self, bid):
        return self.data["books"].get(bid)

    def all_books(self):
        return self.data["books"]

    def update_progress(self, bid, progress, save=True):
        b = self.data["books"].get(bid)
        if not b:
            return
        b["progress"] = progress
        now = time.time()
        b["last_read_at"] = now
        # 进度自己的时间戳：同步合并只认它，书架排序仍看 last_read_at。
        # 分开的原因：仅仅"打开/切换书籍"也会刷新 last_read_at（touch），
        # 若同步按 last_read_at 判断新者胜，PC 现实上没改进度却会压掉对端的新进度。
        b["progress_at"] = now
        if save:
            self.save()

    def save_reading_state(self, bid, progress, last_book):
        """阅读位置 + 上次书 一次性落盘（原来分两次调用 = 两次全量 JSON 重写）。"""
        b = self.data["books"].get(bid)
        if b:
            b["progress"] = progress
            now = time.time()
            b["last_read_at"] = now
            b["progress_at"] = now
        if last_book:
            self.data["settings"]["last_book"] = last_book
        self.save()

    def remove_book(self, bid, deleted_at=None):
        """从书架删除一本书，并留下删除墓碑（供 WebDAV 同步传给其它设备）。

        deleted_at 不传时取当前时间；同步应用远端墓碑时要传远端时间，
        否则本机时间会把对端的删除事件"变新"，导致反复拉锯。
        """
        b = self.data["books"].pop(bid, None)
        if isinstance(b, dict):
            ck = str(b.get("content_key") or "").strip()
            if ck:
                self.mark_deleted(ck, b.get("title") or "", deleted_at)
        self.save()

    # ---------- 删除墓碑 ----------
    def deletions(self):
        return self.data.setdefault("deletions", {})

    def mark_deleted(self, content_key, title="", deleted_at=None, save=True):
        ck = str(content_key or "").strip()
        if not ck:
            return None
        t = float(deleted_at) if deleted_at else time.time()
        old = self.deletions().get(ck) or {}
        try:
            old_t = float(old.get("deleted_at") or 0)
        except Exception:
            old_t = 0.0
        # 只保留最新一次删除事件
        if t >= old_t:
            self.deletions()[ck] = {"deleted_at": round(t, 3), "title": title or old.get("title") or ""}
        if save:
            self.save()
        return ck

    def clear_deletion(self, content_key, save=True):
        ck = str(content_key or "").strip()
        if ck and ck in self.deletions():
            self.deletions().pop(ck, None)
            if save:
                self.save()

    def touch(self, bid):
        """更新最近阅读时间（不改变进度，避免频繁全量保存）。"""
        b = self.data["books"].get(bid)
        if not b:
            return
        b["last_read_at"] = time.time()
        self.save()

    # ---------- 书签（划线高亮 + 备注笔记） ----------
    def get_bookmarks(self, bid):
        """返回某本书的书签列表（按创建时间正序）。"""
        b = self.data["books"].get(bid)
        if not b:
            return []
        bms = b.get("bookmarks")
        if not isinstance(bms, list):
            return []
        return [x for x in bms if isinstance(x, dict)]

    def add_bookmark(self, bid, bm):
        """新增书签；bm 为 dict（chapter_idx/offset/offset_end/text/note）。返回 id。"""
        b = self.data["books"].get(bid)
        if not b:
            return None
        bms = b.setdefault("bookmarks", [])
        bm["id"] = str(time.time_ns())
        bm.setdefault("created_at", time.time())
        bms.append(bm)
        self.save()
        return bm["id"]

    def remove_bookmark(self, bid, bm_id):
        """按 id 删除书签。"""
        b = self.data["books"].get(bid)
        if not b:
            return
        bms = b.get("bookmarks")
        if not isinstance(bms, list):
            return
        b["bookmarks"] = [x for x in bms if x.get("id") != bm_id]
        self.save()

    # ---------- 缓存大小持久化（避免每次刷新书架遍历文件系统） ----------
    def book_cache_size(self, bid):
        """读取持久化的书籍总缓存大小（字节），不扫描文件系统。"""
        b = self.data["books"].get(bid)
        if not b:
            return 0
        try:
            return int(b.get("cache_size", 0) or 0)
        except Exception:
            return 0

    def has_cache_size(self, bid):
        """是否已有持久化的缓存大小（缺失表示老数据，需一次性校准）。"""
        b = self.data["books"].get(bid)
        return bool(b) and "cache_size" in b

    def set_book_cache_size(self, bid, size, save=True):
        """持久化书籍总缓存大小（save=False 时只写内存，由调用方批量落盘一次）。"""
        b = self.data["books"].get(bid)
        if not b:
            return
        b["cache_size"] = int(size)
        if save:
            self.save()

    # ---------- 解析缓存 ----------
    def cache_path(self, bid):
        custom = self.data.get("settings", {}).get("cache_dir", "")
        root = resolve_cache_dir(custom)
        return os.path.join(root, f"{bid}.json")

    def read_cache(self, bid, any_version=False):
        from .book_loader import CONTENT_CACHE_VERSION

        p = self.cache_path(bid)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not any_version and data.get("v") != CONTENT_CACHE_VERSION:
                return None  # 缓存格式过期，触发重新解析
            return data
        except Exception:
            return None

    def write_cache(self, bid, content):
        from .book_loader import BookContent

        try:
            p = self.cache_path(bid)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(content.to_dict(), f, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception:
            pass

    # ---------- 书籍源文件备份（原文件被删除后仍可重解析/复制原文件） ----------
    def sources_dir(self):
        custom = self.data.get("settings", {}).get("cache_dir", "")
        root = resolve_cache_dir(custom)
        return os.path.join(root, "sources")

    def backup_source_path(self, bid, ext):
        ext = (ext or ".txt").lower()
        return os.path.join(self.sources_dir(), "%s%s" % (bid, ext))

    def backup_source(self, bid, src_path):
        """把源文件复制到 <cache_dir>/sources/<bid><ext>，返回备份路径；失败返回 None。"""
        import shutil
        try:
            ext = os.path.splitext(src_path)[1]
            dst = self.backup_source_path(bid, ext)
            if os.path.abspath(src_path) == os.path.abspath(dst):
                return dst
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src_path, dst)
            return dst
        except Exception:
            return None

    def remove_source_backup(self, bid):
        """删除该书在 sources/ 下的全部备份文件（<bid>.*）。"""
        import glob
        try:
            d = self.sources_dir()
            if os.path.isdir(d):
                for f in glob.glob(os.path.join(d, bid + ".*")):
                    try:
                        os.remove(f)
                    except Exception:
                        pass
        except Exception:
            pass


# ============================================================
# 音频缓存大小索引（避免反复遍历数万细碎小文件）
# ============================================================
# 索引文件存放在缓存根目录 <root>/.tts_sizes.json，结构：
#   {"v": 1, "entries": {"<bid>/<语音>/<语速>": {"size": 字节, "t": 时间戳}}}
# 缓存进行中增量更新；三处（书架/关于页/整本缓存窗口）统一读索引，不扫盘。
TTS_SIZE_INDEX = ".tts_sizes.json"
_tts_size_lock = threading.Lock()


def tts_size_index_path(root):
    return os.path.join(root, TTS_SIZE_INDEX)


def load_tts_size_index(root):
    """读索引 entries dict；损坏/缺失返回 {}。"""
    try:
        p = tts_size_index_path(root)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("v") == 1:
                entries = data.get("entries")
                if isinstance(entries, dict):
                    return entries
    except Exception:
        pass
    return {}


def save_tts_size_index(root, entries):
    """原子写索引文件。"""
    try:
        os.makedirs(root, exist_ok=True)
        p = tts_size_index_path(root)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"v": 1, "entries": entries}, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


def tts_size_key(bid, voice, rate):
    """大小索引键，与缓存目录层级一一对应：`<content_key>/<语音>/<语速百分比>`。

    rate 既可能是 PC 整数标度（200），也可能是目录名本身（"0"/"50"/"-50"），
    两种入参都归一到同一目录名，避免索引与真实目录错位。
    """
    try:
        if isinstance(rate, (int, float)) and not isinstance(rate, bool):
            from .content_key import rate_dir_key
            r = rate_dir_key(int(rate))
        else:
            r = str(rate)
    except Exception:
        r = str(rate)
    return f"{str(bid)}/{voice}/{r}"


def migrate_old_tts_layout(root):
    """一次性迁移旧结构 `root/<语音>/<语速>/<book_id>/` → 新结构 `root/<book_id>/<语音>/<语速>/`。

    在后台线程调用。返回迁移了多少个目录。
    """
    moved = 0
    try:
        if not os.path.isdir(root):
            return 0
        for voice in os.listdir(root):
            vd = os.path.join(root, voice)
            if not os.path.isdir(vd) or voice.startswith("."):
                continue
            for rate in os.listdir(vd):
                rd = os.path.join(vd, rate)
                if not os.path.isdir(rd) or rate.startswith("."):
                    continue
                for bid in os.listdir(rd):
                    src = os.path.join(rd, bid)
                    if not os.path.isdir(src):
                        continue
                    dst = os.path.join(root, bid, voice, rate)
                    try:
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        # 目标已存在时仅补齐缺失文件（合并，不覆盖同名）
                        if os.path.isdir(dst):
                            for f in os.listdir(src):
                                s = os.path.join(src, f)
                                d = os.path.join(dst, f)
                                if os.path.isfile(s) and not os.path.exists(d):
                                    import shutil
                                    shutil.move(s, d)
                            # 源目录清空后删除
                            if not os.listdir(src):
                                os.rmdir(src)
                        else:
                            os.renames(src, dst)
                        moved += 1
                    except Exception:
                        pass
                    # 逐级清理空目录
                    try:
                        if os.path.isdir(rd) and not os.listdir(rd):
                            os.rmdir(rd)
                    except Exception:
                        pass
                try:
                    if os.path.isdir(vd) and not os.listdir(vd):
                        os.rmdir(vd)
                except Exception:
                    pass
            try:
                if os.path.isdir(root) and os.path.isdir(os.path.join(root, voice)) and not os.listdir(os.path.join(root, voice)):
                    os.rmdir(os.path.join(root, voice))
            except Exception:
                pass
    except Exception:
        pass
    return moved


def calibrate_tts_size(root, bid=None):
    """对指定书（或全部）目录重新统计音频缓存大小并写入索引。

    全量校准：清空旧条目后按 `bid/语音/语速` 细粒度逐个目录 dir_size 重建，
    之后增量 bump 在此基数上累加，不会双重计数。
    返回 {bid: size}。只在索引缺失/迁移后一次性后台调用。
    """
    try:
        with _tts_size_lock:
            entries = load_tts_size_index(root)
            # 只校准指定书时不能清空整个索引：其它书的条目必须原样保留
            if bid is not None:
                prefix = str(bid) + "/"
                for k in [k for k in list(entries) if k.startswith(prefix)]:
                    entries.pop(k, None)
            else:
                entries.clear()
            result = {}
            if bid is not None:
                bids = [str(bid)]
            else:
                bids = []
                try:
                    if os.path.isdir(root):
                        bids = [b for b in os.listdir(root)
                                if os.path.isdir(os.path.join(root, b)) and not b.startswith(".")]
                except Exception:
                    pass
            for b in bids:
                total = 0
                bd = os.path.join(root, str(b))
                try:
                    if os.path.isdir(bd):
                        for voice in os.listdir(bd):
                            vd = os.path.join(bd, voice)
                            if not os.path.isdir(vd) or voice.startswith("."):
                                continue
                            for rate in os.listdir(vd):
                                rd = os.path.join(vd, rate)
                                if os.path.isdir(rd):
                                    sz = dir_size(rd)
                                    entries[tts_size_key(b, voice, rate)] = {
                                        "size": sz, "t": time.time()
                                    }
                                    total += sz
                except Exception:
                    pass
                result[b] = total
            save_tts_size_index(root, entries)
            return result
    except Exception:
        return {}
