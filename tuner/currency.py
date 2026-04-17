"""Currency config for the tokenomy statusline.

Stores a single JSON file at `~/.claude/tokenomy/currency.json` shaped like:

    {"code": "EUR", "symbol": "\u20ac", "rate_to_usd": 0.92}

`rate_to_usd` is the multiplier applied to any USD cost to convert it to the
user's chosen currency (so 1 USD × 0.92 = 0.92 EUR). The default is USD at
rate 1.0 — no conversion, matches ccusage exactly.

Exposed via `python -m tuner.currency {set|show|reset}`, which in turn is
wired to the `/tokenomy-currency` slash command for easy user access.

Rates are intentionally static snapshots — the statusline renders every
second and we refuse to pay a network round-trip for it. Users who want
precision can pass `--rate <value>`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Tuple

CONFIG_DIR = os.path.expanduser("~/.claude/tokenomy")
CONFIG_PATH = os.path.join(CONFIG_DIR, "currency.json")

# Static fallback rates (USD → X) as of early 2026. Precision is not the goal;
# the statusline just needs "same order of magnitude in the user's currency".
DEFAULT_RATES = {
    "USD": (1.0, "$"),
    "EUR": (0.92, "\u20ac"),
    "GBP": (0.79, "\u00a3"),
    "RON": (4.58, "lei"),
    "JPY": (151.0, "\u00a5"),
    "CAD": (1.36, "C$"),
    "AUD": (1.52, "A$"),
    "CHF": (0.88, "Fr"),
    "PLN": (3.99, "z\u0142"),
}

DEFAULT = {"code": "USD", "symbol": "$", "rate_to_usd": 1.0}


def load_currency() -> dict:
    """Return the active currency config, falling back to USD on any error."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(DEFAULT)
        out = dict(DEFAULT)
        out.update(data)
        # Sanity: rate must be a positive number
        try:
            out["rate_to_usd"] = float(out["rate_to_usd"])
        except (TypeError, ValueError):
            return dict(DEFAULT)
        if out["rate_to_usd"] <= 0:
            return dict(DEFAULT)
        return out
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT)


def convert(usd: float, cfg: dict | None = None) -> Tuple[float, str]:
    """Return (converted_value, symbol) for a USD amount."""
    if cfg is None:
        cfg = load_currency()
    return usd * float(cfg["rate_to_usd"]), str(cfg["symbol"])


def save_currency(code: str, rate: float | None = None, symbol: str | None = None) -> dict:
    code = code.upper().strip()
    if rate is None or symbol is None:
        if code not in DEFAULT_RATES:
            raise SystemExit(
                f"unknown currency '{code}'. Known: {', '.join(sorted(DEFAULT_RATES))}. "
                f"Pass --rate <value> and --symbol <text> to add a new one."
            )
        default_rate, default_symbol = DEFAULT_RATES[code]
        rate = float(rate) if rate is not None else default_rate
        symbol = symbol if symbol is not None else default_symbol
    else:
        rate = float(rate)
    cfg = {"code": code, "symbol": symbol, "rate_to_usd": rate}
    os.makedirs(CONFIG_DIR, exist_ok=True)
    # Atomic write: crash mid-write used to leave a zero-byte file, which
    # load_currency silently reset to USD — losing the user's choice.
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".currency.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return cfg


def reset_currency() -> None:
    if os.path.exists(CONFIG_PATH):
        os.unlink(CONFIG_PATH)


def _cmd_show() -> int:
    cfg = load_currency()
    print(f"currency: {cfg['code']} ({cfg['symbol']})  1 USD = {cfg['rate_to_usd']} {cfg['code']}")
    print(f"file:     {CONFIG_PATH}")
    return 0


def _cmd_set(code: str, rate: float | None, symbol: str | None) -> int:
    cfg = save_currency(code, rate=rate, symbol=symbol)
    print(f"OK: statusline now displays {cfg['code']} ({cfg['symbol']}). 1 USD = {cfg['rate_to_usd']} {cfg['code']}.")
    print("Open a new Claude Code session to see the change.")
    return 0


def _cmd_reset() -> int:
    reset_currency()
    print("OK: currency reset to USD (ccusage default).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="tuner.currency",
        description="Switch the tokenomy statusline to a different currency.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_set = sub.add_parser("set", help="Set active currency (e.g. EUR).")
    p_set.add_argument("code", help="Currency code: USD, EUR, GBP, RON, JPY, CAD, AUD, CHF, PLN — or any 3-letter code if you also pass --rate and --symbol.")
    p_set.add_argument("--rate", type=float, default=None, help="Override rate: how many <code> per 1 USD.")
    p_set.add_argument("--symbol", type=str, default=None, help="Override symbol shown before amounts.")

    sub.add_parser("show", help="Print the active currency config.")
    sub.add_parser("reset", help="Revert to USD.")

    args = ap.parse_args(argv)
    if args.cmd == "set":
        return _cmd_set(args.code, args.rate, args.symbol)
    if args.cmd == "show":
        return _cmd_show()
    if args.cmd == "reset":
        return _cmd_reset()
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
