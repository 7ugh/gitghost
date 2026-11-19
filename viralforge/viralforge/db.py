"""SQLite persistence (stdlib only)."""
from __future__ import annotations
import json, sqlite3, threading, time
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_runs(id INTEGER PRIMARY KEY, started_at REAL, finished_at REAL, source_status TEXT);
CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY, run_id INTEGER, source TEXT, title TEXT, url TEXT, summary TEXT,
  score REAL, age_hours REAL, category TEXT, fetched_at REAL, meta TEXT);
CREATE TABLE IF NOT EXISTS manual_signals(id INTEGER PRIMARY KEY, created_at REAL, platform TEXT, title TEXT, url TEXT, note TEXT, category TEXT);
CREATE TABLE IF NOT EXISTS ideas(id INTEGER PRIMARY KEY, run_id INTEGER, topic TEXT, category TEXT, format TEXT, hook TEXT,
  score REAL, breakdown TEXT, why TEXT, signals TEXT, status TEXT DEFAULT 'new', created_at REAL);
CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY, idea_id INTEGER, topic TEXT, category TEXT, style TEXT, status TEXT,
  title TEXT, created_at REAL, updated_at REAL, scheduled_at REAL, posted_at REAL, ig_media_id TEXT, permalink TEXT,
  qc TEXT, meta TEXT, feedback TEXT, duration REAL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, video_id TEXT, ts REAL, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY, video_id TEXT, fetched_at REAL, source TEXT, metrics TEXT);
"""
_lock = threading.RLock()
_conn: dict[str, sqlite3.Connection] = {}


def conn() -> sqlite3.Connection:
    path = str(config.HOME / "viralforge.db")
    with _lock:
        if path not in _conn:
            config.ensure_dirs()
            c = sqlite3.connect(path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.executescript(SCHEMA)
            _conn[path] = c
        return _conn[path]


def q(sql: str, args=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def x(sql: str, args=()) -> int:
    with _lock:
        cur = conn().execute(sql, args)
        conn().commit()
        return cur.lastrowid


def jl(v):  # json load tolerant
    try:
        return json.loads(v) if isinstance(v, str) else v
    except Exception:
        return v


def log_event(video_id: str, kind: str, detail: str = ""):
    x("INSERT INTO events(video_id,ts,kind,detail) VALUES(?,?,?,?)", (video_id, time.time(), kind, detail))


def get_video(vid: str) -> dict | None:
    r = q("SELECT * FROM videos WHERE id=?", (vid,))
    if not r:
        return None
    v = r[0]
    for k in ("qc", "meta"):
        v[k] = jl(v[k]) or {}
    return v


def update_video(vid: str, **fields):
    fields["updated_at"] = time.time()
    for k in ("qc", "meta"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    cols = ", ".join(f"{k}=?" for k in fields)
    x(f"UPDATE videos SET {cols} WHERE id=?", (*fields.values(), vid))
