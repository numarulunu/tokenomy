#!/usr/bin/env bash
# tokenomy SessionEnd hook — append one summary line to sessions.jsonl. Fail-open.
set +e
HOME_DIR="${TOKENOMY_HOME:-$HOME/.claude/tokenomy}"
mkdir -p "$HOME_DIR" 2>/dev/null
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)"
SID="${CLAUDE_SESSION_ID:-unknown}"
PROJ="${CLAUDE_PROJECT_NAME:-unknown}"
printf '{"ts":"%s","sid":"%s","project":"%s"}\n' "$TS" "$SID" "$PROJ" >> "$HOME_DIR/sessions.jsonl" 2>/dev/null
exit 0
