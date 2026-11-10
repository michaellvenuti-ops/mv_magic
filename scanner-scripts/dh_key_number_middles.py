#!/usr/bin/env python3
"""DarkHorse KEY-NUMBER middles (PC port) — STRICT RULE.

Michael's words, Oct 26, 2026: "I need exactly on the key numbers."
Keep ONLY middles whose BOTH-WIN margin set CONTAINS a key number:
3, 7, 10, 14, 17, 21, 28. The old cluster rule and key-leg extension
are RETIRED — near-misses are out, no exceptions in this script.

Margin set definition (matches the live state files): for a spread
middle favorite -A / underdog +B (B > A), the both-win-or-push margins
are the integers m with A < m < B (both legs win outright), PLUS the
endpoints A and B themselves when they are whole numbers (one leg
wins, the other pushes — the live scan records those, e.g. -9/+10 ->
margins [9, 10]). The middle qualifies only if that set intersects
KEY_NUMBERS.

Other rules: width minimum 1; both legs -170 cap applied MANUALLY on
parsed results; clean tier = both sides -140-or-better; collect from
BOTH the Full-Time and Full-Time+ segments when the segment control
exists (best-effort click, view setting); every leg book + price;
Smaller Bets pass PRESERVING the strict rule.
State shape matches live key-number-middles-state.json
({"entries", "meta"}); entries carry "margins" and "key_number".
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
KEY_NUMBERS = (3, 7, 10, 14, 17, 21, 28)
MIN_WINDOW = 1.0
CAP = -170
CLEAN = -140


def margin_set(a, b):
    """Both-win-or-push margins for favorite -a / underdog +b."""
    lo, hi = min(a, b), max(a, b)
    margins = [m for m in range(int(lo) + 1, int(hi) + 1)
               if lo < m < hi]
    for endpoint in (lo, hi):
        if float(endpoint).is_integer() and int(endpoint) not in margins:
            margins.append(int(endpoint))
    return sorted(margins)


def key_hit(mid):
    """The key number in the margin set, or None (STRICT)."""
    try:
        a = abs(mid["leg1"]["line"])
        b = abs(mid["leg2"]["line"])
    except (TypeError, KeyError):
        return None
    margins = margin_set(a, b)
    hits = [k for k in KEY_NUMBERS if k in margins]
    return (hits[0], margins) if hits else (None, margins)


def qualifies(mid):
    if mid["window"] < MIN_WINDOW:
        return False
    if not (price_in(mid["leg1"]["price"], CAP, 100000)
            and price_in(mid["leg2"]["price"], CAP, 100000)):
        return False
    hit, _ = key_hit(mid)
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
        lines = [f"## {now_stamp()} — key-number-middles (STRICT "
                 f"exact-key rule): {len(full)} + {len(smaller)} rows "
                 f"parsed, qualifying only below"]
        for mid in full + smaller:
            if not qualifies(mid):
                continue
            key_num, margins = key_hit(mid)
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
                     "key_number": key_num, "margins": margins,
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
                state["entries"][key]["last_seen"] = now_stamp()
            lines.append(f"KEY {key_num}: {key} margins {margins} :: "
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
            new_lines.append(
                f"NEW MIDDLE: {tag}KEY {e['key_number']}: {key} "
                f"margins {e['margins']} :: {e['leg1']} / {e['leg2']}")
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
            print("key-number-middles: no new strict-key middles.")
        return 0
    finally:
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
