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

from dh_common import PCT_RE, _book_in
from dh_pairs import run_pair_scan

MIN_PROFIT_PCT = 3.0


def displayed_profit(pair):
    """The tool's profit % for the pair: the % in the row's payout /
    profit summary cell — the cell carrying a $ profit figure and a
    % but NO book (e.g. "$6.35\\n0.60%").

    NEVER the max % on the row (the original calibration): exchange
    bet cells carry the contract's win % in brackets ("-1684.1 [94%]
    $1,000 Kalshi"), and max() read that 94.0 as the pair's profit.
    Live probe 2026-11-10: that row's pcts were [94.0, 0.6]; the
    summary cell's 0.60% matched the from-prices complementary
    profit (0.61%). Book-only rows carry exactly one % — the same
    summary-cell figure (probe: matched computed profit to rounding
    on 15 of 15 rows). A summary cell carrying two scenario %s
    yields the MINIMUM — the worst case is the guaranteed figure.
    Fallback when no summary cell parses: min of the row's %s
    (book rows hold exactly one; exchange rows hold the win % plus
    the true profit, and the profit is the smaller in practice)."""
    for cell in pair.get("cells") or []:
        if "%" not in cell or "$" not in cell:
            continue
        if _book_in(cell):
            continue
        pcts = [float(p) for p in PCT_RE.findall(cell)]
        if pcts:
            return min(pcts)
    return min(pair["pcts"]) if pair["pcts"] else None


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
                         smaller=not args.no_smaller,
                         deliver_label="PAIR")


if __name__ == "__main__":
    sys.exit(main())
