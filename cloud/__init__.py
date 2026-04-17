"""Tokenomy cloud — SQLite-backed sync + dashboard backend.

Same codebase runs two deploy modes:
  - local:   KONTEXT_HOST=127.0.0.1, auth bypassed, DB at ~/.claude/tokenomy/tokenomy.db
  - hub:     KONTEXT_HOST=0.0.0.0,   bearer-token required, DB at /data/tokenomy.db

Devices push normalized event rows to /v1/sync/push; the dashboard queries
aggregates out of the same DB.
"""
VERSION = "0.1.0"
