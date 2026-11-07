#!/usr/bin/env python3
"""Steam-side stale-book scan (PC port; built live Oct 31, 2026).

Logic (identical to Chet's hourly scan): track the MEDIAN spread and
total per FBS game on the ScoresAndOdds public board. A STEAM EVENT is
the median spread moving >= 1.0 or the median total moving >= 1.5
since the last check. When steam hits, flag any NJ book still dealing
the PRE-MOVE median number or better on the side the steam moved
TOWARD (e.g. median steamed from -6 to -7.5: a book still hanging
-6.5 or better on the favorite is stale on the steam side).

State shape matches the live file: {"created", "flags": {}, "medians":
{key: {spread, total, favorite, kickoff, books_counted, last_seen}}}.
Only NEW flags print; everything seen is banked to
data/steam-side-<date>.md. Grading on Chet's side: $100 flat at
assumed -110, labeled as assumed.

KICKOFF CUTOFF (added Nov 5, 2026): games ESPN shows as already
kicked off are skipped entirely — no steam check, no median update.
The board keeps dealing LIVE lines on in-progress games; reading
those against pregame medians produced phantom flags on a decided
JMU @ Southern Miss game. This matches the Muse-side scan, which
excludes live games at board read.

Unreadable board = banked note + exit 2, state untouched. Never
invents a line.
"""
import argparse
import sys

from config import DATA_DIR
from odds_common import (bank, fmt_price, game_started, median_of,
                         now_stamp, started_matchups, today_str)
from so_board import BoardUnreadable, load_board, medians
from state import load_state, save_state

SCAN = "steam-side"
SPREAD_STEAM, TOTAL_STEAM = 1.0, 1.5


def game_key(game):
    return f"FBS {game['away']} @ {game['home']} | {today_str()}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true",
                    help="save raw board HTML to data/ for calibration")
    args = ap.parse_args()
    state = load_state(SCAN)
    # Live shape keeps top-level flags/medians; state.py adds entries/meta.
    state.setdefault("flags", {})
    state.setdefault("medians", {})
    dump = DATA_DIR / f"dump-steam-side-{today_str()}.html" if args.dump else None
    try:
        games, _ = load_board("NCAAF", dump_path=dump)
    except BoardUnreadable as e:
        bank(SCAN, [f"## {now_stamp()} — board unreadable: {e}"])
        print(f"steam-side: board unreadable ({e})", file=sys.stderr)
        return 2
    started = started_matchups(("NCAAF",))
    new_flags = []
    steam_events = 0
    skipped_started = 0
    for game in games:
        if game_started(game, started):
            skipped_started += 1
            continue
        key = game_key(game)
        med_spread, med_total = medians(game)
        prev = state["medians"].get(key)
        if prev and med_spread is not None and prev.get("spread") is not None:
            d_spread = med_spread - prev["spread"]
            d_total = None
            if med_total is not None and prev.get("total") is not None:
                d_total = med_total - prev["total"]
            steamed = (abs(d_spread) >= SPREAD_STEAM or
                       (d_total is not None and abs(d_total) >= TOTAL_STEAM))
            if steamed:
                steam_events += 1
                # Side steam moved toward: spread more negative = home.
                if abs(d_spread) >= SPREAD_STEAM:
                    toward_home = d_spread < 0
                    pre = prev["spread"]
                    for book, lines in game["books"].items():
                        cur = lines.get("home_spread")
                        if cur is None:
                            continue
                        stale = (cur >= pre) if toward_home else (cur <= pre)
                        fkey = f"{key} | spread | {book}"
                        if stale and fkey not in state["flags"]:
                            flag = {"game": key, "market": "spread",
                                    "book": book, "stale_line": cur,
                                    "pre_move_median": pre,
                                    "new_median": med_spread,
                                    "side": game["home"] if toward_home
                                            else game["away"],
                                    "first_flagged": now_stamp()}
                            state["flags"][fkey] = flag
                            new_flags.append(flag)
                if d_total is not None and abs(d_total) >= TOTAL_STEAM:
                    toward_over = d_total > 0
                    pre_t = prev["total"]
                    for book, lines in game["books"].items():
                        cur = lines.get("total")
                        if cur is None:
                            continue
                        stale = (cur <= pre_t) if toward_over else (cur >= pre_t)
                        fkey = f"{key} | total | {book}"
                        if stale and fkey not in state["flags"]:
                            flag = {"game": key, "market": "total",
                                    "book": book, "stale_line": cur,
                                    "pre_move_median": pre_t,
                                    "new_median": med_total,
                                    "side": "Over" if toward_over else "Under",
                                    "first_flagged": now_stamp()}
                            state["flags"][fkey] = flag
                            new_flags.append(flag)
        state["medians"][key] = {
            "spread": med_spread, "total": med_total,
            "books_counted": len(game["books"]),
            "kickoff": game.get("kickoff", ""), "last_seen": now_stamp()}
    save_state(SCAN, state)
    lines = [f"## {now_stamp()} — {len(games)} games, "
             f"{steam_events} steam events, {len(new_flags)} NEW flags"
             + (f", {skipped_started} started games skipped"
                if skipped_started else "")]
    for f in new_flags:
        lines.append(
            f"NEW FLAG: {f['game']} — {f['book']} still dealing "
            f"{f['market']} {f['stale_line']} on {f['side']} "
            f"(pre-move median {f['pre_move_median']}, now {f['new_median']})")
    bank(SCAN, lines)
    for f in new_flags:
        print(f"STEAM-SIDE FLAG: {f['game']} | {f['book']} | "
              f"{f['side']} {f['market']} {f['stale_line']} "
              f"(median {f['pre_move_median']} -> {f['new_median']})")
    if not new_flags:
        print(f"steam-side: {len(games)} games, no new flags (silent).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
