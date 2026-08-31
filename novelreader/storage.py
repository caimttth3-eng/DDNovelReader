# -*- coding: utf-8 -*-
"""数据层：书架、阅读进度自动保存、全局设置、解析缓存。"""
import hashlib
import json
import os
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
    """返回某本书在 `root/<语音>/<语速>/<book_id>` 下的全部缓存目录列表。"""
    out = []
    try:
        for voice in os.listdir(root):
            vd = os.path.join(root, voice)
            if not os.path.isdir(vd):
                continue
            for rate in os.listdir(vd):
                rd = os.path.join(vd, rate)
                if not os.path.isdir(rd):
                    continue
                bd = os.path.join(rd, str(bid))
                if os.path.isdir(bd):
                    out.append(bd)
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
