#!/usr/bin/env python3
"""DarkHorse Kalshi/Polymarket arbitrage scan (PC port; added live
Oct 29, 2026 at Michael's request — he is limited at some sportsbooks).

Same Arbitrage finder as dh_arbitrage.py, but keeps ONLY pairs with
>= 1 leg on Kalshi or Polymarket US. Polymarket International NEVER
counts. Profit > 3% displayed (3% edge bar, Oct 31, 2026). Dedupe vs data/arbitrage-state.json:
a pair already in the main arbitrage state is not delivered here.
Smaller Bets pass with the same rules (filter preserved).
State: data/kalshi-poly-arb-state.json.
Banked: data/kalshi-poly-arb-<date>.md.
"""
import argparse
import sys

from dh_arbitrage import changed, displayed_profit, enrich
from dh_pairs import pair_key, run_pair_scan
from state import load_state

EXCHANGE_HINTS = ("kalshi", "polymarket us")


def has_exchange_leg(pair):
    for leg in (pair["leg1"], pair["leg2"]):
        book = (leg.get("book") or "").lower()
        if any(h in book for h in EXCHANGE_HINTS):
            return True
    return False


def qualify(pair):
    p = displayed_profit(pair)
    return p is not None and p > 3.0 and has_exchange_leg(pair)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--no-smaller", action="store_true")
    args = ap.parse_args()
    # Cross-scan dedupe is enforced inside run_pair_scan's state diff
    # only for this scan's own state; pre-load the main arb keys and
    # wrap qualify so already-covered pairs never qualify here.
    main_state = load_state("arbitrage")
    covered = set(main_state.get("entries", {}).keys())

    def qualify_deduped(pair):
        return qualify(pair) and pair_key("kalshi-poly-arb", pair) not in covered

    return run_pair_scan("kalshi-poly-arb", "arbitrage", qualify_deduped,
                         enrich, changed=changed, dump=args.dump,
                         smaller=not args.no_smaller,
                         deliver_label="PAIR")


if __name__ == "__main__":
    sys.exit(main())
