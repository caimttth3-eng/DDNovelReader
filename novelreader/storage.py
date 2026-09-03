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


def audio_cache_dirs(root, bid):
    """返回某本书在 `root/<book_id>/<语音>/<语速>` 下的全部缓存目录列表。

    每本书一个顶层目录，内部再按语音/语速分子目录（参数指纹隔离）。
    """
    out = []
    bd = os.path.join(root, str(bid))
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
}


class Storage:
    def __init__(self, path=None):
        self.path = path or os.path.join(data_dir(), "library.json")
        self.data = {"books": {}, "settings": dict(DEFAULT_SETTINGS)}
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
        s = dict(DEFAULT_SETTINGS)
        s.update(self.data["settings"])
        self.data["settings"] = s

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

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
        self.save()
        return meta["id"]

    def get_book(self, bid):
        return self.data["books"].get(bid)

    def all_books(self):
        return self.data["books"]

    def update_progress(self, bid, progress):
        b = self.data["books"].get(bid)
        if not b:
            return
        b["progress"] = progress
        b["last_read_at"] = time.time()
        self.save()

    def remove_book(self, bid):
        self.data["books"].pop(bid, None)
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

    def set_book_cache_size(self, bid, size):
        """持久化书籍总缓存大小。"""
        b = self.data["books"].get(bid)
        if not b:
            return
        b["cache_size"] = int(size)
        self.save()

    # ---------- 解析缓存 ----------
    def cache_path(self, bid):
        custom = self.data.get("settings", {}).get("cache_dir", "")
        root = resolve_cache_dir(custom)
        return os.path.join(root, f"{bid}.json")

    def read_cache(self, bid):
        from .book_loader import CONTENT_CACHE_VERSION

        p = self.cache_path(bid)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("v") != CONTENT_CACHE_VERSION:
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
    return f"{str(bid)}/{voice}/{int(rate)}"


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
