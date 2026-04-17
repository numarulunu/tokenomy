"""FastAPI sync hub for tokenomy. One codebase, two deploy modes.

Local mode (default):
    KONTEXT_HOST=127.0.0.1 KONTEXT_ALLOW_ANON=1 python -m cloud.server
    → no auth, loopback-only. The client and server can both run on the
      same machine; useful for single-device development and tests.

Hub mode (VPS):
    KONTEXT_HOST=0.0.0.0 TOKENOMY_SYNC_TOKEN=<long-random> python -m cloud.server
    → bearer-auth required on every sync endpoint. Health stays open so
      upstream load balancers can probe.

Endpoints:
    GET  /health                    — always 200 (no auth)
    POST /v1/sync/push              — bulk UPSERT events for one device
    GET  /v1/sync/status            — per-platform watermark for a device
    GET  /v1/sync/devices           — enrolled devices (debugging / dashboard)

The server never de-normalizes. Events land straight in the `events` table
via the same `bulk_upsert_events` used by the local exporter, so any device
that pushes is treated as if it had run the exporter locally on this box.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud import db as cloud_db  # noqa: E402

log = logging.getLogger("cloud.server")

VERSION = "0.1.0"

# State kinds we actively validate. New values 400 — better a loud client-side
# typo than a silent "where did my data go" in the reader.
KNOWN_STATE_KINDS = {"usage", "applied"}

# ─────────────────── wire models ───────────────────


class EventIn(BaseModel):
    """Matches the dict shape emitted by cloud.exporter._iter_*_events."""

    device_id: str
    platform: str
    kind: str
    session_id: str
    ts_utc: str
    model: Optional[str] = None
    project: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    tool_name: Optional[str] = None
    tool_bytes: Optional[int] = None
    tool_is_error: Optional[int] = None
    tool_truncated: Optional[int] = None
    dedupe_key: str


class PushRequest(BaseModel):
    device_id: str
    label: Optional[str] = None
    events: list[EventIn] = Field(default_factory=list)


class PushResponse(BaseModel):
    accepted: int
    inserted: int
    device_id: str


class StatePushRequest(BaseModel):
    """Opaque per-device state blob (usage.json, applied.json, etc.)."""

    device_id: str
    label: Optional[str] = None
    kind: str
    value: Any


# ─────────────────── auth ───────────────────


def _allow_anon() -> bool:
    return os.environ.get("KONTEXT_ALLOW_ANON", "").strip() == "1"


def _expected_token() -> str:
    return os.environ.get("TOKENOMY_SYNC_TOKEN", "").strip()


def _extract_bearer(header: str | None) -> str:
    if not header:
        return ""
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def require_auth(authorization: str | None = Header(default=None)) -> None:
    if _allow_anon():
        return
    expected = _expected_token()
    if not expected:
        # Fail closed: refuse to serve if hub mode is implied but no token is
        # configured. Prevents accidental open deploys.
        raise HTTPException(
            status_code=503,
            detail="server misconfigured: set TOKENOMY_SYNC_TOKEN or KONTEXT_ALLOW_ANON=1",
        )
    provided = _extract_bearer(authorization)
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail="missing or invalid bearer token",
            headers={"WWW-Authenticate": 'Bearer realm="tokenomy"'},
        )


# ─────────────────── app factory ───────────────────


def build_app(db_path: str) -> FastAPI:
    app = FastAPI(title="Tokenomy Sync Hub", version=VERSION)

    @app.get("/health")
    def health() -> dict:
        # Cheap liveness check. Touches the DB so a corrupted/missing file
        # turns into a 500 instead of a silent false-positive.
        conn = cloud_db.connect(db_path)
        try:
            v = cloud_db.current_schema_version(conn)
            return {"status": "ok", "schema_version": v, "version": VERSION}
        finally:
            conn.close()

    @app.post(
        "/v1/sync/push",
        response_model=PushResponse,
        dependencies=[Depends(require_auth)],
    )
    def sync_push(req: PushRequest) -> PushResponse:
        # Stamp every event with the request-level device_id so a malicious or
        # buggy client can't write into another device's row-space. The dedupe
        # key stays intact so idempotency holds.
        rows: list[dict] = []
        for ev in req.events:
            row = ev.model_dump()
            row["device_id"] = req.device_id
            rows.append(row)

        conn = cloud_db.connect(db_path)
        try:
            cloud_db.init_schema(conn)
            cloud_db.upsert_device(conn, req.device_id, label=req.label)
            with cloud_db.tx(conn):
                inserted = cloud_db.bulk_upsert_events(conn, rows)
        finally:
            conn.close()

        return PushResponse(
            accepted=len(rows), inserted=inserted, device_id=req.device_id
        )

    @app.post(
        "/v1/sync/state",
        dependencies=[Depends(require_auth)],
    )
    def sync_state(req: StatePushRequest) -> dict:
        if req.kind not in KNOWN_STATE_KINDS:
            raise HTTPException(
                status_code=400,
                detail=f"unknown state kind: {req.kind}",
            )
        blob = json.dumps(req.value, ensure_ascii=False)
        conn = cloud_db.connect(db_path)
        try:
            cloud_db.init_schema(conn)
            cloud_db.upsert_device(conn, req.device_id, label=req.label)
            with cloud_db.tx(conn):
                cloud_db.upsert_device_state(conn, req.device_id, req.kind, blob)
        finally:
            conn.close()
        return {"status": "ok", "device_id": req.device_id, "kind": req.kind}

    @app.get(
        "/v1/sync/status",
        dependencies=[Depends(require_auth)],
    )
    def sync_status(device_id: str = Query(..., min_length=1)) -> dict:
        """Return the hub's view of what it already has for this device.

        Clients use the per-platform max(ts_utc) as a push watermark so they
        only transmit events the hub hasn't acknowledged yet. Also returns
        `count` so the dashboard/client can sanity-check totals.
        """
        conn = cloud_db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT label, last_seen FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            per_platform = conn.execute(
                """
                SELECT platform,
                       MAX(ts_utc) AS max_ts,
                       COUNT(*)    AS n
                FROM events
                WHERE device_id = ?
                GROUP BY platform
                """,
                (device_id,),
            ).fetchall()
        finally:
            conn.close()

        return {
            "device_id": device_id,
            "label": row["label"] if row else None,
            "last_seen": row["last_seen"] if row else None,
            "platforms": {
                r["platform"]: {"max_ts_utc": r["max_ts"], "count": r["n"]}
                for r in per_platform
            },
        }

    @app.get(
        "/v1/sync/devices",
        dependencies=[Depends(require_auth)],
    )
    def sync_devices() -> dict:
        conn = cloud_db.connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT d.device_id, d.label, d.first_seen, d.last_seen,
                       COALESCE(e.n, 0) AS event_count
                FROM devices d
                LEFT JOIN (
                    SELECT device_id, COUNT(*) AS n FROM events GROUP BY device_id
                ) e ON e.device_id = d.device_id
                ORDER BY d.last_seen DESC
                """
            ).fetchall()
        finally:
            conn.close()
        return {"devices": [dict(r) for r in rows]}

    return app


# ─────────────────── uvicorn entrypoint ───────────────────


def _env(name: str, default: str) -> str:
    v = os.environ.get(name, "").strip()
    return v if v else default


def main() -> int:
    import uvicorn

    db_path = _env("TOKENOMY_DB_PATH", cloud_db.default_db_path())
    host = _env("KONTEXT_HOST", "127.0.0.1")
    port = int(_env("KONTEXT_PORT", "8787"))
    log_level = _env("KONTEXT_LOG_LEVEL", "info").lower()

    # Init schema eagerly so the first /health doesn't race with the first push.
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = cloud_db.connect(db_path)
    try:
        cloud_db.init_schema(conn)
    finally:
        conn.close()

    app = build_app(db_path)

    auth = "ANON" if _allow_anon() else ("BEARER" if _expected_token() else "MISCONFIGURED")
    print(
        f"[cloud.server] db={db_path} bind={host}:{port} auth={auth} log_level={log_level}",
        flush=True,
    )
    uvicorn.run(app, host=host, port=port, log_level=log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
