#!/usr/bin/env bash
# firebreak hook D — log-grep preprocessor
# When Read targets a log file, replace content with: error/warn lines + last 50.
# Fails open on any error.

set -u

CACHE_DIR="$HOME/.claude/firebreak"
TMP_DIR="$CACHE_DIR/tmp"
mkdir -p "$TMP_DIR" 2>/dev/null || true

approve() { printf '{"decision":"approve"}\n'; exit 0; }

INPUT=$(cat 2>/dev/null) || approve
[ -z "$INPUT" ] && approve

# Parse path + user prompt (for !fulllog escape)
PARSED=$(printf '%s' "$INPUT" | python -c '
import json, sys, os
try:
    d = json.loads(sys.stdin.read())
    ti = d.get("tool_input") or {}
    p  = ti.get("file_path") or ti.get("path") or ""
    if p and os.path.exists(p):
        try: p = os.path.realpath(p)
        except Exception: pass
    # User prompt not always available; scan the raw payload for !fulllog marker
    raw = json.dumps(d)
    escape = "1" if "!fulllog" in raw else "0"
    print(f"{p}\t{escape}")
except Exception:
    print("")
' 2>/dev/null)

[ -z "$PARSED" ] && approve

FPATH=$(printf '%s' "$PARSED" | awk -F'\t' '{print $1}')
ESCAPE=$(printf '%s' "$PARSED" | awk -F'\t' '{print $2}')

[ -z "$FPATH" ] && approve
[ ! -f "$FPATH" ] && approve
[ "$ESCAPE" = "1" ] && approve

# Is it a log? Match *.log, _*.log, or path containing /log/ or /logs/
case "$FPATH" in
  *.log|*.log.*) : ;;
  */log/*|*/logs/*) : ;;
  *) approve ;;
esac

# Size check: <200 lines = pass through
LINES=$(python -c "
import sys
try:
    with open(sys.argv[1], 'rb') as f:
        n = sum(1 for _ in f)
    print(n)
except Exception:
    print(0)
" "$FPATH" 2>/dev/null)
[ -z "$LINES" ] && approve
if [ "$LINES" -lt 200 ] 2>/dev/null; then approve; fi

# Binary detection: first 1KB contains NUL byte?
IS_BINARY=$(python -c "
import sys
try:
    with open(sys.argv[1], 'rb') as f:
        chunk = f.read(1024)
    print('1' if b'\x00' in chunk else '0')
except Exception:
    print('0')
" "$FPATH" 2>/dev/null)
[ "$IS_BINARY" = "1" ] && approve

# Build filtered content
FILTERED=$(python - "$FPATH" <<'PY' 2>/dev/null
import sys, re
path = sys.argv[1]
pat = re.compile(r'(ERROR|WARN|FAIL|Exception|Traceback|CRITICAL|FATAL)', re.IGNORECASE)
try:
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
except Exception:
    sys.exit(1)

total = len(lines)
matches = [(i+1, l.rstrip()) for i, l in enumerate(lines) if pat.search(l)]
# cap at 200 match lines (keep last 200 to favor recent errors)
if len(matches) > 200:
    matches = matches[-200:]
tail = lines[-50:]

out = []
out.append(f"[firebreak] Log filtered: {total} total lines. Showing {len(matches)} matched (ERROR/WARN/FAIL/Exception/Traceback/CRITICAL/FATAL) + last 50 lines.")
out.append(f"[firebreak] Source: {path}")
out.append(f"[firebreak] To bypass and read the full log, include the token !fulllog in your next user prompt, or use Bash: cat \"{path}\".")
out.append("")
out.append("=== MATCHED LINES ===")
if matches:
    for ln, txt in matches:
        out.append(f"{ln}: {txt}")
else:
    out.append("(none)")
out.append("")
out.append("=== LAST 50 LINES ===")
for l in tail:
    out.append(l.rstrip())
print("\n".join(out))
PY
)

if [ -z "$FILTERED" ]; then approve; fi

# Return as block + reason (Claude sees the filtered content as the hook's reason).
# This is reliable across Claude Code versions; "modify" with file_path rewrite is not guaranteed.
printf '%s' "$FILTERED" | python -c '
import json, sys
content = sys.stdin.read()
print(json.dumps({"decision": "block", "reason": content}))
'
