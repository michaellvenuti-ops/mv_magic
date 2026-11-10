#!/usr/bin/env python3
"""DarkHorse game-line middles (PC port).

Rules (identical to Chet's hourly scan): Middles finder on GAME lines
(spreads/totals, not player props), width >= 3, every delivered leg
carries book + price, both legs -170-or-better cap applied manually
(the tool's Hedge Bet Odds max is disabled under Any Book target).
Compact reporting; Smaller Bets pass (see dh_middles.py).
State shape matches live game-middles-state.json ({"entries", "meta"}).
Banked: data/game-middles-<date>.md. Alert-only.
"""
import argparse
import sys

from dh_common import (FINDER_URLS, open_session, signed_in,
                       stable_row_identity, strip_volatile)
from dh_middles import collect_middles, collect_with_smaller, price_in
from odds_common import bank, now_stamp, today_str
from state import load_state, save_state

SCAN = "game-middles"
MIN_WINDOW = 3.0
CAP = -170


def qualifies(mid):
    if mid["window"] < MIN_WINDOW:
        return False
    return (price_in(mid["leg1"]["price"], CAP, 100000)
            and price_in(mid["leg2"]["price"], CAP, 100000))


def middle_key(mid):
    """Stable state key: row identity (event/market/line text from
    the row's non-bet cells — dh_common.stable_row_identity) + the
    two leg books. The old key embedded the raw bet-cell text
    (price / % / stake), so every price wiggle re-keyed a known
    middle as NEW (fix 2026-11-05, same as dh_pairs.pair_key)."""
    l1, l2 = mid["leg1"], mid["leg2"]
    ident = stable_row_identity(mid.get("cells"))
    if not ident:
        ident = (f"{mid.get('game', '')} | {mid.get('market', '')} | "
                 f"{strip_volatile(l1['side'])} | "
                 f"{strip_volatile(l2['side'])}")
    return f"{ident} | {l1['book']} | {l2['book']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--no-smaller", action="store_true")
    args = ap.parse_args()
    state = load_state(SCAN)
    pw, ctx = open_session()
    try:
        page = ctx.new_page()
        if not signed_in(page, FINDER_URLS["middles"]):
            print("DarkHorse check unavailable (signed out) — "
                  "game-middles skipped, no prices invented.")
            return 0
        if args.no_smaller:
            full, _ = collect_middles(page)
            smaller = []
        else:
            full, smaller = collect_with_smaller(page)
        if not full and not smaller:
            print("game-middles: zero middles parsed — send a dump "
                  "back for a parser patch. No state written.",
                  file=sys.stderr)
            return 2
        deliverables = []
        lines = [f"## {now_stamp()} — game-middles: {len(full)} + "
                 f"{len(smaller)} rows parsed (qualifying only)"]
        for mid in full + smaller:
            if not qualifies(mid):
                continue
            key = middle_key(mid)
            entry = {"game": mid.get("game", ""),
                     "market": mid.get("market", ""),
                     "window": mid["window"],
                     "conversion_pct": mid["conversion_pct"],
                     "leg1": mid["leg1"], "leg2": mid["leg2"],
                     "smaller_bets": bool(mid.get("smaller_bets")),
                     "first_reported": now_stamp(),
                     "last_seen": today_str()}
            if key not in state["entries"]:
                state["entries"][key] = entry
                deliverables.append((key, entry))
            else:
                state["entries"][key]["last_seen"] = today_str()
            lines.append(f"{key} w{entry['window']} :: {mid['leg1']} / "
                         f"{mid['leg2']}")
        # Chat-delivery wiring (2026-11-10, Michael's tap): the banked
        # list re-lists every qualifying middle each run, so the truly
        # NEW ones are ALSO banked as NEW MIDDLE lines directly under
        # the header, and the header carries their count — the exact
        # pattern steam_side.py uses ("N NEW flags") that the pc-data
        # delivery watcher screens for. Without this, new middles
        # reached only stdout (scheduler.log), never chat.
        new_lines = []
        for key, e in deliverables:
            tag = "SMALLER BETS " if e["smaller_bets"] else ""
            new_lines.append(
                f"NEW MIDDLE: {tag}{key} w{e['window']} :: "
                f"{e['leg1']} / {e['leg2']}")
        lines[0] += f" — {len(deliverables)} NEW middles"
        if new_lines:
            lines = (lines[:1] + new_lines
                     + ["(end of new middles — full qualifying list "
                         "below)"] + lines[1:])
        save_state(SCAN, state)
        bank(SCAN, lines)
        for key, e in deliverables[:10]:
            tag = "SMALLER BETS " if e["smaller_bets"] else ""
            print(f"{tag}GAME MIDDLE: {key} — window {e['window']} :: "
                  f"{e['leg1']['side']} {e['leg1']['price']} "
                  f"{e['leg1']['book']} / {e['leg2']['side']} "
                  f"{e['leg2']['price']} {e['leg2']['book']}")
        if not deliverables:
            print("game-middles: no new qualifying middles.")
        return 0
    finally:
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
