#!/usr/bin/env python3
"""DarkHorse KEY-NUMBER middles (PC port) — DATA-BASED KEY SET.

Rule changed Nov 10, 2026 at Michael's tap, replacing the Oct 26
tradition-based set (3, 7, 10, 14, 17, 21, 28). The new set comes from
Chet's scan of the nflverse game log — every NFL game since 2010
(n=4,499), victory-margin landing rates: 3 = 14.6%, 7 = 8.7%,
6 = 6.7%, 4 = 5.1%, 10 = 5.0%, 14 = 5.0%, 17 = 3.4%. Keep ONLY
middles whose BOTH-WIN margin set CONTAINS one of: 3, 7, 6, 4, 10,
14, 17 (ordered by landing frequency, so the reported key_number is
the strongest hit). DROPPED: 21 (2.5%) and 28 (1.5%) — they land less
often than the numbers that replaced them. ADDED: 6 and 4. The seven
kept margins cover 48.5% of all games vs 40.8% for the old set.
Full findings: nfl-key-numbers.html on mv_magic. Near-misses are
still out — the set changed, the strictness didn't.

Margin sets (MARGIN-LOGIC FIX, Nov 12, 2026, Michael's tap): for a
spread middle favorite -A / underdog +B (B > A), the STRICT both-win
margins are the integers m with A < m < B (both legs win outright);
the WIN+PUSH margins are the endpoints A and B themselves when they
are whole numbers (one leg wins, the other pushes — e.g. -9/+10 has
NO both-win margin, only win+push [9, 10]). The middle qualifies if
EITHER set contains a key number, and its kind is "both_win" when a
key sits in the strict set, else "win_push". BEFORE this fix the scan
merged both sets into one "margins" list and treated any key in it as
a both-win key — Kansas +5 / WV -4 (Nov 11) and BUF/NYJ Jets +10 /
Bills -8.5 (Nov 12 05:04) were delivered as both-win pairs when both
are win+push only. Whole-number endpoints NEVER count as both-win.

SCOPE — FOOTBALL ONLY (Michael's ruling, Nov 11, 2026): the key
set is derived from FOOTBALL margins. This scan originally read
the all-sports finder with no league gate and happily banked
NCAAB/NBA middles labeled with football keys (the Nov 10-11 night
runs were nearly all basketball) — wrong: basketball has no such
keys and has its OWN scan (dh_basketball_key_middles.py, modeled
cluster + Spanky tiers). Rows now qualify only when the row
identity's first token is NFL or NCAAF; every other league is
excluded, fail-closed when the league cannot be read.

Other rules: width minimum 1; both legs -170 cap applied MANUALLY on
parsed results; clean tier = both sides -140-or-better; collect from
BOTH the Full-Time and Full-Time+ segments when the segment control
exists (best-effort click, view setting); every leg book + price;
Smaller Bets pass PRESERVING the strict rule.
State shape matches live key-number-middles-state.json
({"entries", "meta"}); entries carry "margins" (STRICT both-win list
only, Nov 12 fix), "both_win", "win_push", "kind", and "key_number".
Banked: data/key-number-middles-<date>.md. Alert-only.
"""
import argparse
import sys

from dh_common import (FINDER_URLS, open_session, signed_in,
                       stable_row_identity, strip_volatile)
from dh_middles import collect_middles, collect_with_smaller, price_in
from odds_common import bank, now_stamp, today_str
from state import load_state, save_state

SCAN = "key-number-middles"
KEY_NUMBERS = (3, 7, 6, 4, 10, 14, 17)  # by NFL landing rate since 2010 (Nov 10, 2026)
MIN_WINDOW = 1.0
CAP = -170
CLEAN = -140


# --- Line recovery (fix 2026-11-10) --------------------------------
# parse_middle_row takes the legs from the BET cells, and for many
# books those cells carry only price / stake / book — the line lives
# in the row's MARKET columns ("Alt Spread Notre Dame -13.5"). For
# those rows leg["line"] silently equals the leg PRICE, so the old
# code computed margin sets off prices (100-200 range: could never
# contain a key number, so those rows were silently dropped and real
# key middles were MISSED). Lines are now recovered from the
# identity's Spread segments first; leg lines are only a fallback
# when they cannot be a price (different from the leg's price).
import re as _re
_SEG_NUM = _re.compile(r"([+-]?\d+(?:\.\d+)?)\s*(?:YES|NO)?\s*$",
                       _re.I)


def identity_spread_lines(cells):
    ident = stable_row_identity(cells) or ""
    out = []
    for seg in ident.split(" / "):
        if "spread" not in seg.lower():
            continue
        m = _SEG_NUM.search(seg.strip())
        if m:
            out.append(float(m.group(1)))
    return out


def recovered_lines(mid):
    """(a, b) absolute spread lines for the middle, or None."""
    lines = identity_spread_lines(mid.get("cells"))
    if len(lines) >= 2:
        return abs(lines[0]), abs(lines[1])
    try:
        l1 = float(mid["leg1"]["line"]); p1 = int(mid["leg1"]["price"])
        l2 = float(mid["leg2"]["line"]); p2 = int(mid["leg2"]["price"])
    except (TypeError, ValueError, KeyError):
        return None
    if l1 == p1 or l2 == p2:
        return None            # a bet cell carried no line
    return abs(l1), abs(l2)


def split_margins(a, b):
    """(both_win, win_push) integer margin lists for favorite -a /
    underdog +b. both_win: m with lo < m < hi (both legs win
    outright). win_push: whole-number endpoints lo/hi (one leg wins,
    the other pushes at exactly that margin). Nov 12, 2026 fix: the
    old margin_set() merged these into one list, so a key sitting
    only on a push endpoint qualified the pair as a BOTH-WIN key
    middle — wrong (Kansas +5 / WV -4: both_win [], win_push [4, 5])."""
    lo, hi = min(a, b), max(a, b)
    both_win = [m for m in range(int(lo) + 1, int(hi) + 1)
                if lo < m < hi]
    win_push = sorted({int(e) for e in (lo, hi)
                       if float(e).is_integer()})
    return sorted(both_win), win_push


def key_hit(mid):
    """(key, both_win, win_push, kind) — kind 'both_win' when a key
    sits in the STRICT both-win set, else 'win_push' when a key sits
    only in the win+push endpoints. STRICT: a whole-number endpoint
    never counts as a both-win margin."""
    lines = recovered_lines(mid)
    if lines is None:
        return None, [], [], None
    a, b = lines
    both_win, win_push = split_margins(a, b)
    for k in KEY_NUMBERS:
        if k in both_win:
            return k, both_win, win_push, "both_win"
    for k in KEY_NUMBERS:
        if k in win_push:
            return k, both_win, win_push, "win_push"
    return None, both_win, win_push, None


FOOTBALL_LEAGUES = ("NFL", "NCAAF")
BASKETBALL_LEAGUES = ("NBA", "NCAAB")


def row_league(mid):
    """League token from the row identity's first word (the
    basketball scan's classify() reads the same field). None when
    the identity carries no league token."""
    ident = stable_row_identity(mid.get("cells")) or ""
    if not ident:
        ident = (mid["leg1"].get("side", "") + " "
                 + mid["leg2"].get("side", ""))
    head = ident.split(" ", 1)[0].strip().upper() if ident else ""
    return head or None


def qualifies(mid):
    if row_league(mid) not in FOOTBALL_LEAGUES:
        return False        # football-only rule (Nov 11, 2026)
    lines = recovered_lines(mid)
    if lines is None or abs(lines[1] - lines[0]) < MIN_WINDOW:
        return False
    if not (price_in(mid["leg1"]["price"], CAP, 100000)
            and price_in(mid["leg2"]["price"], CAP, 100000)):
        return False
    hit, _, _, _ = key_hit(mid)
    return hit is not None


def collect_segments(page, smaller_flag):
    """Full-Time AND Full-Time+ segments (view setting clicks)."""
    from dh_common import switch_liquidity_mode  # noqa: F401 (pattern reuse)
    mids = []
    for segment in ("Full-Time", "Full-Time+"):
        try:
            loc = page.get_by_text(segment, exact=True)
            if loc.count():
                loc.first.click()
                page.wait_for_timeout(2500)
        except Exception:  # noqa: BLE001 - segment control may not exist
            pass
        if smaller_flag:
            got, _ = collect_middles(page)
            for m in got:
                m["smaller_bets"] = True
            mids += got
        else:
            got, _ = collect_middles(page)
            mids += got
    return mids


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
                  "key-number-middles skipped, no prices invented.")
            return 0
        full = collect_segments(page, smaller_flag=False)
        smaller = []
        if not args.no_smaller:
            from dh_common import switch_liquidity_mode
            if switch_liquidity_mode(page, "Smaller Bets"):
                try:
                    smaller = collect_segments(page, smaller_flag=True)
                finally:
                    switch_liquidity_mode(page, "Full Bet Size Only")
        if not full and not smaller:
            print("key-number-middles: zero middles parsed — send a "
                  "dump back. No state written.", file=sys.stderr)
            return 2
        deliverables = []
        lines = [f"## {now_stamp()} — key-number-middles (data-based "
                 f"key set 3/7/6/4/10/14/17; NFL/NCAAF only; "
                 f"win+push labeled honestly): "
                 f"{len(full)} + {len(smaller)} rows parsed, "
                 f"qualifying only below"]
        _hoops = sum(1 for m in full + smaller
                     if row_league(m) in BASKETBALL_LEAGUES)
        for mid in full + smaller:
            if not qualifies(mid):
                continue
            key_num, both_win, win_push, kind = key_hit(mid)
            # Stable key (fix 2026-11-05, same as dh_pairs.pair_key):
            # row identity from the non-bet cells + leg books — the
            # old key embedded raw bet-cell text (price / % / stake),
            # so every price wiggle re-keyed a known middle as NEW.
            _l1, _l2 = mid["leg1"], mid["leg2"]
            _ident = stable_row_identity(mid.get("cells"))
            if not _ident:
                _ident = (f"{mid.get('game', '')} | "
                          f"{mid.get('market', '')} | "
                          f"{strip_volatile(_l1['side'])} | "
                          f"{strip_volatile(_l2['side'])}")
            key = f"{_ident} | {_l1['book']} | {_l2['book']}"
            entry = {"game": mid.get("game", ""),
                     "market": mid.get("market", ""),
                     # "margins" is the STRICT both-win list only
                     # (Nov 12 fix) — the board's fallback reads it,
                     # so a push endpoint must never sit in it.
                     "key_number": key_num, "margins": both_win,
                     "both_win": both_win, "win_push": win_push,
                     "kind": kind,
                     "cluster": f"Key {key_num}",
                     "clean_tier": all(
                         price_in(mid[l]["price"], CLEAN, 100000)
                         for l in ("leg1", "leg2")),
                     "leg1": f"{mid['leg1']['side']}, "
                             f"{mid['leg1']['book']}, "
                             f"{mid['leg1']['price']}",
                     "leg2": f"{mid['leg2']['side']}, "
                             f"{mid['leg2']['book']}, "
                             f"{mid['leg2']['price']}",
                     "smaller_bets": bool(mid.get("smaller_bets")),
                     "first_reported": now_stamp(),
                     "last_seen": now_stamp()}
            if key not in state["entries"]:
                state["entries"][key] = entry
                deliverables.append((key, entry))
            else:
                # Refresh the margin fields on re-see (Nov 12 fix):
                # entries banked before the fix carry the merged
                # "margins" list — refresh them so the board stops
                # showing push numbers as both-win windows.
                old = state["entries"][key]
                old.update({"margins": both_win, "both_win": both_win,
                            "win_push": win_push, "kind": kind,
                            "key_number": key_num,
                            "last_seen": now_stamp()})
                old["leg1"] = entry["leg1"]
                old["leg2"] = entry["leg2"]
            if kind == "win_push":
                label = (f"KEY {key_num} WIN+PUSH: {key} win+push "
                         f"{win_push}; both-win "
                         f"{both_win if both_win else 'none'}")
            else:
                label = f"KEY {key_num}: {key} both-win {both_win}"
            lines.append(f"{label} :: "
                         f"{entry['leg1']} / {entry['leg2']}")
        # Chat-delivery wiring (2026-11-10, Michael's tap): bank the
        # NEW strict-key middles as NEW MIDDLE lines directly under
        # the header, with their count in the header — the
        # steam_side.py pattern the pc-data delivery watcher screens
        # for. Previously deliverables reached only stdout
        # (scheduler.log), never chat.
        new_lines = []
        for key, e in deliverables:
            tag = "SMALLER BETS " if e["smaller_bets"] else ""
            if e["kind"] == "win_push":
                label = (f"KEY {e['key_number']} WIN+PUSH: {key} "
                         f"win+push {e['win_push']}; both-win "
                         f"{e['both_win'] if e['both_win'] else 'none'}")
            else:
                label = (f"KEY {e['key_number']}: {key} "
                         f"both-win {e['both_win']}")
            new_lines.append(
                f"NEW MIDDLE: {tag}{label} :: "
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
            print(f"{tag}KEY-NUMBER MIDDLE (key {e['key_number']}): {key} "
                  f"— {e['leg1']} / {e['leg2']}")
        if not deliverables:
            print("key-number-middles: no new key middles.")
        print(f"key-number-middles: {_hoops} basketball rows "
              f"excluded (football-only rule).")
        return 0
    finally:
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
