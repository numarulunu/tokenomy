"""SQLite backbone for tokenomy cloud sync + dashboard.

One DB serves both the device-local cache and the hub. Path picked via env:
  TOKENOMY_DB_PATH  — defaults to ~/.claude/tokenomy/tokenomy.db locally; the
                      hub container sets it to /data/tokenomy.db.

All event writes are idempotent via UNIQUE(device_id, dedupe_key). Callers
can replay an entire corpus through bulk_upsert_events() without inflating
counts — the UNIQUE conflict is resolved by INSERT OR IGNORE.

WAL is mandatory — dashboard reads must not block device pushes. Every
connection opens isolation_level=None so transactions are explicit (see tx()).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

VERSION = "0.1.0"
SCHEMA_VERSION = 1


def default_db_path() -> str:
    env = os.environ.get("TOKENOMY_DB_PATH", "").strip()
    if env:
        return env
    return str(Path.home() / ".claude" / "tokenomy" / "tokenomy.db")


_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS devices (
        device_id  TEXT PRIMARY KEY,
        label      TEXT,
        token_hash TEXT,
        first_seen TEXT NOT NULL,
        last_seen  TEXT NOT NULL
    )
    """,
    # events holds both assistant turns and tool invocations. `kind` splits
    # them; dashboard queries filter on it. Single table keeps cross-device
    # aggregation one SQL statement, not a UNION.
    """
    CREATE TABLE IF NOT EXISTS events (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id               TEXT NOT NULL,
        platform                TEXT NOT NULL,
        kind                    TEXT NOT NULL,
        session_id              TEXT NOT NULL,
        ts_utc                  TEXT NOT NULL,
        model                   TEXT,
        project                 TEXT,
        input_tokens            INTEGER DEFAULT 0,
        output_tokens           INTEGER DEFAULT 0,
        cache_read_tokens       INTEGER DEFAULT 0,
        cache_creation_tokens   INTEGER DEFAULT 0,
        cost_usd                REAL DEFAULT 0.0,
        tool_name               TEXT,
        tool_bytes              INTEGER,
        tool_is_error           INTEGER,
        tool_truncated          INTEGER,
        dedupe_key              TEXT NOT NULL,
        UNIQUE(device_id, dedupe_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_utc)",
    "CREATE INDEX IF NOT EXISTS idx_events_dev_platform_ts ON events(device_id, platform, ts_utc)",
    "CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)",
    """
    CREATE TABLE IF NOT EXISTS quotas (
        device_id   TEXT NOT NULL,
        platform    TEXT NOT NULL,
        window      TEXT NOT NULL,
        used_pct    REAL NOT NULL,
        resets_at   TEXT,
        captured_at TEXT NOT NULL,
        PRIMARY KEY (device_id, platform, window)
    )
    """,
    # Opaque per-device state blobs (usage.json, applied.json, etc.). Schema
    # stays dumb — kind + JSON — so we never break wire compat when the
    # tuner reshapes its state files. Readers JSON-parse on demand.
    """
    CREATE TABLE IF NOT EXISTS device_state (
        device_id   TEXT NOT NULL,
        kind        TEXT NOT NULL,
        value_json  TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        PRIMARY KEY (device_id, kind)
    )
    """,
)


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open a WAL-mode connection. Caller must close it."""
    path = db_path or default_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    for stmt in _SCHEMA_STATEMENTS:
        conn.execute(stmt)
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))


def current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row["version"]) if row else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Placeholder for future migrations. Returns the schema version after migrating.

    Shape: when SCHEMA_VERSION bumps, add an `if v < N: apply_Nth_migration()`
    block here, then `conn.execute("UPDATE schema_version SET version = ?", (N,))`.
    Keep each migration idempotent — re-running must be safe.
    """
    return current_schema_version(conn)


_UPSERT_EVENT_SQL = """
INSERT OR IGNORE INTO events (
    device_id, platform, kind, session_id, ts_utc, model, project,
    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
    cost_usd, tool_name, tool_bytes, tool_is_error, tool_truncated, dedupe_key
) VALUES (
    :device_id, :platform, :kind, :session_id, :ts_utc, :model, :project,
    :input_tokens, :output_tokens, :cache_read_tokens, :cache_creation_tokens,
    :cost_usd, :tool_name, :tool_bytes, :tool_is_error, :tool_truncated, :dedupe_key
)
"""


def bulk_upsert_events(conn: sqlite3.Connection, events: Iterable[dict]) -> int:
    """Insert events; conflicting (device_id, dedupe_key) rows are skipped.

    Returns the count of rows actually inserted.
    """
    before = conn.total_changes
    conn.executemany(_UPSERT_EVENT_SQL, list(events))
    return conn.total_changes - before


def upsert_device(
    conn: sqlite3.Connection,
    device_id: str,
    label: str | None = None,
    token_hash: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO devices(device_id, label, token_hash, first_seen, last_seen)
        VALUES (:device_id, :label, :token_hash, :ts, :ts)
        ON CONFLICT(device_id) DO UPDATE SET
            label      = COALESCE(excluded.label, devices.label),
            token_hash = COALESCE(excluded.token_hash, devices.token_hash),
            last_seen  = excluded.last_seen
        """,
        {"device_id": device_id, "label": label, "token_hash": token_hash, "ts": now},
    )


def upsert_device_state(
    conn: sqlite3.Connection,
    device_id: str,
    kind: str,
    value_json: str,
) -> None:
    """Record the latest state blob for (device, kind). Always overwrites —
    there's only one 'current' usage snapshot, one 'current' applied-caps
    payload per device."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO device_state(device_id, kind, value_json, captured_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(device_id, kind) DO UPDATE SET
            value_json  = excluded.value_json,
            captured_at = excluded.captured_at
        """,
        (device_id, kind, value_json, now),
    )


def record_quota(
    conn: sqlite3.Connection,
    device_id: str,
    platform: str,
    window: str,
    used_pct: float,
    resets_at: str | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO quotas(device_id, platform, window, used_pct, resets_at, captured_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id, platform, window) DO UPDATE SET
            used_pct    = excluded.used_pct,
            resets_at   = excluded.resets_at,
            captured_at = excluded.captured_at
        """,
        (device_id, platform, window, used_pct, resets_at, now),
    )


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction — isolation_level=None means we drive BEGIN/COMMIT."""
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m cloud.db {init|info} [--db-path PATH]"""
    import argparse

    ap = argparse.ArgumentParser(prog="tokenomy-cloud-db")
    ap.add_argument("cmd", choices=["init", "info"])
    ap.add_argument("--db-path", default=None)
    args = ap.parse_args(argv)

    path = args.db_path or default_db_path()
    conn = connect(path)
    try:
        if args.cmd == "init":
            init_schema(conn)
            v = migrate(conn)
            print(f"[cloud.db] init ok: path={path} schema_version={v}")
            return 0
        # info
        v = current_schema_version(conn)
        devices = conn.execute("SELECT COUNT(*) AS n FROM devices").fetchone()["n"]
        events = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
        print(
            f"[cloud.db] path={path} schema_version={v} "
            f"devices={devices} events={events}"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
