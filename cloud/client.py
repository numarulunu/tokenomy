"""Push local tokenomy.db rows to a sync hub.

The exporter produces the canonical local SQLite. The client reads from that
same DB and streams new-since-last-push events to an HTTP hub. Push state
lives in ~/.claude/tokenomy/push_watermarks.json keyed by hub URL so you can
push to multiple hubs (staging + prod, say) without cross-contamination.

Flow:
    1. Load device_id (exporter creates it; we never mint here).
    2. Ask /v1/sync/status for per-platform max(ts_utc) already on the hub.
    3. Take max(local_watermark, server_watermark) as the cutoff — this lets
       a fresh machine pointed at an existing hub resume correctly instead
       of re-uploading 100k+ rows.
    4. Batch events > cutoff, POST to /v1/sync/push.
    5. Advance local watermark to the max ts_utc in the batch.

Typical invocation:
    TOKENOMY_HUB_URL=https://tokenomy.example.com \
    TOKENOMY_SYNC_TOKEN=<bearer> \
    python -m cloud.client push
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Iterator, Optional

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud import db as cloud_db  # noqa: E402
from cloud.exporter import DEVICE_FILE, TOKENOMY_DIR, load_device_id  # noqa: E402

log = logging.getLogger("cloud.client")

PUSH_WATERMARK_FILE = TOKENOMY_DIR / "push_watermarks.json"
LOG_FILE = TOKENOMY_DIR / "_client.log"
BATCH_SIZE = 500
DEFAULT_TIMEOUT_SEC = 30.0
MAX_RETRIES = 4  # total attempts per batch; 5xx / connection errors only

PLATFORMS = ("claude", "codex")

# Per-device state files pushed as opaque JSON blobs after each event push.
# Replaces the legacy scp of these same files to the VPS /data volume.
STATE_FILES = {
    "usage":   TOKENOMY_DIR / "usage.json",
    "applied": TOKENOMY_DIR / "applied.json",
}


def _log(msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{stamp} {msg}\n")
    except OSError:
        pass


# ─────────────────── watermark ───────────────────


def load_push_watermarks() -> dict:
    if not PUSH_WATERMARK_FILE.exists():
        return {}
    try:
        data = json.loads(PUSH_WATERMARK_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_push_watermarks(marks: dict) -> None:
    PUSH_WATERMARK_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PUSH_WATERMARK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(marks, indent=2), encoding="utf-8")
    os.replace(tmp, PUSH_WATERMARK_FILE)


def _hub_key(hub_url: str) -> str:
    # Watermark key is the canonical URL minus trailing slash. Prevents two
    # entries for `https://h/` vs `https://h`.
    return hub_url.rstrip("/")


# ─────────────────── local event stream ───────────────────


_SELECT_SQL = """
SELECT device_id, platform, kind, session_id, ts_utc, model, project,
       input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
       cost_usd, tool_name, tool_bytes, tool_is_error, tool_truncated,
       dedupe_key
FROM events
WHERE device_id = :dev
  AND platform = :plat
  AND ts_utc > :cutoff
ORDER BY ts_utc ASC
"""


def _iter_rows(conn, device_id: str, platform: str, cutoff: str) -> Iterator[dict]:
    cursor = conn.execute(_SELECT_SQL, {"dev": device_id, "plat": platform, "cutoff": cutoff})
    for row in cursor:
        yield dict(row)


def _batched(it: Iterable[dict], n: int) -> Iterator[list[dict]]:
    buf: list[dict] = []
    for item in it:
        buf.append(item)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


# ─────────────────── hub I/O ───────────────────


class HubClient:
    def __init__(self, base_url: str, token: str | None, timeout: float = DEFAULT_TIMEOUT_SEC):
        self.base_url = base_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # keep-alive across the push loop — new connection per batch would
        # eat most of the wall-clock on a high-latency hub
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def health(self) -> dict:
        r = self._client.get("/health")
        r.raise_for_status()
        return r.json()

    def status(self, device_id: str) -> dict:
        r = self._client.get("/v1/sync/status", params={"device_id": device_id})
        r.raise_for_status()
        return r.json()

    def push(self, device_id: str, label: str | None, events: list[dict]) -> dict:
        payload = {"device_id": device_id, "label": label, "events": events}
        return self._post_with_retry("/v1/sync/push", payload)

    def push_state(self, device_id: str, label: str | None, kind: str, value) -> dict:
        payload = {"device_id": device_id, "label": label, "kind": kind, "value": value}
        return self._post_with_retry("/v1/sync/state", payload)

    def _post_with_retry(self, path: str, payload: dict) -> dict:
        # Retry only on 5xx and connection errors. Auth or schema failures
        # (4xx) are surfaced immediately — retrying won't help.
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = self._client.post(path, json=payload)
                if 500 <= r.status_code < 600:
                    last_exc = httpx.HTTPStatusError(
                        f"server {r.status_code}", request=r.request, response=r
                    )
                    time.sleep(min(2 ** (attempt - 1), 8))
                    continue
                r.raise_for_status()
                return r.json()
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                time.sleep(min(2 ** (attempt - 1), 8))
        assert last_exc is not None
        raise last_exc


# ─────────────────── orchestration ───────────────────


def _push_state_files(hub: "HubClient", device_id: str, label: str | None, dry_run: bool) -> dict:
    """Ship local state JSON files as opaque blobs.

    Missing files are silently skipped — not every device runs both the
    quota fetcher and the tuner, so absence is legitimate. Individual blob
    failures are logged but don't abort the whole push: the event data is
    already safe on the hub by the time we get here.
    """
    out: dict[str, str] = {}
    for kind, path in STATE_FILES.items():
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log(f"state read failed kind={kind} path={path}: {exc}")
            out[kind] = "read-error"
            continue
        if dry_run:
            out[kind] = "dry-run"
            continue
        try:
            hub.push_state(device_id, label, kind, value)
            out[kind] = "ok"
        except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
            _log(f"state push failed kind={kind}: {exc}")
            out[kind] = "push-error"
    return out


def _effective_cutoff(
    local_marks: dict, server_status: dict, hub_key: str, platform: str, force_full: bool
) -> str:
    """Pick the ts_utc cutoff for a platform.

    Prefers the later of (local watermark, server watermark). Server watermark
    seeds a fresh client pointed at an already-populated hub; local watermark
    dominates once the first push succeeds.
    """
    if force_full:
        return ""
    local = (local_marks.get(hub_key) or {}).get(platform) or ""
    server = (server_status.get("platforms") or {}).get(platform, {}).get("max_ts_utc") or ""
    return max(local, server)


def push_all(
    hub_url: str,
    token: str | None,
    *,
    db_path: Optional[str] = None,
    force_full: bool = False,
    dry_run: bool = False,
) -> dict:
    dev_id, label = load_device_id(None)
    hub_key = _hub_key(hub_url)

    conn = cloud_db.connect(db_path) if db_path else cloud_db.connect()
    hub = HubClient(hub_url, token)
    try:
        health = hub.health()
        _log(f"push start hub={hub_url} device={dev_id} schema_v={health.get('schema_version')}")

        status = hub.status(dev_id) if not force_full else {"platforms": {}}
        local_marks = load_push_watermarks()

        totals: dict[str, dict[str, int]] = {
            p: {"sent": 0, "inserted": 0, "batches": 0} for p in PLATFORMS
        }
        updated_marks = dict(local_marks)
        updated_marks.setdefault(hub_key, {})

        for platform in PLATFORMS:
            cutoff = _effective_cutoff(local_marks, status, hub_key, platform, force_full)
            stream = _iter_rows(conn, dev_id, platform, cutoff)
            for batch in _batched(stream, BATCH_SIZE):
                if dry_run:
                    totals[platform]["sent"] += len(batch)
                    totals[platform]["batches"] += 1
                    continue
                resp = hub.push(dev_id, label, batch)
                totals[platform]["sent"] += len(batch)
                totals[platform]["inserted"] += int(resp.get("inserted", 0))
                totals[platform]["batches"] += 1
                # Advance watermark only after a successful ack — if the next
                # batch fails we will resume correctly on retry.
                updated_marks[hub_key][platform] = batch[-1]["ts_utc"]
                save_push_watermarks(updated_marks)

        state_pushed = _push_state_files(hub, dev_id, label, dry_run)

        _log(f"push ok {json.dumps(totals)} state={state_pushed}")
        return {
            "device_id": dev_id,
            "hub": hub_url,
            "totals": totals,
            "state": state_pushed,
            "dry_run": dry_run,
        }
    finally:
        hub.close()
        conn.close()


def cmd_push(args: argparse.Namespace) -> int:
    hub = args.hub or os.environ.get("TOKENOMY_HUB_URL", "").strip()
    if not hub:
        print("[client] no hub: set TOKENOMY_HUB_URL or pass --hub", file=sys.stderr)
        return 2
    token = args.token or os.environ.get("TOKENOMY_SYNC_TOKEN", "").strip() or None

    t0 = time.time()
    try:
        result = push_all(
            hub, token, db_path=args.db_path, force_full=args.full, dry_run=args.dry_run
        )
    except httpx.HTTPStatusError as exc:
        print(f"[client] hub rejected: {exc.response.status_code} {exc.response.text}", file=sys.stderr)
        _log(f"FAIL http {exc.response.status_code}: {exc.response.text[:200]}")
        return 1
    except httpx.HTTPError as exc:
        print(f"[client] hub unreachable: {exc}", file=sys.stderr)
        _log(f"FAIL net: {exc}")
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"[client] failed: {exc}", file=sys.stderr)
        _log(f"FAIL: {exc}")
        return 1

    dt = time.time() - t0
    dry = " (dry-run)" if result["dry_run"] else ""
    per = result["totals"]
    state = result.get("state") or {}
    state_str = " ".join(f"{k}={v}" for k, v in state.items()) or "none"
    print(
        f"[client]{dry} device={result['device_id']} hub={result['hub']} "
        f"claude sent={per['claude']['sent']} inserted={per['claude']['inserted']} | "
        f"codex sent={per['codex']['sent']} inserted={per['codex']['inserted']} | "
        f"state: {state_str} | elapsed={dt:.1f}s"
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    hub = args.hub or os.environ.get("TOKENOMY_HUB_URL", "").strip()
    if not hub:
        print("[client] no hub: set TOKENOMY_HUB_URL or pass --hub", file=sys.stderr)
        return 2
    token = args.token or os.environ.get("TOKENOMY_SYNC_TOKEN", "").strip() or None
    dev_id, _ = load_device_id(None)
    client = HubClient(hub, token)
    try:
        health = client.health()
        status = client.status(dev_id)
    finally:
        client.close()
    print(json.dumps({"health": health, "status": status}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tokenomy-cloud-client")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_push = sub.add_parser("push", help="upload new local events to the hub")
    p_push.add_argument("--hub", default=None, help="hub base URL (overrides TOKENOMY_HUB_URL)")
    p_push.add_argument("--token", default=None, help="bearer token (overrides TOKENOMY_SYNC_TOKEN)")
    p_push.add_argument("--db-path", default=None, help="local DB path (default: env/home)")
    p_push.add_argument("--full", action="store_true", help="ignore watermarks; re-push everything")
    p_push.add_argument("--dry-run", action="store_true")
    p_push.set_defaults(func=cmd_push)

    p_status = sub.add_parser("status", help="print /health + /v1/sync/status for this device")
    p_status.add_argument("--hub", default=None)
    p_status.add_argument("--token", default=None)
    p_status.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
