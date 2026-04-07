#!/usr/bin/env bash
# firebreak SessionEnd cleanup — remove per-session caches and tmp files.
# Fails silent; never errors the session end.

set -u
CACHE_DIR="$HOME/.claude/firebreak"
[ -d "$CACHE_DIR" ] || { printf '{}\n'; exit 0; }

INPUT=$(cat 2>/dev/null || true)
SID=$(printf '%s' "$INPUT" | python -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get("session_id") or d.get("sessionId") or "")
except Exception:
    print("")
' 2>/dev/null)

if [ -n "$SID" ]; then
  rm -f "$CACHE_DIR/read-cache-${SID}.json" 2>/dev/null || true
else
  # No session id? Cleanup stale caches older than 7 days
  find "$CACHE_DIR" -maxdepth 1 -name 'read-cache-*.json' -mtime +7 -delete 2>/dev/null || true
fi

# Always clean tmp files older than 1 day
find "$CACHE_DIR/tmp" -type f -mtime +1 -delete 2>/dev/null || true

printf '{}\n'
exit 0
