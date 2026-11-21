import json
import sqlite3
import time
import uuid
from contextlib import contextmanager


def uid():
    return uuid.uuid4().hex


class DB:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, cli_session_id TEXT);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                status TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL,
                finished_at REAL, error TEXT, model TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                run_id TEXT REFERENCES runs(id) ON DELETE CASCADE, role TEXT NOT NULL,
                content TEXT NOT NULL, attachments TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS events_session ON events(session_id,id);
            CREATE INDEX IF NOT EXISTS runs_session ON runs(session_id,created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(session_id)
                WHERE status IN ('queued','running');
            """)

    def all(self, sql, args=()):
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.connect() as c:
            c.execute(sql, args)

    def event(self, sid, rid, kind, data):
        with self.connect() as c:
            c.execute("INSERT INTO events(session_id,run_id,kind,data,created_at) VALUES(?,?,?,?,?)",
                      (sid, rid, kind, json.dumps(data, ensure_ascii=False), time.time()))

    def recover(self):
        for run in self.all("SELECT * FROM runs WHERE status IN ('queued','running')"):
            msg = "服务已重启，上次执行中断。文件和对话已保留，可发送新消息继续。"
            self.execute("UPDATE runs SET status='interrupted',finished_at=?,error=? WHERE id=?",
                         (time.time(), msg, run["id"]))
            self.event(run["session_id"], run["id"], "status", {"status": "interrupted", "error": msg})

    def sessions(self):
        return self.all("""SELECT s.*,
            (SELECT status FROM runs WHERE session_id=s.id ORDER BY created_at DESC LIMIT 1) status,
            (SELECT content FROM messages WHERE session_id=s.id ORDER BY created_at DESC LIMIT 1) preview
            FROM sessions s ORDER BY updated_at DESC""")

    def snapshot(self, sid):
        with self.connect() as c:
            c.execute("BEGIN")
            session = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if session is None:
                return None
            result = dict(session)
            result["messages"] = [dict(r) for r in c.execute("SELECT * FROM messages WHERE session_id=? ORDER BY created_at", (sid,))]
            for m in result["messages"]:
                m["attachments"] = json.loads(m["attachments"])
            result["runs"] = [dict(r) for r in c.execute("SELECT * FROM runs WHERE session_id=? ORDER BY created_at", (sid,))]
            result["events"] = [dict(r) for r in c.execute("SELECT * FROM (SELECT * FROM events WHERE session_id=? ORDER BY id DESC LIMIT 300) ORDER BY id", (sid,))]
            for e in result["events"]:
                e["data"] = json.loads(e["data"])
            result["last_event_id"] = result["events"][-1]["id"] if result["events"] else 0
            return result
