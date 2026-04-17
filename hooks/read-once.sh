#!/usr/bin/env bash
# tokenomy hook C — read-once
# Blocks redundant Read tool calls on unchanged files within a session.
# Fails open on any error (never blocks a legitimate read).

# Dropped `set -u` — approve() is the fail-open escape hatch and every
# variable in this hook is already defensively defaulted or guarded.

CACHE_DIR="$HOME/.claude/tokenomy"
mkdir -p "$CACHE_DIR" 2>/dev/null || true

approve() { printf '{"decision":"approve"}\n'; exit 0; }

INPUT=$(cat 2>/dev/null) || approve
[ -z "$INPUT" ] && approve

# Parse stdin JSON: sid, path, offset, limit (tab-separated)
PARSED=$(printf '%s' "$INPUT" | python -c '
import json, sys, os
try:
    d = json.loads(sys.stdin.read())
    sid = d.get("session_id") or d.get("sessionId") or "nosession"
    ti = d.get("tool_input") or {}
    p  = ti.get("file_path") or ti.get("path") or ""
    off = ti.get("offset") or ""
    lim = ti.get("limit") or ""
    if p and os.path.exists(p):
        try: p = os.path.realpath(p)
        except Exception: pass
    p = p.replace("\\", "/")
    print(f"{sid}\t{p}\t{off}\t{lim}")
except Exception:
    print("")
' 2>/dev/null)

[ -z "$PARSED" ] && approve

SID=$(printf '%s' "$PARSED" | awk -F'\t' '{print $1}')
FPATH=$(printf '%s' "$PARSED" | awk -F'\t' '{print $2}')
OFFSET=$(printf '%s' "$PARSED" | awk -F'\t' '{print $3}')
LIMIT=$(printf '%s' "$PARSED" | awk -F'\t' '{print $4}')

[ -z "$FPATH" ] && approve
[ ! -f "$FPATH" ] && approve

# File identity signature: mtime + size. Previously mtime alone blocked
# legitimate re-reads after same-second edits (Windows NTFS / fast editors)
# because integer-second mtime hadn't advanced. Adding size substantially
# narrows the false-negative window at no real cost.
SIG=$(python -c "import os,sys; st=os.stat(sys.argv[1]); print(f'{int(st.st_mtime)}:{st.st_size}')" "$FPATH" 2>/dev/null) || approve

CACHE="$CACHE_DIR/read-cache-${SID}.json"
KEY="${FPATH}|${OFFSET}|${LIMIT}"

# Note on the cache write below: `json.dump` has no timeout. On slow disks /
# NFS / AV scan, the 5s hook timeout can be consumed by the write. The
# `except Exception: pass` keeps writes best-effort but won't catch a hang;
# operators on such filesystems should disable this hook entirely.
RESULT=$(python - "$CACHE" "$KEY" "$SIG" <<'PY' 2>/dev/null
import json, sys, os, time
cache_path, key, sig = sys.argv[1], sys.argv[2], sys.argv[3]
data = {}
try:
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
except Exception:
    data = {}

prev = data.get(key)
if isinstance(prev, dict) and prev.get("sig") == sig:
    print("BLOCK\t" + prev.get("ts", "earlier"))
else:
    data[key] = {"sig": sig, "ts": time.strftime("%H:%M:%S")}
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass
    print("APPROVE")
PY
)

case "$RESULT" in
  BLOCK*)
    TS=$(printf '%s' "$RESULT" | awk -F'\t' '{print $2}')
    REASON="[tokenomy] You already read this file at ${TS} this session and its mtime has not changed. Use the version already in your context. If you need a fresh view, edit the file or use Bash (cat/head/tail)."
    printf '%s' "$REASON" | python -c 'import json,sys; print(json.dumps({"decision":"block","reason":sys.stdin.read()}))'
    exit 0
    ;;
  *)
    approve
    ;;
esac
