#!/usr/bin/env bash
# tokenomy SessionStart hook — spawn tuner in background if stale. Fail-open, <50ms.
set +e
HOME_DIR="${TOKENOMY_HOME:-$HOME/.claude/tokenomy}"
APPLIED="$HOME_DIR/applied.json"
LOG="$HOME_DIR/tuner.log"
LOCKDIR="$HOME_DIR/tuner.lock.d"
mkdir -p "$HOME_DIR" 2>/dev/null

# Decide if we even need to run (cheap; before touching the lock).
NEED=0
FLAG=""
if [ ! -f "$APPLIED" ]; then
  NEED=1
  FLAG="--first-run"
elif [ -n "$(find "$APPLIED" -mtime +3 2>/dev/null)" ]; then
  NEED=1
fi
[ "$NEED" = "1" ] || exit 0

# Atomic acquire via mkdir (POSIX-portable, no TOCTOU window).
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  AGE=$(( $(date +%s 2>/dev/null) - $(stat -c %Y "$LOCKDIR" 2>/dev/null || echo 0) ))
  if [ "$AGE" -lt 300 ]; then
    exit 0
  fi
  # Stale lock — clear it and try once more. Still fail-open on any error.
  rm -rf "$LOCKDIR" 2>/dev/null
  mkdir "$LOCKDIR" 2>/dev/null || exit 0
fi
# The hook shell only holds the lock long enough to spawn; the tuner clears
# its own lock in a finally block. trap covers crash-on-spawn.
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

TOKENOMY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Detach unconditionally; redirect handled by the outer subshell so the
# spawned python inherits already-redirected handles regardless of platform.
(
  cd "$TOKENOMY_DIR" || exit 0
  nohup python -m tuner.tuner $FLAG </dev/null >> "$LOG" 2>&1 &
) >/dev/null 2>&1
disown 2>/dev/null

exit 0
