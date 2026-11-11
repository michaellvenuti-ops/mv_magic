#!/usr/bin/env python3
"""DarkHorse BASKETBALL key-number middles (PC) — CLUSTER rule.

Built Nov 10, 2026 at Michael's tap, off Chet's ESPN-history scan of
basketball finals: NBA 2,644 games (2023-24 + 2024-25) and NCAAB
6,292 games (2024-25, Division I). Findings page:
key-number-middles-nba-ncaab.html on mv_magic.

The honest headline: basketball has NO football-style key numbers.
Scoring in 1s, 2s and 3s over ~100 possessions smooths the margin
distribution into a plateau — the most common NBA margin (7) lands
5.67% of games vs the NFL's 14.63% on margin 3 — and totals form a
smooth bell with no key totals at all (modal NBA total 223 = 2.46%).
So this scan does NOT hunt exact keys. It SCORES every NBA/NCAAB
middle by its modeled both-win probability, summed from the
empirical landing rates below (both-win numbers at full weight,
whole-number endpoints at half weight — one leg wins, the other
pushes), and keeps:

  SPREAD middles with modeled both-win probability >= 10%
  TOTAL  middles with modeled both-win probability >=  8%

The bars sit near football selectivity (one NFL key-3 window is
~14.6%) while respecting that basketball value comes from window
WIDTH over the cluster (NBA margins 2-9 cover 42.2% of games;
NCAAB margins 2-5 are the core at 23.0%, 2-9 covers 41.5%).

SPANKY TIER (added Nov 10, 2026 at Michael's tap — "scans looking
for middles like Spanky said"): across the 93-episode Be Better
Bettors catalog Spanky never names a basketball key number; his
college basketball middling baselines are WIDTH rules at -110
(Ep 14, Mar 6 2020: CBB totals need ~2.5 points of differential
for a +EV middle, 2 points is roughly break-even; Ep 39, Dec 14
2020: a 1.5-point side middle and a 2-point total middle are the
baselines at -110, and a half point on a CBB total is worth about
5 cents / 2.5%). So a COLLEGE row also qualifies — labeled
SPANKY — when its window is >= 1.5 points (spreads) or >= 2.5
points (totals) AND both legs are priced -125 or better (near his
-110 baseline; worse juice needs more width than his rule
assumes). The 2.0-point total window is his break-even, not +EV,
so it does NOT qualify. He gave no NBA width rule (Ep 93 quotes a
flat ~$0.78 per half point on NBA spreads), so NBA rows stay on
the modeled tier only — no rule invented for him.

Other rules match the football key scan: width minimum 1; both
legs -170 cap (price_in on parsed rows); clean tier = both sides
-140-or-better; BOTH Full-Time and Full-Time+ segments; Smaller
Bets pass preserving the rule; every leg book + price. League and
market come from the row identity's first token (NBA / NCAAB) and
its market columns (Spread / Total) — football, hockey and
moneyline rows are ignored here (the football key scan owns NFL /
NCAAF spreads). Alert-only. Banked:
data/basketball-key-middles-<date>.md.
"""
import argparse
import sys

from dh_common import (FINDER_URLS, open_session, signed_in,
                       stable_row_identity, strip_volatile)
from dh_middles import collect_middles, price_in
from odds_common import bank, now_stamp, today_str  # noqa: F401
from state import load_state, save_state

SCAN = "basketball-key-middles"
CAP = -170
CLEAN = -140
MIN_WINDOW = 1.0
SPREAD_BAR = 10.0   # modeled both-win % (see docstring)
TOTAL_BAR = 8.0
# Spanky tier (college only, Ep 14 + Ep 39 baselines at -110):
SPANKY_SPREAD_WIDTH = 1.5
SPANKY_TOTAL_WIDTH = 2.5
SPANKY_PRICE_FLOOR = -125   # both legs -125 or better

# Empirical landing rates (%), ESPN finals — see docstring.
NBA_MARGIN_PCT = {1: 4.16, 2: 5.41, 3: 5.11, 4: 4.61, 5: 4.99,
                  6: 5.60, 7: 5.67, 8: 5.48, 9: 5.30, 10: 4.27,
                  11: 4.20, 12: 4.35, 13: 3.56, 14: 3.67, 15: 3.25,
                  16: 3.37, 17: 2.23, 18: 2.19, 19: 2.19, 20: 1.85,
                  21: 1.93, 22: 1.55, 23: 1.82, 24: 1.29, 25: 1.29}
NCAAB_MARGIN_PCT = {1: 4.05, 2: 5.47, 3: 5.74, 4: 5.90, 5: 5.93,
                    6: 4.37, 7: 4.53, 8: 4.86, 9: 4.66, 10: 4.18,
                    11: 4.02, 12: 3.10, 13: 3.56, 14: 2.99, 15: 2.64,
                    16: 2.62, 17: 2.43, 18: 2.11, 19: 2.00, 20: 1.88,
                    21: 1.65, 22: 1.56, 23: 1.70, 24: 1.30, 25: 1.26}
MARGIN_PCT = {"NBA": NBA_MARGIN_PCT, "NCAAB": NCAAB_MARGIN_PCT}
MARGIN_TAIL = {"NBA": 1.0, "NCAAB": 0.9}   # margins beyond the table

NBA_TOTAL_PCT = {195: 0.53, 196: 0.79, 197: 0.87, 198: 0.53, 199: 0.72, 200: 0.83, 201: 0.83, 202: 0.79, 203: 1.17, 204: 1.17, 205: 1.06, 206: 1.10, 207: 1.29, 208: 1.66, 209: 1.36, 210: 1.51, 211: 1.70, 212: 1.85, 213: 1.51, 214: 1.44, 215: 1.66, 216: 1.82, 217: 1.74, 218: 1.85, 219: 1.59, 220: 2.04, 221: 1.97, 222: 1.74, 223: 2.46, 224: 1.44, 225: 1.63, 226: 1.89, 227: 1.97, 228: 2.16, 229: 2.04, 230: 1.78, 231: 1.82, 232: 1.97, 233: 1.85, 234: 1.82, 235: 2.00, 236: 1.85, 237: 1.85, 238: 1.70, 239: 1.66, 240: 1.70, 241: 1.48, 242: 1.25, 243: 1.25, 244: 1.78, 245: 1.06, 246: 1.06, 247: 1.02, 248: 1.13, 249: 0.98, 250: 0.72, 251: 0.95, 252: 1.06, 253: 0.91, 254: 0.76, 255: 0.72, 256: 0.68, 257: 0.64, 258: 0.61, 259: 0.76, 260: 0.38, 261: 0.61, 262: 0.57}
NCAAB_TOTAL_PCT = {112: 0.43, 113: 0.49, 114: 0.51, 115: 0.48, 116: 0.46, 117: 0.60, 118: 0.76, 119: 0.81, 120: 0.86, 121: 0.94, 122: 0.75, 123: 0.78, 124: 1.35, 125: 1.32, 126: 1.00, 127: 1.41, 128: 1.26, 129: 1.32, 130: 1.78, 131: 1.64, 132: 1.78, 133: 1.68, 134: 1.84, 135: 1.68, 136: 1.80, 137: 2.21, 138: 2.46, 139: 2.56, 140: 1.97, 141: 2.03, 142: 2.11, 143: 2.07, 144: 2.21, 145: 2.38, 146: 2.02, 147: 2.38, 148: 2.11, 149: 2.35, 150: 2.08, 151: 2.30, 152: 2.07, 153: 1.84, 154: 1.72, 155: 2.05, 156: 1.68, 157: 1.73, 158: 1.91, 159: 1.57, 160: 1.30, 161: 1.49, 162: 1.32, 163: 1.43, 164: 1.21, 165: 1.14, 166: 0.86, 167: 1.21, 168: 1.00, 169: 0.92, 170: 0.78, 171: 0.79, 172: 0.76, 173: 0.49, 174: 0.62, 175: 0.62, 176: 0.45, 177: 0.51, 178: 0.46, 179: 0.59, 180: 0.46, 181: 0.43, 182: 0.29}
TOTAL_PCT = {"NBA": NBA_TOTAL_PCT, "NCAAB": NCAAB_TOTAL_PCT}
TOTAL_TAIL = {"NBA": 0.5, "NCAAB": 0.4}    # totals beyond the table


# --- Line recovery (fix 2026-11-10, first-run postmortem) ----------
# parse_middle_row takes the legs from the BET cells, and for many
# books those cells carry only price / stake / book — the line lives
# in the row's MARKET columns ("Alt Spread Bucknell +41.5"). For
# those rows leg["line"] silently equals the leg PRICE (e.g. +145),
# and the first version scored windows like [145..160] as "margins".
# The lines are therefore recovered from the identity's market
# segments; leg lines are only a fallback when they cannot be a
# price (non-integer, or different from the leg's price).
import re as _re
_SEG_NUM = _re.compile(r"([+-]?\d+(?:\.\d+)?)\s*(?:YES|NO)?\s*$",
                       _re.I)


def identity_lines(ident, market):
    """The two lines named in the identity's market segments."""
    if not ident:
        return []
    out = []
    for seg in ident.split(" / "):
        if market not in seg.lower():
            continue
        m = _SEG_NUM.search(seg.strip())
        if m:
            out.append(float(m.group(1)))
    return out


def leg_line_trustworthy(mid, leg):
    """A parsed leg line that cannot be the leg's price."""
    try:
        line = float(mid[leg]["line"])
        price = int(mid[leg]["price"])
    except (TypeError, ValueError, KeyError):
        return None
    if line == price:
        return None            # bet cell carried no line: line IS price
    return line


def classify(mid):
    """(league, market) from the row identity, or (None, None).

    Identity looks like 'NBA BKN Nets ATL Hawks Today 7:45 PM /
    table / Alt Spread ... +5.5 / ...' — the league is the first
    token and the market columns name Spread or Total. Moneyline
    and non-basketball rows return (None, None).
    """
    ident = stable_row_identity(mid.get("cells")) or ""
    if not ident:
        sides = (mid["leg1"].get("side", "") + " "
                 + mid["leg2"].get("side", ""))
        ident = sides
    head = ident.split(" ", 1)[0].strip().upper()
    league = head if head in ("NBA", "NCAAB") else None
    if league is None:
        return None, None
    low = ident.lower()
    if "total" in low:
        return league, "total"
    if "spread" in low:
        return league, "spread"
    return league, None


def window_numbers(lo, hi):
    """(full, endpoints): integers strictly between lo and hi count
    full; whole-number endpoints count half (win + push)."""
    full = [n for n in range(int(lo) + 1, int(hi) + 1) if lo < n < hi]
    ends = [int(e) for e in (lo, hi)
            if float(e).is_integer() and int(e) not in full]
    return full, ends


def spread_score(league, a, b):
    """Modeled both-win % for a spread middle fav -a / dog +b."""
    lo, hi = min(a, b), max(a, b)
    full, ends = window_numbers(lo, hi)
    table = MARGIN_PCT[league]
    tail = MARGIN_TAIL[league]
    score = sum(table.get(m, tail) for m in full)
    score += 0.5 * sum(table.get(m, tail) for m in ends)
    return round(score, 1), full + ends


def total_score(league, l1, l2):
    """Modeled both-win % for a totals middle Over l1 / Under l2."""
    lo, hi = min(l1, l2), max(l1, l2)
    full, ends = window_numbers(lo, hi)
    table = TOTAL_PCT[league]
    tail = TOTAL_TAIL[league]
    score = sum(table.get(t, tail) for t in full)
    score += 0.5 * sum(table.get(t, tail) for t in ends)
    return round(score, 1), full + ends


def score_mid(mid):
    """(league, market, modeled_pct, window_values) or None."""
    league, market = classify(mid)
    if league is None or market is None:
        return None
    ident = stable_row_identity(mid.get("cells")) or ""
    lines = identity_lines(ident, market)
    if len(lines) >= 2:
        l1, l2 = lines[0], lines[1]
    else:
        l1 = leg_line_trustworthy(mid, "leg1")
        l2 = leg_line_trustworthy(mid, "leg2")
        if l1 is None or l2 is None:
            return None
    if market == "spread":
        pct, values = spread_score(league, abs(l1), abs(l2))
    else:
        pct, values = total_score(league, l1, l2)
    width = (abs(abs(l2) - abs(l1)) if market == "spread"
             else abs(l2 - l1))
    spanky = False
    if league == "NCAAB":
        bar_w = (SPANKY_SPREAD_WIDTH if market == "spread"
                 else SPANKY_TOTAL_WIDTH)
        try:
            prices_ok = all(
                int(mid[l]["price"]) >= SPANKY_PRICE_FLOOR
                for l in ("leg1", "leg2"))
        except (TypeError, ValueError, KeyError):
            prices_ok = False
        spanky = width >= bar_w and prices_ok
    return league, market, pct, values, width, spanky


def qualifies(mid):
    # NOTE: mid["window"] from the parser is computed off the raw leg
    # lines, which for price-only bet cells are the PRICES — never
    # trust it. The width used here comes from the recovered lines.
    if not (price_in(mid["leg1"]["price"], CAP, 100000)
            and price_in(mid["leg2"]["price"], CAP, 100000)):
        return False, None
    scored = score_mid(mid)
    if scored is None:
        return False, None
    if scored[4] < MIN_WINDOW:
        return False, None
    bar = SPREAD_BAR if scored[1] == "spread" else TOTAL_BAR
    return (scored[2] >= bar or scored[5]), scored


def collect_segments(page, smaller_flag):
    """Full-Time AND Full-Time+ segments (view setting clicks)."""
    mids = []
    for segment in ("Full-Time", "Full-Time+"):
        try:
            loc = page.get_by_text(segment, exact=True)
            if loc.count():
                loc.first.click()
                page.wait_for_timeout(2500)
        except Exception:  # noqa: BLE001 - segment control may not exist
            pass
        got, _ = collect_middles(page)
        if smaller_flag:
            for m in got:
                m["smaller_bets"] = True
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
                  "basketball-key-middles skipped, no prices invented.")
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
            print("basketball-key-middles: zero middles parsed — "
                  "send a dump back. No state written.",
                  file=sys.stderr)
            return 2
        deliverables = []
        lines = [f"## {now_stamp()} — basketball-key-middles "
                 f"(NBA/NCAAB cluster rule, modeled bars: spreads "
                 f">=10%, totals >=8%; plus SPANKY tier: CBB "
                 f"spreads >=1.5 pts / totals >=2.5 pts at -125 or "
                 f"better): {len(full)} + "
                 f"{len(smaller)} rows parsed, qualifying only below"]
        for mid in full + smaller:
            ok, scored = qualifies(mid)
            if not ok:
                continue
            league, market, pct, values = scored[:4]
            tier = ("MODELED" if pct >= (SPREAD_BAR if market == "spread" else TOTAL_BAR)
                    else "SPANKY")
            _l1, _l2 = mid["leg1"], mid["leg2"]
            _ident = stable_row_identity(mid.get("cells"))
            if not _ident:
                _ident = (f"{league} | {market} | "
                          f"{strip_volatile(_l1['side'])} | "
                          f"{strip_volatile(_l2['side'])}")
            key = f"{_ident} | {_l1['book']} | {_l2['book']}"
            entry = {"game": _ident, "league": league,
                     "market": market, "tier": tier,
                     "modeled_pct": pct,
                     "window_values": values,
                     "cluster": (f"{league} {'margin cluster' if market == 'spread' else 'total window'}"
                                 f" ~{pct:.1f}%"),
                     "clean_tier": all(
                         price_in(mid[l]["price"], CLEAN, 100000)
                         for l in ("leg1", "leg2")),
                     "leg1": f"{_l1['side']}, {_l1['book']}, "
                             f"{_l1['price']}",
                     "leg2": f"{_l2['side']}, {_l2['book']}, "
                             f"{_l2['price']}",
                     "smaller_bets": bool(mid.get("smaller_bets")),
                     "first_reported": now_stamp(),
                     "last_seen": now_stamp()}
            if key not in state["entries"]:
                state["entries"][key] = entry
                deliverables.append((key, entry))
            else:
                state["entries"][key]["last_seen"] = now_stamp()
            lines.append(
                f"{league} {market.upper()} [{tier}] ~{pct:.1f}%: "
                f"{key} window {values} :: "
                f"{entry['leg1']} / {entry['leg2']}")
        # Chat-delivery wiring (same pattern as the football key
        # scan, 2026-11-10): bank NEW middles as NEW MIDDLE lines
        # directly under the header, count in the header.
        new_lines = []
        for key, e in deliverables:
            tag = "SMALLER BETS " if e["smaller_bets"] else ""
            label = (f"~{e['modeled_pct']:.1f}%"
                     if e.get("tier") != "SPANKY"
                     else "SPANKY baseline")
            new_lines.append(
                f"NEW MIDDLE: {tag}{e['league']} "
                f"{e['market'].upper()} {label}: "
                f"{key} window {e['window_values']} :: "
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
            label = (f"modeled {e['modeled_pct']:.1f}%"
                     if e.get("tier") != "SPANKY"
                     else "SPANKY baseline")
            print(f"{tag}BASKETBALL MIDDLE ({e['league']} "
                  f"{e['market']}, {label}): "
                  f"{key} — {e['leg1']} / {e['leg2']}")
        if not deliverables:
            print("basketball-key-middles: no new basketball middles.")
        return 0
    finally:
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
