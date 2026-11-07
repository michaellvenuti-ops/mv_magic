#!/usr/bin/env python3
"""Public-fade scan (PC port; Spanky batch 4, Bruno Ep 37).

Flags today's NFL/FBS games where >= 90% of public TICKETS sit on one
side of the spread or total; the FADE is the other side (Bruno's
10-to-1 rule). HONESTY GUARD, printed with every flag list: Bruno's
~85% cold-side rate is his book's experience, NOT a verified rate.

Splits come from ScoresAndOdds' public splits when readable (see
so_board.parse_splits). GATE RULE (same as live): if no public splits
source validates, print the gate message ONCE, write
data/public-fade-gate-notified, and stay silent on later runs until
the gate file is removed.

State: data/public-fade-state.json ({"entries", "meta"}); banked list
data/public-fade-<date>.md. Alert-only: NEW flags print.
"""
import argparse
import sys
from pathlib import Path

from config import DATA_DIR
from odds_common import bank, now_stamp, today_str
from so_board import BoardUnreadable, load_board, parse_splits
from state import load_state, save_state

SCAN = "public-fade"
TICKET_THRESHOLD = 90.0
GATE_FILE = Path(DATA_DIR) / "public-fade-gate-notified"
HONESTY = ("HONESTY GUARD: Bruno's ~85% cold-side rate is his book's "
           "experience, NOT a verified rate.")


def splits_for_game(html_text, game):
    """Per-game splits are not reliably attributable from the public
    board snapshot; only a board-level splits block is attempted.
    Returns None when unattributable (gate rule applies)."""
    return parse_splits(html_text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()
    state = load_state(SCAN)
    games, html_by_league = [], {}
    for league in ("NFL", "NCAAF"):
        dump = (DATA_DIR / f"dump-public-fade-{league}-{today_str()}.html"
                if args.dump else None)
        try:
            lg, html_text = load_board(league, dump_path=dump)
            games += lg
            html_by_league[league] = html_text
        except BoardUnreadable as e:
            bank(SCAN, [f"## {now_stamp()} — {league} board unreadable: {e}"])
    splits_ok = any(parse_splits(h) for h in html_by_league.values())
    if not splits_ok:
        if not GATE_FILE.exists():
            GATE_FILE.write_text(now_stamp())
            print("PUBLIC-FADE GATE: no public splits source validated "
                  "on ScoresAndOdds this run. The scan cannot flag "
                  "90%-ticket games without a splits source. This "
                  "message prints once; later runs stay silent until "
                  "data/public-fade-gate-notified is removed.")
        bank(SCAN, [f"## {now_stamp()} — GATE: no public splits source; "
                    f"{len(games)} games read, no flags possible."])
        return 0
    # Splits validated at board level. Per-game attribution from the
    # public snapshot is best-effort; unattributable games are skipped,
    # never guessed.
    new_flags = []
    for game in games:
        sp = game.get("splits")
        if not sp:
            continue
        for market, pct_key, sides in (
                ("spread", "bets_spread_home_pct",
                 (game["home"], game["away"])),
                ("total", "bets_total_over_pct", ("Over", "Under"))):
            pct = sp.get(pct_key)
            if pct is None:
                continue
            hot_side, hot_pct = None, None
            if pct >= TICKET_THRESHOLD:
                hot_side, hot_pct = sides[0], pct
            elif (100 - pct) >= TICKET_THRESHOLD:
                hot_side, hot_pct = sides[1], 100 - pct
            if hot_side:
                fade = sides[1] if hot_side == sides[0] else sides[0]
                key = f"{game['away']} @ {game['home']} | {market} | {today_str()}"
                if key not in state["entries"]:
                    flag = {"game": key, "market": market,
                            "hot_side": hot_side, "tickets_pct": hot_pct,
                            "fade_side": fade, "first_flagged": now_stamp()}
                    state["entries"][key] = flag
                    new_flags.append(flag)
    save_state(SCAN, state)
    lines = [f"## {now_stamp()} — {len(games)} games, "
             f"{len(new_flags)} NEW public-fade flags", HONESTY]
    for f in new_flags:
        lines.append(f"NEW FLAG: {f['game']} — {f['tickets_pct']:.0f}% of "
                     f"tickets on {f['hot_side']} ({f['market']}); "
                     f"FADE: {f['fade_side']}")
    bank(SCAN, lines)
    if new_flags:
        print(HONESTY)
    for f in new_flags:
        print(f"PUBLIC FADE: {f['game']} | {f['tickets_pct']:.0f}% tickets "
              f"on {f['hot_side']} | FADE {f['fade_side']}")
    if not new_flags:
        print("public-fade: no new flags (silent).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
