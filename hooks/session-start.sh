#!/usr/bin/env bash
# tokenomy SessionStart hook — spawn tuner in background if stale. Fail-open, <50ms.
set +e
HOME_DIR="${TOKENOMY_HOME:-$HOME/.claude/tokenomy}"
APPLIED="$HOME_DIR/applied.json"
LOG="$HOME_DIR/tuner.log"
LOCK="$HOME_DIR/tuner.lock"
mkdir -p "$HOME_DIR" 2>/dev/null

# Lock check (stale after 5 min)
if [ -f "$LOCK" ]; then
  AGE=$(( $(date +%s 2>/dev/null) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
  [ "$AGE" -lt 300 ] && exit 0
fi

NEED=0
if [ ! -f "$APPLIED" ]; then
  NEED=1
  FLAG="--first-run"
else
  # 3-day staleness check (portable-ish)
  if [ -n "$(find "$APPLIED" -mtime +3 2>/dev/null)" ]; then
    NEED=1
    FLAG=""
  fi
fi

if [ "$NEED" = "1" ]; then
  echo $$ > "$LOCK" 2>/dev/null
  TOKENOMY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
  if command -v start >/dev/null 2>&1; then
    # Windows
    (cd "$TOKENOMY_DIR" && start /b python -m tuner.tuner $FLAG >> "$LOG" 2>&1) &
  else
    (cd "$TOKENOMY_DIR" && nohup python -m tuner.tuner $FLAG >> "$LOG" 2>&1 &)
  fi
fi
exit 0
