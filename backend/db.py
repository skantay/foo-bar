"""SQLite как документное хранилище: каждая сущность — JSON в своей таблице."""
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "cqa.db"
TABLES = ("dialogs", "checklists", "evaluations", "appeals", "jobs", "ground_truth")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        for t in TABLES:
            _conn.execute(f"CREATE TABLE IF NOT EXISTS {t} (id TEXT PRIMARY KEY, created_at REAL, doc TEXT)")
        _conn.commit()
    return _conn


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now() -> float:
    return time.time()


def put(table: str, doc: dict) -> dict:
    doc.setdefault("created_at", now())
    with _lock:
        conn().execute(f"INSERT OR REPLACE INTO {table} (id, created_at, doc) VALUES (?, ?, ?)",
                       (doc["id"], doc["created_at"], json.dumps(doc, ensure_ascii=False)))
        conn().commit()
    return doc


def get(table: str, id_: str) -> dict | None:
    with _lock:
        row = conn().execute(f"SELECT doc FROM {table} WHERE id = ?", (id_,)).fetchone()
    return json.loads(row[0]) if row else None


def all(table: str) -> list[dict]:
    with _lock:
        rows = conn().execute(f"SELECT doc FROM {table} ORDER BY created_at").fetchall()
    return [json.loads(r[0]) for r in rows]


def delete(table: str, id_: str) -> None:
    with _lock:
        conn().execute(f"DELETE FROM {table} WHERE id = ?", (id_,))
        conn().commit()
