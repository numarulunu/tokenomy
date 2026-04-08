---
description: Switch the tokenomy statusline to a different currency (EUR, GBP, RON, etc.)
---

The user wants to change the currency shown in their tokenomy statusline.
Their arguments: `$ARGUMENTS`

Parse the arguments and run exactly ONE of the following via the Bash tool:

- `python -m tuner.currency set <CODE>` — when the user gives a known code (USD, EUR, GBP, RON, JPY, CAD, AUD, CHF, PLN).
- `python -m tuner.currency set <CODE> --rate <N> --symbol <SYM>` — when the user supplies a custom code, rate, or symbol.
- `python -m tuner.currency show` — when the user asks what's currently set (e.g. "show", "status", no args).
- `python -m tuner.currency reset` — when the user asks to reset / revert / go back to USD.

Run the command from the tokenomy plugin root: `${CLAUDE_PLUGIN_ROOT}`.

If the command succeeds, report:
1. **Recommended:** the new currency is live after the next Claude Code restart.
2. **Next step:** one sentence telling them to open a fresh session to see the change.

If it fails (unknown code without --rate), show the error line verbatim and suggest the `set <CODE> --rate <N> --symbol <SYM>` form.

Do not edit any files yourself — the Python CLI is the only writer.
