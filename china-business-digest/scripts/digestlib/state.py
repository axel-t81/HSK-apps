import json
import sqlite3
from contextlib import contextmanager

from .errors import StateError
from .models import Item
from .util import utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    url TEXT PRIMARY KEY,
    etag TEXT,
    last_modified TEXT,
    content_type TEXT,
    body BLOB NOT NULL,
    fetched_at TEXT NOT NULL,
    final_url TEXT
);
CREATE TABLE IF NOT EXISTS items (
    key TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT,
    payload_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    processed_at TEXT
);
CREATE INDEX IF NOT EXISTS items_processed ON items(processed_at);
CREATE TABLE IF NOT EXISTS model_cache (
    cache_key TEXT PRIMARY KEY,
    response_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_date TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    digest_json TEXT NOT NULL,
    text_body TEXT NOT NULL,
    html_body TEXT NOT NULL,
    candidate_keys_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sending',
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class State:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(SCHEMA)
        self._migrate_runs()
        self._migrate_http_cache()

    def _migrate_runs(self):
        columns = {row["name"]: row for row in self.connection.execute("PRAGMA table_info(runs)")}
        if "status" in columns and not columns["sent_at"]["notnull"]:
            return
        with self.connection:
            self.connection.execute("ALTER TABLE runs RENAME TO runs_legacy")
            self.connection.execute("""CREATE TABLE runs (
                run_date TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                digest_json TEXT NOT NULL,
                text_body TEXT NOT NULL,
                html_body TEXT NOT NULL,
                candidate_keys_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'sending',
                sent_at TEXT
            )""")
            self.connection.execute(
                """INSERT INTO runs
                   (run_date, subject, digest_json, text_body, html_body,
                    candidate_keys_json, status, sent_at)
                   SELECT run_date, subject, digest_json, text_body, html_body,
                          candidate_keys_json, 'sent', sent_at FROM runs_legacy"""
            )
            self.connection.execute("DROP TABLE runs_legacy")

    def _migrate_http_cache(self):
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(http_cache)")}
        if "final_url" not in columns:
            self.connection.execute("ALTER TABLE http_cache ADD COLUMN final_url TEXT")
            self.connection.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        self.connection.close()

    def get_http(self, url):
        row = self.connection.execute("SELECT * FROM http_cache WHERE url = ?", (url,)).fetchone()
        return dict(row) if row else None

    def set_http(self, url, body, etag=None, last_modified=None, content_type=None, final_url=None):
        now = utc_now().isoformat()
        self.connection.execute(
            """INSERT INTO http_cache
               (url, etag, last_modified, content_type, body, fetched_at, final_url)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                   etag=excluded.etag, last_modified=excluded.last_modified,
                   content_type=excluded.content_type, body=excluded.body,
                   fetched_at=excluded.fetched_at, final_url=excluded.final_url""",
            (url, etag, last_modified, content_type, sqlite3.Binary(body), now, final_url),
        )
        self.connection.commit()

    def get_item(self, key):
        row = self.connection.execute("SELECT * FROM items WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["item"] = Item.from_dict(json.loads(row["payload_json"]))
        return result

    def is_processed(self, key):
        row = self.connection.execute("SELECT processed_at FROM items WHERE key = ?", (key,)).fetchone()
        return bool(row and row["processed_at"])

    def upsert_item(self, item):
        now = utc_now().isoformat()
        self.connection.execute(
            """INSERT INTO items
               (key, source_id, url, published_at, payload_json, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   url=excluded.url, published_at=excluded.published_at,
                   payload_json=excluded.payload_json, last_seen_at=excluded.last_seen_at""",
            (
                item.key, item.source_id, item.url,
                item.published_at.isoformat() if item.published_at else None,
                json.dumps(item.to_dict(), ensure_ascii=False), now, now,
            ),
        )
        self.connection.commit()

    def mark_items_processed(self, keys, timestamp=None):
        if not keys:
            return
        timestamp = timestamp or utc_now().isoformat()
        placeholders = ",".join("?" for _ in keys)
        self.connection.execute(
            f"UPDATE items SET processed_at = ? WHERE key IN ({placeholders})",
            [timestamp, *keys],
        )

    def get_model(self, key):
        row = self.connection.execute("SELECT response_text FROM model_cache WHERE cache_key = ?", (key,)).fetchone()
        return row["response_text"] if row else None

    def set_model(self, key, response_text):
        self.connection.execute(
            "INSERT OR REPLACE INTO model_cache (cache_key, response_text, created_at) VALUES (?, ?, ?)",
            (key, response_text, utc_now().isoformat()),
        )
        self.connection.commit()

    def has_sent_run(self):
        row = self.connection.execute("SELECT 1 FROM runs WHERE status = 'sent' LIMIT 1").fetchone()
        return bool(row)

    def has_collection_run(self):
        row = self.connection.execute("SELECT value FROM metadata WHERE key = 'last_collection_at'").fetchone()
        return bool(row)

    def mark_collection(self):
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_collection_at', ?)",
            (utc_now().isoformat(),),
        )
        self.connection.commit()

    def get_pending_runs(self):
        rows = self.connection.execute(
            "SELECT run_date, subject FROM runs WHERE status = 'sending' ORDER BY run_date"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_date):
        row = self.connection.execute("SELECT * FROM runs WHERE run_date = ?", (run_date,)).fetchone()
        return dict(row) if row else None

    def record_pending(self, run_date, subject, digest, text_body, html_body, candidate_keys):
        """Reserve a delivery before SMTP so an interrupted attempt fails closed."""
        now = utc_now().isoformat()
        try:
            with self.connection:
                self.connection.execute(
                    """INSERT INTO runs
                       (run_date, subject, digest_json, text_body, html_body,
                        candidate_keys_json, status, sent_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'sending', NULL)""",
                    (
                        run_date, subject, json.dumps(digest, ensure_ascii=False),
                        text_body, html_body, json.dumps(candidate_keys),
                    ),
                )
                self.mark_items_processed(candidate_keys, now)
        except sqlite3.IntegrityError as error:
            raise StateError(f"A delivery record already exists for {run_date}") from error

    def mark_sent(self, run_date):
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE runs SET status = 'sent', sent_at = ? WHERE run_date = ?",
                (utc_now().isoformat(), run_date),
            )
        if cursor.rowcount != 1:
            raise StateError(f"No saved delivery exists for {run_date}")

    def record_sent(self, run_date, subject, digest, text_body, html_body, candidate_keys):
        self.record_pending(run_date, subject, digest, text_body, html_body, candidate_keys)
        self.mark_sent(run_date)


@contextmanager
def exclusive_file_lock(path):
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise StateError("Another digest process is already running") from error
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
