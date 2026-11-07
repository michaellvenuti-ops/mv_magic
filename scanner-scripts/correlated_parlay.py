#!/usr/bin/env python3
"""Correlated-parlay scan (PC port; Spanky batch 4 closes gap G1).

Shapes (Peabody / Pasko): on today's NFL/FBS games with a favorite
laying 7+, top 12 by spread:
  - Peabody: big favorite + OVER (their scoring keeps the total live).
  - Pasko: big favorite + a total the market has set mismatched to the
    spread (total low relative to the spread — the classic
    favorite-and-under-the-inflated-number shape is NOT used; per the
    live scan this port flags favorite + over only when the total is
    low vs the spread, i.e. the 'mismatched total' case).
NEVER favorite+under as a standalone, NEVER underdog+over.

Pricing: the live scan prices via FanDuel's public SGP builder. That
builder is not reliably readable outside a browser session on this PC
port either, so this script:
  1. builds the candidate list from the ScoresAndOdds public board,
  2. attempts a plain requests read of FanDuel's public odds page,
  3. when no SGP price can be read, BANKS the candidates with
     "pricing unavailable" and flags nothing — it NEVER invents an
     SGP price.
FLAG RULE (when a price IS read): the book's SGP price must pay at
least as well as independent fair = product of the legs' implied
probabilities (correlation raises the true joint probability, so
independent fair is the conservative bar). Tread-lightly warning
rides every flag list: books limit correlated-parlay winners fast.

State: data/correlated-parlay-state.json. Banked:
data/correlated-parlay-<date>.md.
"""
import argparse
import sys

import requests

from odds_common import (bank, decimal_odds, implied_pct, now_stamp,
                         today_str)
from so_board import BoardUnreadable, load_board, medians
from state import load_state, save_state

SCAN = "correlated-parlay"
MIN_SPREAD = 7.0
TOP_N = 12
TREAD = ("Tread lightly: correlated parlays are the fastest way to "
         "get limited at a book (Spanky warning).")


def independent_fair(spread_price, total_price):
    """Fair combined decimal odds from independent leg probabilities."""
    p1, p2 = implied_pct(spread_price), implied_pct(total_price)
    if p1 is None or p2 is None:
        return None
    return 100.0 / ((p1 / 100.0) * (p2 / 100.0))


def try_fanduel_sgp_price(game_desc):
    """FanDuel's public SGP builder is login/JS-gated in practice.
    Returns None (pricing unavailable) unless a future public endpoint
    is configured here — never guesses a price."""
    try:
        resp = requests.get("https://sportsbook.fanduel.com/",
                            headers={"User-Agent": "Mozilla/5.0"},
                            timeout=15)
        _ = resp.status_code
    except requests.RequestException:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()
    state = load_state(SCAN)
    games = []
    for league in ("NFL", "NCAAF"):
        try:
            lg, _ = load_board(league)
            games += lg
        except BoardUnreadable as e:
            bank(SCAN, [f"## {now_stamp()} — {league} unreadable: {e}"])
    candidates = []
    for game in games:
        med_spread, med_total = medians(game)
        if med_spread is None or med_total is None:
            continue
        if abs(med_spread) < MIN_SPREAD:
            continue
        fav_home = med_spread < 0
        # Mismatched total: total low relative to spread magnitude.
        mismatched = med_total < 6.0 * abs(med_spread)
        candidates.append({"game": game, "spread": med_spread,
                           "total": med_total, "fav_home": fav_home,
                           "shape": "Peabody" if not mismatched else "Pasko"})
    candidates.sort(key=lambda c: -abs(c["spread"]))
    candidates = candidates[:TOP_N]
    flags = []
    lines = [f"## {now_stamp()} — {len(candidates)} candidates "
             f"(favorite 7+, top {TOP_N})", TREAD]
    for cand in candidates:
        g = cand["game"]
        desc = f"{g['away']} @ {g['home']} ({g['league']})"
        fav = g["home"] if cand["fav_home"] else g["away"]
        sgp_price = try_fanduel_sgp_price(desc)
        if sgp_price is None:
            lines.append(f"candidate (pricing unavailable): {desc} — "
                         f"{fav} {cand['spread']} + Over {cand['total']} "
                         f"[{cand['shape']}] — FanDuel SGP builder not "
                         f"readable this run; banked only, NOT flagged.")
            continue
        # A read price path (future): legs priced at -110 each unless
        # the builder supplied leg prices alongside; see README.
        fair = independent_fair(-110, -110)
        if fair is not None and decimal_odds(sgp_price) >= fair:
            key = f"{desc} | {today_str()}"
            if key not in state["entries"]:
                flag = {"game": desc, "shape": cand["shape"],
                        "sgp_price": sgp_price, "fair_decimal": fair,
                        "first_flagged": now_stamp()}
                state["entries"][key] = flag
                flags.append(flag)
                lines.append(f"FLAG: {desc} {flag['shape']} SGP "
                             f"{sgp_price:+d} vs independent fair "
                             f"{fair:.2f}")
    save_state(SCAN, state)
    bank(SCAN, lines)
    for f in flags:
        print(f"CORRELATED PARLAY FLAG: {f['game']} [{f['shape']}] SGP "
              f"{f['sgp_price']:+d} >= independent fair {f['fair_decimal']:.2f}")
    if not flags:
        print(f"correlated-parlay: {len(candidates)} candidates banked; "
              f"no priced flags (SGP pricing unavailable or below fair).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
