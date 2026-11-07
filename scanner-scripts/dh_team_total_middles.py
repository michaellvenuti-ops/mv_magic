#!/usr/bin/env python3
"""Team-total middles at key 20 — GATE STUB (PC port).

DarkHorse has NO team totals anywhere. Verified definitively on
Oct 31, 2026: the Market Type filter trees in the Profit Boost, Low
Holds and Arbitrage finders are node-for-node identical down to level
3 with NO "Team Totals" node at any level; the Browse Odds Table
market selector offers only Moneyline/Spreads, Totals and Player
Props; game detail pages across NFL/NBA/NHL/soccer/NCAAF show only a
single game total, never split per team. So no key-20 team-total pair
can be sourced from DarkHorse.

This stub therefore, once per run:
  1. opens the Middles finder and checks the market filter tree text
     for a team-totals node,
  2. while absent: writes data/team-total-middles-state.json with
     gate_notified: true and prints NOTHING (silent — the live scan
     sent its one-time gate notice on Oct 31, 2026),
  3. if a team-totals node EVER appears: prints a loud notice that
     team totals are now readable, so the real scan can be enabled —
     and still flags nothing itself (enabling is a human decision).

Target shapes, for when it ever unlocks: straddle Over <= 19.5 +
Under >= 20.5 (both win at exactly 20) and flat-20 win+push pairings;
both legs -170 cap; grading $100/leg.
"""
import json
import sys

from config import DATA_DIR
from dh_common import FINDER_URLS, open_session, signed_in
from odds_common import now_stamp

STATE_PATH = DATA_DIR / "team-total-middles-state.json"


def main():
    pw, ctx = open_session()
    try:
        page = ctx.new_page()
        if not signed_in(page, FINDER_URLS["middles"]):
            print("DarkHorse check unavailable (signed out) — "
                  "team-total gate check skipped.")
            return 0
        text = page.locator("body").inner_text(timeout=20000).lower()
        # Best-effort: expand the Market Type filter if present.
        try:
            loc = page.get_by_text("Market Type", exact=False)
            if loc.count():
                loc.first.click()
                page.wait_for_timeout(2000)
                text = page.locator("body").inner_text(
                    timeout=20000).lower()
        except Exception:  # noqa: BLE001
            pass
        has_node = "team total" in text
        state = {"entries": {}, "gate_notified": True,
                 "last_check": now_stamp(),
                 "team_totals_node_present": has_node}
        with open(STATE_PATH, "w") as fh:
            json.dump(state, fh, indent=1)
        if has_node:
            print("NOTICE: a team-totals node now appears in DarkHorse's "
                  "market tree — team totals may be readable. Tell Chet "
                  "so the key-20 team-total middles scan can be enabled. "
                  "This stub flags nothing itself.")
        # Absent: silent by design (gate already notified on the live side).
        return 0
    finally:
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
