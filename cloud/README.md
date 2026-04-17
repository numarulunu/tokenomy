# tokenomy · cloud

Cross-device sync for tokenomy. One codebase, two deploy modes — no
forks, no schema duplication, just different env vars.

## Modes

### Local-only (default)

Exporter writes to `~/.claude/tokenomy/tokenomy.db`. The analyzer,
tuner, and MCP server all read from that same path. Nothing leaves the
machine.

```bash
python -m cloud.exporter                    # refresh DB from transcripts
python -m cloud.db info                     # inspect schema + counts
```

No client, no server, no token needed.

### Hub (VPS)

A second process binds the sync API on `0.0.0.0` and every device pushes
new events + state blobs to it over HTTPS. One DB aggregates N devices.

```bash
# on the VPS
TOKENOMY_DB_PATH=/data/tokenomy.db \
TOKENOMY_SYNC_TOKEN=<mint output> \
KONTEXT_HOST=0.0.0.0 \
  python -m cloud.server

# on each dev machine
TOKENOMY_HUB_URL=https://hub.example.com \
TOKENOMY_SYNC_TOKEN=<same token> \
  python -m cloud.client push
```

The dashboard repo (`tokenomy-dashboard`) vendors the same sync router
into its FastAPI app, so the dashboard UI and the hub API share a
single container / single hostname / single DB.

## Wire protocol

All endpoints require `Authorization: Bearer <TOKENOMY_SYNC_TOKEN>`
unless `KONTEXT_ALLOW_ANON=1` (local loopback development only).

| Method | Path                | Purpose                                        |
|--------|---------------------|------------------------------------------------|
| GET    | `/health`           | Liveness probe (always open, no auth)          |
| POST   | `/v1/sync/push`     | Bulk UPSERT of event rows for one device       |
| POST   | `/v1/sync/state`    | Store opaque per-device JSON blob by `kind`    |
| GET    | `/v1/sync/status`   | Per-platform max(ts_utc) + counts for a device |
| GET    | `/v1/sync/devices`  | List enrolled devices                          |

Push bodies are idempotent — the server `INSERT OR IGNORE`s on
`UNIQUE(device_id, dedupe_key)`, so the client can replay a batch safely
on retry or after a network partition.

## Token minting

```bash
python -m cloud.mint_token
```

Prints a 32-byte URL-safe random string. Store it in the VPS secrets
manager and in each device's user env under `TOKENOMY_SYNC_TOKEN`.

## Schema

One SQLite file (`tokenomy.db`), WAL mode. Canonical definition lives in
`cloud/db.py` — the dashboard repo vendors an identical copy in
`app/sync_db.py`. When bumping the schema, update both; the `ts_utc`
watermark protocol assumes lockstep.

Tables:

- `devices(device_id PK, label, token_hash, first_seen, last_seen)`
- `events(id PK, device_id, platform, kind, session_id, ts_utc, …,
  dedupe_key, UNIQUE(device_id, dedupe_key))`
- `quotas(device_id, platform, window PK, used_pct, resets_at,
  captured_at)`
- `device_state(device_id, kind PK, value_json, captured_at)` — opaque
  per-device blobs. `kind ∈ {usage, applied}` today.

## Environment

| Var                     | Where      | Purpose                                          |
|-------------------------|------------|--------------------------------------------------|
| `TOKENOMY_DB_PATH`      | both       | SQLite path (device or hub)                      |
| `TOKENOMY_HUB_URL`      | client     | Base URL of the hub                              |
| `TOKENOMY_SYNC_TOKEN`   | both       | Shared bearer secret                             |
| `KONTEXT_HOST`          | server     | Bind host (default `127.0.0.1`; `0.0.0.0` for VPS) |
| `KONTEXT_PORT`          | server     | Bind port (default `8787`)                       |
| `KONTEXT_ALLOW_ANON`    | server     | `=1` skips bearer auth (loopback/dev only)       |

## Files

| File             | Role                                                    |
|------------------|---------------------------------------------------------|
| `db.py`          | Schema, connection, UPSERT helpers, `cloud-db` CLI      |
| `exporter.py`    | Walks Claude/Codex transcripts → events/state in SQLite |
| `server.py`      | FastAPI sync hub (VPS-side)                             |
| `client.py`      | `push` / `status` CLI, ships events + state to hub      |
| `mint_token.py`  | Generates a bearer secret                               |

## Failure modes

- **Hub down** → client exits non-zero, watermark unchanged; next run
  resumes cleanly.
- **Hub misconfigured** (no token, not `ALLOW_ANON`) → `/v1/sync/*`
  returns 503. Fails closed on purpose.
- **Schema drift between repos** → UPSERT breaks silently. Keep
  `cloud/db.py` and `tokenomy-dashboard/app/sync_db.py` in lockstep.
- **Partial event batch** → watermark advances only on HTTP 2xx, so a
  5xx mid-stream leaves you at the last acked row.
