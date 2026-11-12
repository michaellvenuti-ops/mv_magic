#!/usr/bin/env python3
"""DarkHorse player-prop middles (PC port).

Rules (identical to Chet's scan):
  - Middles finder, PLAYER PROPS default view,
  - window (width) >= 5,
  - both legs priced -200..+200 inclusive,
  - both-sides -140-or-better = "clean" tier flag (a flag, not a
    filter — dirtier pairs still bank/deliver, labeled),
  - pages 1-10 at max items-per-page, conversion % descending,
    COMPACT reporting (result count + qualifying rows only),
  - dedupe on player + market + line pair ("Over X.5" == "(X+1)+"
    alt-line labels normalize to the same key),
  - ALTERNATIVES RULE: when the tool lists alternative book combos for
    the same line pair, they are recorded under "alternatives",
  - Smaller Bets pass (dh_middles.collect_with_smaller).
PLAYER-PROP FILTER (2026-11-12, Michael's tap): the finder serves
game spreads/totals in its default view too, so this script keeps
ONLY rows whose market names a player stat (is_player_prop) — game
lines stay in game-middles. Game rows dropped by the filter are
counted in the banked header, never delivered.
State shape matches live prop-middles-state.json:
  {"entries": {key: {player, market, game, window, conversion_pct,
   clean_tier, headline: {leg1, leg2}, alternatives: [...],
   first_reported, last_seen}}, "meta": {...}}
Banked: data/prop-middles-<date>.md. Alert-only: NEW/CHANGED print.
"""
import argparse
import re
import sys

from config import DATA_DIR
from dh_common import (FINDER_URLS, open_session, signed_in,
                       stable_row_identity, strip_volatile)
from dh_middles import collect_with_smaller, price_in
from odds_common import bank, now_stamp, today_str
from state import load_state, save_state

SCAN = "prop-middles"
MIN_WINDOW = 5.0
PRICE_LO, PRICE_HI = -200, 200
CLEAN = -140

# Market words that only ever name a PLAYER stat. Game segments
# ("Total Under X", "Spread ...", "Alt Spread ...", "Moneyline",
# "Team Total ...") never contain these, so a row carrying one is a
# prop row. "Points" is safe: game totals render as "Total Under X",
# never "... Points ...".
PROP_MARKETS = (
    "Rebounds", "Assists", "Points", "Steals", "Blocks", "Turnovers",
    "Threes", "3-Pointers", "3 Pointers", "PRA",
    "Receiving Yards", "Recv Yards", "Rushing Yards", "Rush Yards",
    "Passing Yards", "Pass Yards", "Receptions", "Receiving",
    "Rushing Attempts", "Rush Attempts", "Targets", "Tackles", "Sacks",
    "Passing TD", "Rushing TD", "Receiving TD", "Touchdown",
    "Shots on Goal", "Shots", "Goals", "Saves",
    "Hits", "Strikeouts", "Home Runs", "RBIs", "Total Bases",
    "First Basket", "Double-Double", "Triple-Double",
    "Completions", "Pass Attempts", "Interceptions",
    "Points + Rebounds", "Points + Assists",
)


def is_prop_identity(ident):
    """True when a row identity names a player stat (see PROP_MARKETS),
    or — fallback for stat words outside the list — when a market
    segment carries a player name in 'X. Surname' form ('P. Reed')."""
    if not ident:
        return False
    low = ident.lower()
    if any(k.lower() in low for k in PROP_MARKETS):
        return True
    return bool(re.search(r"\b[A-Z]\. [A-Z][a-z]+", ident))


def is_player_prop(mid):
    """True only for genuine player-prop rows. The finder serves game
    spreads/totals in its default view, so prop-middles filters rows
    itself (fix 2026-11-12, Michael's tap) instead of trusting the
    view. Rows with no readable identity are dropped (fail-closed:
    a game row must never reach the prop file again)."""
    ident = stable_row_identity(mid.get("cells")) or ""
    if not ident:
        ident = f"{mid.get('player', '')} {mid.get('market', '')}"
    return is_prop_identity(ident)


def normalize_line(leg):
    """'Over 49.5' and '50+' describe the same alt line; normalize an
    alt 'N+' label to the Over (N-0.5) line for dedupe."""
    side = leg["side"]
    if side.rstrip().endswith("+") and "Over" not in side:
        return leg["line"] - 0.5
    return leg["line"]


def middle_key(mid):
    """Stable state key: row identity (player/market/line text from
    the row's non-bet cells — dh_common.stable_row_identity) + the
    two leg books. This carries the documented dedupe identity
    (player + market + line pair) as the finder actually renders
    it; the old key read player/market fields the parser never
    populates plus the parsed 'line' values, so price moves
    re-keyed known middles as NEW (fix 2026-11-05, same as
    dh_pairs.pair_key). normalize_line() stays for the calibrated
    parser that will populate real leg lines."""
    l1, l2 = mid["leg1"], mid["leg2"]
    ident = stable_row_identity(mid.get("cells"))
    if not ident:
        ident = (f"{mid.get('player', '')} | {mid.get('market', '')} | "
                 f"{strip_volatile(l1['side'])} | "
                 f"{strip_volatile(l2['side'])}")
    return f"{ident} | {l1['book']} | {l2['book']}"


def qualifies(mid):
    if mid["window"] < MIN_WINDOW:
        return False
    return (price_in(mid["leg1"]["price"], PRICE_LO, PRICE_HI)
            and price_in(mid["leg2"]["price"], PRICE_LO, PRICE_HI))


def is_clean(mid):
    try:
        return (int(mid["leg1"]["price"]) >= CLEAN
                and int(mid["leg2"]["price"]) >= CLEAN)
    except (TypeError, ValueError):
        return False


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
                  "prop-middles skipped, no prices invented.")
            return 0
        if args.no_smaller:
            from dh_middles import collect_middles
            full, _ = collect_middles(page)
            smaller = []
        else:
            full, smaller = collect_with_smaller(page)
        # Prop filter (2026-11-12): drop game-line rows the finder
        # serves in its default view; count them in the header.
        n_full, n_smaller = len(full), len(smaller)
        full = [m for m in full if is_player_prop(m)]
        smaller = [m for m in smaller if is_player_prop(m)]
        n_dropped = (n_full - len(full)) + (n_smaller - len(smaller))
        if not full and not smaller:
            print("prop-middles: zero player-prop middles parsed "
                  f"({n_dropped} game-line rows dropped by prop filter) "
                  "— run with a dump and send it back for a parser "
                  "patch. No state written.", file=sys.stderr)
            return 2
        deliverables = []
        lines = [f"## {now_stamp()} — prop-middles: {len(full)} full-size "
                 f"+ {len(smaller)} smaller-bets rows parsed (compact: "
                 f"qualifying only below)"
                 + (f" — {n_dropped} game-line rows dropped by prop "
                    f"filter" if n_dropped else "")]
        for mid in full + smaller:
            if not qualifies(mid):
                continue
            key = middle_key(mid)
            entry = {"player": mid.get("player", ""),
                     "market": mid.get("market", ""),
                     "game": mid.get("game", ""),
                     "window": mid["window"],
                     "conversion_pct": mid["conversion_pct"],
                     "clean_tier": is_clean(mid),
                     "headline": {"leg1": mid["leg1"],
                                  "leg2": mid["leg2"]},
                     "alternatives": mid.get("alternatives", []),
                     "smaller_bets": bool(mid.get("smaller_bets")),
                     "first_reported": now_stamp(),
                     "last_seen": today_str()}
            prior = state["entries"].get(key)
            if prior is None:
                state["entries"][key] = entry
                deliverables.append(("NEW", key, entry))
            else:
                prior["last_seen"] = today_str()
                # CHANGED: newly clean, window grew 2+, or a leg +10c.
                try:
                    leg_better = any(
                        int(entry["headline"][l]["price"])
                        - int(prior["headline"][l]["price"]) >= 10
                        for l in ("leg1", "leg2"))
                except (TypeError, ValueError):
                    leg_better = False
                if (not prior.get("clean_tier") and entry["clean_tier"]) \
                        or entry["window"] - prior.get("window", 0) >= 2 \
                        or leg_better:
                    state["entries"][key] = {
                        **prior, **entry,
                        "first_reported": prior["first_reported"]}
                    deliverables.append(("CHANGED", key, entry))
            lines.append(f"{'SMALLER BETS ' if entry['smaller_bets'] else ''}"
                         f"{'CLEAN ' if entry['clean_tier'] else ''}{key} "
                         f"w{entry['window']} :: {entry['headline']}")
        # Chat-delivery wiring (2026-11-10, Michael's tap): bank the
        # NEW/CHANGED deliverables as NEW MIDDLE / CHANGED MIDDLE
        # lines directly under the header, with their count in the
        # header — the steam_side.py pattern the pc-data delivery
        # watcher screens for. Previously deliverables reached only
        # stdout (scheduler.log), never chat.
        new_lines = []
        for kind, key, e in deliverables:
            tag = "SMALLER BETS " if e["smaller_bets"] else ""
            clean = "CLEAN " if e["clean_tier"] else ""
            new_lines.append(
                f"{kind} MIDDLE: {tag}{clean}{key} w{e['window']} :: "
                f"{e['headline']}")
        lines[0] += f" — {len(deliverables)} NEW/CHANGED middles"
        if new_lines:
            lines = (lines[:1] + new_lines
                     + ["(end of new/changed middles — full qualifying "
                         "list below)"] + lines[1:])
        save_state(SCAN, state)
        bank(SCAN, lines)
        for kind, key, e in deliverables[:10]:
            tag = "SMALLER BETS " if e["smaller_bets"] else ""
            clean = "CLEAN " if e["clean_tier"] else ""
            print(f"{tag}PROP MIDDLE {clean}: {key} — window "
                  f"{e['window']} :: {e['headline']['leg1']} / "
                  f"{e['headline']['leg2']}")
        if not deliverables:
            print("prop-middles: no new/changed qualifying middles.")
        return 0
    finally:
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
