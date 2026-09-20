# -*- coding: utf-8 -*-
"""本地缓存 (SQLite) —— 工坊元数据 / mod 文件指纹 / 加载顺序 / 限流。

为什么需要它:

1. **工坊请求是有限资源**。Steam 对接口有限流，社区页面也有反爬。
   搜索结果、条目详情缓存下来后，重复查看就不必再打网络请求（离线也能看到上次的信息）。
2. **冲突检测要给每个 mod 走一遍文件树**，大 mod 动辄几百个文件。
   把"目录状态指纹"落盘后，只要目录没变（顶层 mtime + 条目名不变）就直接复用文件清单，
   启动时不必重新全盘扫描。
3. 加载顺序、上次看到的工坊更新时间，也都需要持久化。

只用标准库 sqlite3，没有额外依赖。所有方法都加锁，可以从工作线程调用。
"""
import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS workshop(
    id          TEXT PRIMARY KEY,
    title       TEXT,
    preview     TEXT,
    description TEXT,
    created     INTEGER,
    updated     INTEGER,
    size        INTEGER,
    fetched_at  REAL,
    dead        INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fingerprint(
    folder TEXT PRIMARY KEY,
    token  TEXT,
    files  TEXT,
    ts     REAL
);
CREATE TABLE IF NOT EXISTS load_order(
    name TEXT PRIMARY KEY,
    seq  INTEGER
);
CREATE TABLE IF NOT EXISTS ratelog(ts REAL);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


class Cache:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        d = os.path.dirname(os.path.abspath(path))
        if d:
            try:
                os.makedirs(d, exist_ok=True)
            except OSError:
                pass
        self._db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self):
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass

    # ---------------------------------------------------------------- 工坊
    def get_workshop(self, ids, max_age=None):
        """取缓存的工坊条目；max_age(秒) 为 None 表示不过期"""
        ids = [str(i) for i in (ids or []) if i]
        if not ids:
            return {}
        out = {}
        now = time.time()
        with self._lock:
            q = ",".join("?" * len(ids))
            for row in self._db.execute(
                    "SELECT * FROM workshop WHERE id IN (%s)" % q, ids):
                if max_age is not None and not row["dead"]:
                    if (now - (row["fetched_at"] or 0)) > max_age:
                        continue
                out[row["id"]] = {
                    "id": row["id"], "title": row["title"] or "",
                    "preview": row["preview"] or "",
                    "description": row["description"] or "",
                    "created": row["created"] or 0, "updated": row["updated"] or 0,
                    "size": row["size"] or 0,
                    "fetched_at": row["fetched_at"] or 0,
                    "dead": bool(row["dead"]),
                }
        return out

    def put_workshop(self, items):
        """items: {id: {title, preview, description, created, updated, size}}"""
        now = time.time()
        with self._lock:
            for pid, it in (items or {}).items():
                self._db.execute(
                    "INSERT INTO workshop(id,title,preview,description,created,updated,"
                    "size,fetched_at,dead) VALUES(?,?,?,?,?,?,?,?,0) "
                    "ON CONFLICT(id) DO UPDATE SET title=excluded.title,"
                    "preview=excluded.preview, description=excluded.description,"
                    "created=excluded.created, updated=excluded.updated,"
                    "size=excluded.size, fetched_at=excluded.fetched_at, dead=0",
                    (str(pid), it.get("title", ""), it.get("preview", ""),
                     it.get("description", ""), int(it.get("created") or 0),
                     int(it.get("updated") or 0), int(it.get("size") or 0), now))
            self._db.commit()

    def mark_dead(self, pid):
        """条目已失效(result=9 / 不存在) —— 以后不再重试"""
        with self._lock:
            self._db.execute(
                "INSERT INTO workshop(id, fetched_at, dead) VALUES(?,?,1) "
                "ON CONFLICT(id) DO UPDATE SET dead=1, fetched_at=excluded.fetched_at",
                (str(pid), time.time()))
            self._db.commit()

    def dead_ids(self):
        with self._lock:
            return {r["id"] for r in self._db.execute(
                "SELECT id FROM workshop WHERE dead=1")}

    # ------------------------------------------------------- mod 文件指纹
    def get_fingerprint(self, folder):
        with self._lock:
            r = self._db.execute(
                "SELECT token, files FROM fingerprint WHERE folder=?",
                (folder,)).fetchone()
        if not r:
            return None
        try:
            return {"token": r["token"], "files": json.loads(r["files"] or "[]")}
        except Exception:
            return None

    def set_fingerprint(self, folder, token, files):
        with self._lock:
            self._db.execute(
                "INSERT INTO fingerprint(folder,token,files,ts) VALUES(?,?,?,?) "
                "ON CONFLICT(folder) DO UPDATE SET token=excluded.token,"
                "files=excluded.files, ts=excluded.ts",
                (folder, token, json.dumps(sorted(files)), time.time()))
            self._db.commit()

    def drop_fingerprint(self, folder=None):
        with self._lock:
            if folder is None:
                self._db.execute("DELETE FROM fingerprint")
            else:
                self._db.execute("DELETE FROM fingerprint WHERE folder=?", (folder,))
            self._db.commit()

    # ---------------------------------------------------------------- 顺序
    def save_order(self, names):
        with self._lock:
            self._db.execute("DELETE FROM load_order")
            self._db.executemany("INSERT INTO load_order(name,seq) VALUES(?,?)",
                                 [(n, i) for i, n in enumerate(names or [])])
            self._db.commit()

    def load_order(self):
        with self._lock:
            return [r["name"] for r in self._db.execute(
                "SELECT name FROM load_order ORDER BY seq")]

    # ------------------------------------------------------------ 限流器
    def rate_allow(self, limit=180, window=300):
        """滑动窗口限流: window 秒内最多 limit 次。

        返回 True 表示"这次可以发请求"; False 表示已被限流(调用方应退避)。
        """
        now = time.time()
        with self._lock:
            self._db.execute("DELETE FROM ratelog WHERE ts < ?", (now - window,))
            n = self._db.execute("SELECT COUNT(*) c FROM ratelog").fetchone()["c"]
            if n >= limit:
                self._db.commit()
                return False
            self._db.execute("INSERT INTO ratelog(ts) VALUES(?)", (now,))
            self._db.commit()
        return True

    def rate_state(self, limit=180, window=300):
        now = time.time()
        with self._lock:
            self._db.execute("DELETE FROM ratelog WHERE ts < ?", (now - window,))
            rows = [r["ts"] for r in self._db.execute("SELECT ts FROM ratelog ORDER BY ts")]
            self._db.commit()
        nxt = None
        if len(rows) >= limit:
            nxt = rows[0] + window
        return {"used": len(rows), "limit": limit, "window": window, "next_at": nxt}

    # ---------------------------------------------------------------- 键值
    def kv_get(self, key, default=None):
        with self._lock:
            r = self._db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        if not r:
            return default
        try:
            return json.loads(r["v"])
        except Exception:
            return default

    def kv_set(self, key, value):
        with self._lock:
            self._db.execute(
                "INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (key, json.dumps(value, ensure_ascii=False)))
            self._db.commit()

    def vacuum(self):
        with self._lock:
            self._db.execute("VACUUM")
            self._db.commit()


_CACHE = None
_LOCK = threading.Lock()


def get_cache(data_dir):
    """按数据目录取单例(同一个进程内共享一个连接)"""
    global _CACHE
    with _LOCK:
        if _CACHE is None or _CACHE.path != os.path.join(data_dir, "cache.db"):
            if _CACHE is not None:
                _CACHE.close()
            _CACHE = Cache(os.path.join(data_dir, "cache.db"))
        return _CACHE
