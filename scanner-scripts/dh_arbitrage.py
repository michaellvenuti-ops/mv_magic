#!/usr/bin/env python3
"""DarkHorse Arbitrage finder (PC port).

Rules (identical to Chet's hourly scan):
  - profit > 3% as DISPLAYED by the tool (never recomputed;
  3% edge bar set by Michael Oct 31, 2026 — strict OVER 3.00%),
  - both legs placeable: NJ books, or Kalshi / Polymarket US /
    ProphetX (Polymarket Intl., Fliff, Bet105 never count),
  - $1,000 primary framing, Sportsbook Target untouched (Any Book),
  - compact reporting: qualifying rows only,
  - deliverable = NEW pair, or profit +1pt, or a leg +10c better,
  - Arb Risk flag kept on every pair that carries it,
  - Smaller Bets pass (see dh_pairs.py) with the same thresholds.
State: data/arbitrage-state.json. Banked: data/arbitrage-<date>.md.
Read-only; never places a bet.
"""
import argparse
import sys

from dh_pairs import run_pair_scan

MIN_PROFIT_PCT = 3.0


def displayed_profit(pair):
    """The tool's profit % is the percent attached to the pair row;
    calibration: the largest % on the row is the profit figure on
    DarkHorse's arbitrage table. None when no % parsed."""
    return max(pair["pcts"]) if pair["pcts"] else None


def qualify(pair):
    p = displayed_profit(pair)
    return p is not None and p > MIN_PROFIT_PCT


def enrich(pair):
    return {"profit_pct": displayed_profit(pair)}


def changed(old, new):
    if new["profit_pct"] is not None and old.get("profit_pct") is not None:
        if new["profit_pct"] - old["profit_pct"] >= 1.0:
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
    return run_pair_scan("arbitrage", "arbitrage", qualify, enrich,
                         changed=changed, dump=args.dump,
                         smaller=not args.no_smaller)


if __name__ == "__main__":
    sys.exit(main())
