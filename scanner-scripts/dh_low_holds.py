#!/usr/bin/env python3
"""DarkHorse Low Holds finder (PC port; added live Oct 31, 2026).

A low hold = a two-leg hedged pair whose combined hold is at or near
0% — covering all outcomes costs almost nothing. Uses: churning
volume / clearing qualifying bets / preserving bankroll. A NEGATIVE
hold (both outcome payouts positive) is arb-like and labeled
"NEGATIVE HOLD / ARB-LIKE". A small POSITIVE hold is NOT a guaranteed
profit — it is a near-zero-cost hedge, and every output says so.

TOOL-LABEL NOTE (verified live Oct 31, 2026): the Low Holds tool shows
NO Hold % column and no Hold filter. Its table % is labeled
"Conversion" in the bet slip. This port uses the displayed Conversion
% <= 1.00% as the threshold proxy and copies it EXACTLY (never
recomputes it); pairs whose two displayed outcome payouts are both
positive also qualify regardless of the %.

Rules: $1,000 primary; both legs NJ-placeable or Kalshi / Polymarket
US / ProphetX; deliverable = NEW, or the % improved (fell) 0.50pt+, or
a leg improved 10c+; Smaller Bets pass (see dh_pairs.py).
State: data/low-holds-state.json. Banked: data/low-holds-<date>.md.
"""
import argparse
import sys

from dh_pairs import run_pair_scan

MAX_PCT = 1.00
HONESTY = ("A small positive hold is a near-zero-cost hedge for "
           "churning volume / clearing qualifying bets — NOT a "
           "guaranteed profit.")


def displayed_pct(pair):
    return min(pair["pcts"]) if pair["pcts"] else None


def both_pay(pair):
    """Both outcome payouts positive, when the row shows them
    (calibration: payout $ figures ride in the pair's stake list when
    the slip columns parse; otherwise rely on the % rule)."""
    return pair.get("both_positive") is True


def qualify(pair):
    p = displayed_pct(pair)
    return (p is not None and p <= MAX_PCT) or both_pay(pair)


def enrich(pair):
    p = displayed_pct(pair)
    entry = {"hold_pct": p, "pct_label": "Conversion (tool shows no "
                                         "Hold % column)"}
    if both_pay(pair):
        entry["negative_hold"] = True
    return entry


def changed(old, new):
    if new["hold_pct"] is not None and old.get("hold_pct") is not None:
        if old["hold_pct"] - new["hold_pct"] >= 0.50:
            return True
    for leg in ("leg1", "leg2"):
        try:
            if int(new[leg]["price"]) - int(old[leg]["price"]) >= 10:
                return True
        except (TypeError, ValueError):
            pass
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--no-smaller", action="store_true")
    args = ap.parse_args()
    print(HONESTY)
    return run_pair_scan("low-holds", "low_holds", qualify, enrich,
                         changed=changed, dump=args.dump,
                         smaller=not args.no_smaller)


if __name__ == "__main__":
    sys.exit(main())
