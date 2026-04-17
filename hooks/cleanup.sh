#!/usr/bin/env bash
# tokenomy SessionEnd cleanup — remove per-session caches and tmp files.
# Fails silent; never errors the session end.

# Dropped `set -u` — every variable in this hook is defensively defaulted,
# and an unset-var abort would prevent the fail-open `printf '{}\n'` tail.
CACHE_DIR="$HOME/.claude/tokenomy"
[ -d "$CACHE_DIR" ] || { printf '{}\n'; exit 0; }

INPUT=$(cat 2>/dev/null || true)
SID=$(printf '%s' "${INPUT:-}" | python -c '
import json, re, sys
try:
    d = json.loads(sys.stdin.read())
    sid = d.get("session_id") or d.get("sessionId") or ""
    # Strip any path separators or traversal sequences so the SID cannot
    # escape CACHE_DIR when composed into a file path below.
    print(re.sub(r"[^A-Za-z0-9_-]", "", sid))
except Exception:
    print("")
' 2>/dev/null)

if [ -n "${SID:-}" ]; then
  rm -f "$CACHE_DIR/read-cache-${SID}.json" 2>/dev/null || true
else
  # No session id → purge read-caches older than 7 days via Python (GNU
  # `find -mtime` is unreliable on Git Bash under Windows drive paths).
  python - "$CACHE_DIR" <<'PY' 2>/dev/null || true
import glob, os, sys, time
cache_dir = sys.argv[1]
cutoff = time.time() - 86400 * 7
for p in glob.glob(os.path.join(cache_dir, "read-cache-*.json")):
    try:
        if os.path.getmtime(p) < cutoff:
            os.unlink(p)
    except OSError:
        pass
PY
fi

# Always clean tmp files older than 1 day (same portability story).
python - "$CACHE_DIR/tmp" <<'PY' 2>/dev/null || true
import os, sys, time
tmp = sys.argv[1]
if os.path.isdir(tmp):
    cutoff = time.time() - 86400
    for root, _dirs, files in os.walk(tmp):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.unlink(p)
            except OSError:
                pass
PY

printf '{}\n'
exit 0
