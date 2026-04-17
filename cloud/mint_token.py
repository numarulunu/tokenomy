"""Generate a bearer token for TOKENOMY_SYNC_TOKEN.

Usage:
    python -m cloud.mint_token

Prints a URL-safe random string to stdout. Copy it into the Coolify
secret env (TOKENOMY_SYNC_TOKEN) and into the local client environment.
Rotating: mint a new token, update server env, update every client —
old tokens stop working the moment the server restarts.
"""
from __future__ import annotations

import secrets
import sys


def mint(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def main(argv: list[str] | None = None) -> int:
    n = 32
    if argv and len(argv) >= 1:
        try:
            n = int(argv[0])
        except ValueError:
            print(f"usage: python -m cloud.mint_token [nbytes]", file=sys.stderr)
            return 2
    print(mint(n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
