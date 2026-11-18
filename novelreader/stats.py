# -*- coding: utf-8 -*-
"""阅读统计：朗读时长、软件开启时长、阅读字数、连续天数与周报。

数据存 `<数据目录>/stats.json`（与 library.json 同级），结构：

    {
      "schema": 1,
      "created": 1763...,
      "days": {
        "2026-11-18": {"speak": 1234.5, "app": 3600.0, "chars": 12345,
                       "books": {"<book_id>": 1234}}
      }
    }

- `speak`：真正处于朗读状态（播放中，不含暂停）的秒数，由 UI 每 5 秒心跳累加。
- `app`：软件开启秒数，由 UI 每 60 秒心跳累加，退出时补最后一小段。
- `chars`：读过的正文字数（按阅读位置前进量累加，跨章连续计算）。
- `books`：按书拆分的字数，用于「读得最多的书」这类展示，不影响统计口径。

统计口径只认本机数据，不参与 WebDAV 同步（同步只带书架/进度/书签）。
"""
import json
import os
import threading
import time
from datetime import date, timedelta

from .storage import data_dir

SCHEMA_VERSION = 1
FLUSH_INTERVAL = 15.0      # 脏数据最快落盘间隔（秒），避免每次心跳都写盘
ACTIVE_SPEAK = 60.0        # 判定「这天有阅读」：朗读 ≥60 秒
ACTIVE_CHARS = 200         # 或字数 ≥200


def stats_path():
    return os.path.join(data_dir(), "stats.json")


def day_key(ts=None):
    """本地时区的日期键（YYYY-MM-DD）。"""
    return time.strftime("%Y-%m-%d", time.localtime(ts if ts is not None else time.time()))


def _blank_day():
    return {"speak": 0.0, "app": 0.0, "chars": 0, "books": {}}


def split_duration(seconds):
    """秒 → (小时, 分钟)；不足 1 分钟按 0 小时 0 分钟。"""
    try:
        total_min = int(max(0, float(seconds)) // 60)
    except Exception:
        total_min = 0
    return total_min // 60, total_min % 60


def split_chars(n):
    """字数 → (万, 剩余整数)，用于「1.2 万字」这类展示。"""
    try:
        n = int(max(0, n))
    except Exception:
        n = 0
    if n < 10000:
        return 0, n
    return n // 10000, n % 10000


class StatsStore:
    """按天累加的统计存储；写盘节流，退出前 `flush()`。"""

    def __init__(self, path=None):
        self.path = path or stats_path()
        self._lock = threading.RLock()
        self._dirty = False
        self._last_flush = 0.0
        self.data = {"schema": SCHEMA_VERSION, "created": time.time(), "days": {}}
        self.load()

    # ---------- 读写 ----------
    def load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and isinstance(loaded.get("days"), dict):
                    self.data = loaded
        except Exception:
            pass
        self.data.setdefault("schema", SCHEMA_VERSION)
        self.data.setdefault("created", time.time())
        self.data.setdefault("days", {})

    def flush(self, force=True):
        """落盘。force=False 时按 FLUSH_INTERVAL 节流。"""
        with self._lock:
            if not self._dirty:
                return
            now = time.time()
            if not force and (now - self._last_flush) < FLUSH_INTERVAL:
                return
            tmp = self.path + ".tmp"
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
            except Exception:
                pass
            for _attempt in range(3):
                try:
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(self.data, f, ensure_ascii=False, indent=1)
                    os.replace(tmp, self.path)
                    self._dirty = False
                    self._last_flush = now
                    return
                except (PermissionError, OSError):
                    if _attempt == 2:
                        return
                    time.sleep(0.1)

    # ---------- 累加 ----------
    def _day(self, ts=None, create=True):
        k = day_key(ts)
        days = self.data["days"]
        d = days.get(k)
        if d is None:
            if not create:
                return None, k
            d = _blank_day()
            days[k] = d
        # 旧文件或手改过的数据可能缺字段
        for f, dv in (("speak", 0.0), ("app", 0.0), ("chars", 0)):
            if f not in d or not isinstance(d.get(f), (int, float)):
                d[f] = dv
        if not isinstance(d.get("books"), dict):
            d["books"] = {}
        return d, k

    def add(self, key, value, ts=None):
        """累加 speak / app / chars。非法值或非正值直接忽略。"""
        try:
            value = float(value)
        except Exception:
            return
        if value <= 0 or key not in ("speak", "app", "chars"):
            return
        with self._lock:
            d, _ = self._day(ts)
            d[key] = float(d.get(key) or 0) + value
            if key == "chars":
                d[key] = int(d[key])
            self._dirty = True

    def add_book_chars(self, bid, n, ts=None):
        """按书累加字数（只做展示，不参与 streak 口径）。"""
        if not bid:
            return
        try:
            n = int(n)
        except Exception:
            return
        if n <= 0:
            return
        with self._lock:
            d, _ = self._day(ts)
            d["books"][bid] = int(d["books"].get(bid, 0)) + n
            self._dirty = True

    # ---------- 查询 ----------
    def day(self, key=None):
        d, k = self._day(key, create=False)
        out = _blank_day()
        if d:
            out["speak"] = float(d.get("speak") or 0)
            out["app"] = float(d.get("app") or 0)
            out["chars"] = int(d.get("chars") or 0)
            out["books"] = dict(d.get("books") or {})
        out["date"] = k
        return out

    @staticmethod
    def _active(d):
        return (d.get("speak") or 0) >= ACTIVE_SPEAK or (d.get("chars") or 0) >= ACTIVE_CHARS

    def snapshot(self, now=None):
        """一次性给出 UI 需要的全部数字（今日/本周/累计/连续天数）。"""
        now_ts = time.time() if now is None else float(now)
        today = date.fromtimestamp(now_ts)
        days = self.data.get("days") or {}

        def _val(k, f):
            d = days.get(k)
            if not isinstance(d, dict):
                return 0.0
            try:
                return float(d.get(f) or 0)
            except Exception:
                return 0.0

        # 本周（周一为起点）
        week_start = today - timedelta(days=today.weekday())
        week = []
        for i in range(7):
            dd = week_start + timedelta(days=i)
            k = dd.isoformat()
            week.append({
                "date": k,
                "weekday": dd.weekday(),
                "speak": _val(k, "speak"),
                "chars": int(_val(k, "chars")),
                "app": _val(k, "app"),
            })
        last_week_start = week_start - timedelta(days=7)
        last_week_speak = 0.0
        last_week_chars = 0.0
        for i in range(7):
            k = (last_week_start + timedelta(days=i)).isoformat()
            last_week_speak += _val(k, "speak")
            last_week_chars += _val(k, "chars")

        # 累计与有效天数
        total_speak = total_app = total_chars = 0.0
        total_days = 0
        best_streak = cur_streak = 0
        best_day = ""
        best_day_chars = -1
        for k in sorted(days.keys()):
            d = days[k]
            if not isinstance(d, dict):
                continue
            sp = float(d.get("speak") or 0)
            ap = float(d.get("app") or 0)
            ch = float(d.get("chars") or 0)
            total_speak += sp
            total_app += ap
            total_chars += ch
            if ch > best_day_chars:
                best_day_chars = ch
                best_day = k
            if self._active(d):
                total_days += 1
                cur_streak += 1
                if cur_streak > best_streak:
                    best_streak = cur_streak
            else:
                cur_streak = 0

        # 当前连续天数：从今天往前数；今天还没读则从昨天起算（今天仍有机会续上）
        streak = 0
        probe = today
        if not self._active(days.get(today.isoformat()) or {}):
            probe = today - timedelta(days=1)
        while True:
            d = days.get(probe.isoformat())
            if isinstance(d, dict) and self._active(d):
                streak += 1
                probe -= timedelta(days=1)
            else:
                break
            if streak > 3650:
                break

        return {
            "today": self.day(now_ts),
            "yesterday": self.day(now_ts - 86400),
            "week": week,
            "week_start": week_start.isoformat(),
            "week_speak": sum(x["speak"] for x in week),
            "week_chars": sum(x["chars"] for x in week),
            "week_app": sum(x["app"] for x in week),
            "last_week_speak": last_week_speak,
            "last_week_chars": last_week_chars,
            "total_speak": total_speak,
            "total_app": total_app,
            "total_chars": total_chars,
            "total_days": total_days,
            "streak": streak,
            "best_streak": best_streak,
            "best_day": best_day,
            "best_day_chars": max(0, best_day_chars),
        }
