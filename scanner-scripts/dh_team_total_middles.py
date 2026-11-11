#!/usr/bin/env python3
"""Team-total middles on the key team scores — Kalshi edition.

REPLACES the Oct 31 gate stub. The stub existed because DarkHorse
has NO team totals anywhere (verified definitively Oct 31, 2026:
identical market-filter trees in every finder, browse table offers
only ML/Spreads/Totals/Player Props, game pages show one game
total, never split per team). On Nov 10, 2026 Michael tapped the
hunt for another source, and Kalshi has one: series
KXNFLTEAMTOTAL ("Pro Football Team Total Points") lists, for every
NFL game, a ladder of "Team over X.5 points" binary markets with
live bid/ask — 381 open markets across 14 games in the first
check. This scan reads that public API (no key, no browser, no
DarkHorse profile) and pairs Kalshi thresholds into middles.

THE KEYS (Chet's nflverse scan, 14,824 team-games): team scores
land on 20 in 6.955% of team-games, 17 in 6.732%, 24 in 6.476%,
27 in 5.626% — the TD/FG lattice (20 = 2 TD + 2 FG, 17 = 2 TD +
1 FG, 24 = 3 TD + 1 FG) — against ~1-3.7% for ordinary scores
(18: 0.836%, 22: 1.686%, 25: 1.093%, 21: 3.676%). The four keys
cover 25.8% of all team scores. Game totals have no such keys;
team totals do.

PAIR ALGEBRA: for strikes a < b (half-point strikes only; whole
strikes settle ambiguously at the number), YES over a + NO over b
both win exactly when the team scores in [a+0.5, b-0.5]. Each
contract pays $1: stake C = yes_ask(a) + no_ask(b) returns $2 when
both win and $1 otherwise (exactly one leg always pays), so the
pair is +EV when C < 1 + P, with P the modeled both-win
probability from the empirical distribution below. A 2-cent fee
buffer is charged against that (Kalshi taker fees run ~1-2 cents
per contract near the money). A pair qualifies when:
  - its both-win window CONTAINS a key (17/20/24/27),
  - C + 0.02 < 1 + P  (strictly +EV after the fee buffer),
  - C <= 1.10         (worst case bounded at -10c per $1 pair),
  - both asks are real (0 < ask < 1) with a bid on the yes side,
  - the window is a MIDDLE, not a strangle: at most 5 numbers wide,
    sitting on the team's market-implied median (the strike whose
    YES ask is nearest 50c) — the window must reach within 3 points
    of that median, and neither leg may cost more than 85c. The
    modeled P comes from the league-wide distribution, which is a
    local-density approximation: honest for narrow windows near a
    team's expected score, garbage for a 14-number window under a
    good offense (a YES 3.5 @ 99c + NO 17.5 @ 10c "straddle" on a
    team totaled at 24 is the failure this rule exists to block).
The classic shape is the stub's target: YES over 19.5 + NO over
20.5, both win at exactly 20.

Alert-only, state-deduped (a pair re-alerts as CHANGED when its
combined cost drops 3c+). No Smaller Bets pass — Kalshi contracts
are $1 units, there are no stake tiers to preserve. NFL only: the
distribution is NFL-derived. Banked:
data/team-total-middles-<date>.md.
"""
import sys

import requests

from config import DATA_DIR  # noqa: F401  (kept for suite parity)
from odds_common import bank, now_stamp
from state import load_state, save_state

SCAN = "team-total-middles"
SERIES = "KXNFLTEAMTOTAL"
API = "https://api.elections.kalshi.com/trade-api/v2/markets"
KEYS = (17, 20, 24, 27)
FEE_BUFFER = 0.02
MAX_COMBINED = 1.10
MAX_WINDOW = 5        # both-win numbers; wider is a strangle
MEDIAN_REACH = 3      # window must reach within 3 pts of median
MAX_LEG = 0.85        # neither leg may cost more than 85c

# Empirical team-score landing % (14,824 NFL team-games).
TEAM_SCORE_PCT = {
    0: 1.268, 1: 0.0, 2: 0.02, 3: 2.213, 4: 0.0, 5: 0.04,
    6: 1.889, 7: 2.84, 8: 0.182, 9: 1.531, 10: 5.147, 11: 0.27,
    12: 0.992, 13: 4.661, 14: 3.697, 15: 0.992, 16: 3.582,
    17: 6.732, 18: 0.836, 19: 2.354, 20: 6.955, 21: 3.676,
    22: 1.686, 23: 4.567, 24: 6.476, 25: 1.093, 26: 2.476,
    27: 5.626, 28: 2.901, 29: 1.228, 30: 3.042, 31: 4.486,
    32: 0.648, 33: 1.423, 34: 3.299, 35: 1.498, 36: 0.675,
    37: 1.403, 38: 2.057, 39: 0.25, 40: 0.553, 41: 1.221,
    42: 0.695, 43: 0.263, 44: 0.445, 45: 0.675, 46: 0.054,
    47: 0.162, 48: 0.411, 49: 0.25, 50: 0.027, 51: 0.148,
    52: 0.121, 53: 0.013, 54: 0.047, 55: 0.047, 56: 0.061,
    57: 0.013, 58: 0.013, 59: 0.04, 60: 0.0,
}


def _dollars(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _american(cents):
    """Kalshi price (0-1) -> American odds string."""
    if cents <= 0 or cents >= 1:
        return "n/a"
    if cents >= 0.5:
        return f"-{round(100 * cents / (1 - cents))}"
    return f"+{round(100 * (1 - cents) / cents)}"


def fetch_markets():
    resp = requests.get(
        API, params={"series_ticker": SERIES, "status": "open",
                     "limit": 1000},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("markets", [])


def collect_positions(markets):
    """{(event, team): {strike: (yes_ask, no_ask)}} — .5 strikes
    with two-sided interest only."""
    games = {}
    for m in markets:
        strike = m.get("floor_strike")
        if strike is None or (float(strike) * 2) % 2 != 1:
            continue  # half-point strikes only
        ya = _dollars(m.get("yes_ask_dollars"))
        na = _dollars(m.get("no_ask_dollars"))
        yb = _dollars(m.get("yes_bid_dollars"))
        if ya is None or na is None:
            continue
        if not (0 < ya < 1 and 0 < na < 1):
            continue
        if yb is not None and yb <= 0:
            continue  # nobody bidding the yes side at all
        sub = m.get("yes_sub_title") or ""
        team = sub.split(" over ")[0].strip()
        if not team:
            continue
        key = (m.get("event_ticker") or "", team)
        games.setdefault(key, {})[float(strike)] = (ya, na)
    return games


def _implied_median(ladder):
    """Strike whose YES ask sits nearest 50c — the market's own
    center for this team's score."""
    return min(ladder, key=lambda s: abs(ladder[s][0] - 0.5))


def find_middles(games):
    """All qualifying middles, best EV margin first."""
    out = []
    for (event, team), ladder in games.items():
        strikes = sorted(ladder)
        median = _implied_median(ladder)
        for i, a in enumerate(strikes):
            for b in strikes[i + 1:]:
                lo = int(a + 0.5)
                hi = int(b - 0.5)
                if hi < lo or hi - lo + 1 > MAX_WINDOW:
                    continue
                if not (lo - MEDIAN_REACH <= median
                        <= hi + MEDIAN_REACH):
                    continue
                if ladder[a][0] > MAX_LEG or ladder[b][1] > MAX_LEG:
                    continue
                window = list(range(lo, hi + 1))
                keys_in = [k for k in KEYS if k in window]
                if not keys_in:
                    continue
                pct = sum(TEAM_SCORE_PCT.get(w, 0.0) for w in window)
                combined = ladder[a][0] + ladder[b][1]
                if combined > MAX_COMBINED:
                    continue
                if combined + FEE_BUFFER >= 1 + pct / 100.0:
                    continue
                out.append({
                    "event": event, "team": team, "a": a, "b": b,
                    "window": window, "keys": keys_in, "pct": pct,
                    "yes_ask": ladder[a][0], "no_ask": ladder[b][1],
                    "combined": combined,
                    "ev_margin": 1 + pct / 100.0 - combined,
                })
    out.sort(key=lambda e: -e["ev_margin"])
    return out


def _leg_text(e):
    return (f"Kalshi YES over {e['a']} @ {round(e['yes_ask'] * 100)}c "
            f"({_american(e['yes_ask'])}) / Kalshi NO over {e['b']} "
            f"@ {round(e['no_ask'] * 100)}c "
            f"({_american(e['no_ask'])})")


def _describe(e):
    win = (f"{e['window'][0]}" if len(e['window']) == 1
           else f"{e['window'][0]}-{e['window'][-1]}")
    return (f"{e['event']} {e['team']} team total: {_leg_text(e)} "
            f":: both win on {win} (key "
            f"{'/'.join(str(k) for k in e['keys'])}, modeled "
            f"{e['pct']:.1f}%), combined "
            f"{round(e['combined'] * 100)}c per $1 pair")


def main():
    state = load_state(SCAN)
    entries = state.setdefault("entries", {})
    try:
        markets = fetch_markets()
    except requests.RequestException as exc:
        print(f"team-total-middles: Kalshi fetch failed ({exc})",
              file=sys.stderr)
        return 2
    games = collect_positions(markets)
    middles = find_middles(games)
    best_note = ""
    if not middles:
        # Calibration visibility in scheduler.log only (never
        # banked): how far the best keyed straddle sits from +EV.
        best = None
        for (event, team), ladder in games.items():
            strikes = sorted(ladder)
            median = _implied_median(ladder)
            for i, a in enumerate(strikes):
                for b in strikes[i + 1:]:
                    lo, hi = int(a + 0.5), int(b - 0.5)
                    if hi < lo or hi - lo + 1 > MAX_WINDOW:
                        continue
                    if not (lo - MEDIAN_REACH <= median
                            <= hi + MEDIAN_REACH):
                        continue
                    if ladder[a][0] > MAX_LEG or ladder[b][1] > MAX_LEG:
                        continue
                    window = list(range(lo, hi + 1))
                    if not [k for k in KEYS if k in window]:
                        continue
                    pct = sum(TEAM_SCORE_PCT.get(w, 0.0)
                              for w in window)
                    margin = (1 + pct / 100.0 - FEE_BUFFER
                              - (ladder[a][0] + ladder[b][1]))
                    if best is None or margin > best[0]:
                        best = (margin, team, a, b)
        if best is not None:
            best_note = (f" Best keyed straddle {best[1]} "
                         f"{best[2]}/{best[3]} sits at "
                         f"{best[0] * 100:+.1f}c vs +EV.")
    new_lines, changed_lines, deliverables = [], [], []
    for e in middles:
        key = f"{e['event']}|{e['team']}|{e['a']}|{e['b']}"
        prev = entries.get(key)
        text = _describe(e)
        if prev is None:
            new_lines.append(f"NEW MIDDLE: {text}")
            deliverables.append(e)
        elif e["combined"] <= prev.get("combined", 9) - 0.03:
            changed_lines.append(f"CHANGED MIDDLE: {text}")
            deliverables.append(e)
        entries[key] = {"combined": e["combined"]}
    state["entries"] = entries
    save_state(SCAN, state)
    if new_lines or changed_lines:
        header = (f"## {now_stamp()} — team-total-middles (Kalshi "
                  f"KXNFLTEAMTOTAL; key team scores 17/20/24/27): "
                  f"{len(new_lines)} NEW + {len(changed_lines)} "
                  f"CHANGED middles")
        bank(SCAN, [header] + new_lines + changed_lines)
    for e in deliverables[:10]:
        print(f"TEAM-TOTAL MIDDLE ({e['team']}, keys "
              f"{'/'.join(str(k) for k in e['keys'])}, modeled "
              f"{e['pct']:.1f}%, combined "
              f"{round(e['combined'] * 100)}c): {_leg_text(e)} "
              f"[{e['event']}]")
    print(f"team-total-middles: {len(games)} team-games with "
          f"two-sided ladders, {len(middles)} qualifying straddles, "
          f"{len(new_lines)} new, {len(changed_lines)} changed."
          f"{best_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
