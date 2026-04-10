#!/usr/bin/env bash
# tokenomy SessionStart hook — spawn tuner in background if stale. Fail-open, <50ms.
# Keep TOKENOMY_VERSION in sync with .claude-plugin/plugin.json — test_version_sync guards this.
set +e
TOKENOMY_VERSION="0.4.0"
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
elif ! grep -q "\"version\": \"$TOKENOMY_VERSION\"" "$APPLIED" 2>/dev/null; then
  # Version mismatch — tuner code is newer than applied.json. Force a run so
  # existing users upgrading from an older tokenomy get the new merge/format
  # applied immediately instead of waiting up to 3 days for the staleness gate.
  NEED=1
elif [ "$(python -c "import os,time; print('stale' if time.time()-os.path.getmtime('$APPLIED') > 86400*3 else '')" 2>/dev/null)" = "stale" ]; then
  # Python staleness gate — GNU find's -mtime misbehaves on Git Bash / MSYS
  # under Windows drive-letter paths, so we use Python (already a hard dep).
  NEED=1
fi
[ "$NEED" = "1" ] || exit 0

# Atomic acquire via mkdir (POSIX-portable, no TOCTOU window).
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  # PID-based stale check: if the locking process is still alive, exit.
  # Fail-open if pid file missing or kill -0 errors.
  LOCK_PID=$(cat "$LOCKDIR/pid" 2>/dev/null | head -1)
  if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
    exit 0  # process alive — lock is valid
  fi
  # Process dead or no PID file — stale lock. Clear and retry once.
  rm -rf "$LOCKDIR" 2>/dev/null
  mkdir "$LOCKDIR" 2>/dev/null || exit 0
fi
# NO trap on EXIT: the tuner's Python `finally` block is the sole lock
# release point. A bash trap would clear LOCKDIR the instant this hook shell
# exits — before the backgrounded tuner has acquired its guard — opening a
# race window where a second session-start spawns a second tuner.

if [ "$FLAG" = "--first-run" ]; then
  echo "[tokenomy] First run. Review $HOME_DIR/consent-summary.txt for details."
fi

TOKENOMY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Detach unconditionally; redirect handled by the outer subshell so the
# spawned python inherits already-redirected handles regardless of platform.
(
  cd "$TOKENOMY_DIR" || exit 0
  nohup python -m tuner.tuner $FLAG </dev/null >> "$LOG" 2>&1 &
  echo $! > "$LOCKDIR/pid" 2>/dev/null
) >/dev/null 2>&1
disown 2>/dev/null

exit 0
