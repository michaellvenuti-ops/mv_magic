#!/usr/bin/env python3
"""Futures arb pairing scan (PC port; built live Oct 31, 2026).

Pairs a team's outright at its BEST NJ sportsbook price with that same
team's NO on Kalshi / Polymarket US. The pair covers every outcome;
when book implied % + No cost % < 97% (3% edge bar, Oct 31, 2026), the difference is guaranteed
profit on paper.

NO PRICE RULE: the No price is read from the exchange's OWN page/API
(Kalshi market no_ask in cents; Polymarket market outcome prices).
A No price is NEVER derived as 100 - Yes %. A team whose No price
cannot be read cannot form an arb pair this run — it can still appear
in a price-gap flag.

PRICE-GAP FLAGS are NOT locks: best book implied % vs exchange YES %
differing by >= 5 implied points, labeled exactly
"price gap — not a lock", no stakes, no profit figure.

Stakes on a $1,000 book leg, d = book decimal odds, c = No cost as a
fraction: G = 1000 * d; No stake = G * c; guaranteed profit =
G * (1 - c) - 1000. Profit % is of total staked.

Input: data/futures-board-<date>.json from futures_board.py (falls
back to the newest futures-board-*.json and says so). Stale cells are
excluded from all math. Dedupe vs data/arbitrage-state.json and
data/kalshi-poly-arb-state.json entries (same market/team).

State shape matches live: {"pairs": {key: {...}}, "created",
"last_run"}. Banked: data/futures-pairs-<date>.md. Alert-only.
"""
import argparse
import glob
import json
import sys

import requests

from config import DATA_DIR
from odds_common import (bank, implied_pct, decimal_odds, now_stamp,
                         parse_american, today_str)
from state import load_state

SCAN = "futures-pairs"
BOOK_COLS = ["Bet365", "BetMGM", "DraftKings", "Caesars", "FanDuel",
             "BetRivers", "Hard Rock"]
ARB_SUM_MAX = 97.0
GAP_MIN = 5.0
UA = {"User-Agent": "Mozilla/5.0"}


def load_board_json():
    exact = DATA_DIR / f"futures-board-{today_str()}.json"
    if exact.exists():
        return json.load(open(exact)), exact.name, False
    files = sorted(glob.glob(str(DATA_DIR / "futures-board-*.json")))
    if not files:
        return None, None, False
    return json.load(open(files[-1])), files[-1], True


def kalshi_no(series_map):
    """{market heading: {team: no_cost_pct}} from Kalshi's own quotes."""
    out = {}
    for heading, series in series_map.items():
        try:
            data = requests.get(
                "https://api.elections.kalshi.com/trade-api/v2/markets"
                f"?series_ticker={series}&status=open&limit=200",
                headers=UA, timeout=30).json()
        except (requests.RequestException, ValueError):
            continue
        teams = {}
        for m in data.get("markets", []):
            title = m.get("yes_sub_title") or m.get("title") or ""
            no_ask = m.get("no_ask")
            if not isinstance(no_ask, (int, float)):
                # Same Kalshi fixed-point migration as futures_board:
                # integer-cent no_ask is gone; live field is the
                # dollar string no_ask_dollars ("0.8400").
                try:
                    no_ask = round(float(m.get("no_ask_dollars")) * 100)
                except (TypeError, ValueError):
                    no_ask = None
            if title and isinstance(no_ask, (int, float)):
                teams[title] = float(no_ask)  # cents = percent
        out[heading] = teams
    return out


def poly_no(slug_map):
    """{market heading: {team: no_cost_pct}} from Polymarket's own
    outcome prices (the No outcome's quoted price, never 100 - Yes)."""
    out = {}
    for heading, slug in slug_map.items():
        try:
            data = requests.get("https://gamma-api.polymarket.com/events"
                                f"?slug={slug}", headers=UA,
                                timeout=30).json()
        except (requests.RequestException, ValueError):
            continue
        teams = {}
        events = data if isinstance(data, list) else [data]
        for ev in events:
            for m in ev.get("markets", []):
                outcomes = m.get("outcomes") or []
                prices = m.get("outcomePrices") or []
                if isinstance(outcomes, str):
                    outcomes = json.loads(outcomes)
                if isinstance(prices, str):
                    prices = json.loads(prices)
                name = m.get("groupItemTitle") or ""
                for outcome, price in zip(outcomes, prices):
                    if str(outcome).lower() == "no" and name:
                        try:
                            teams[name] = round(float(price) * 100, 2)
                        except (TypeError, ValueError):
                            pass
        out[heading] = teams
    return out


def _match(name_map, team):
    if not name_map:
        return None
    if team in name_map:
        return name_map[team]
    low = team.lower()
    for k, v in name_map.items():
        if k.lower() in low or low in k.lower():
            return v
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series-map", default=None,
                    help="JSON file {heading: kalshi_series} "
                         "(defaults to futures_board.MARKETS)")
    args = ap.parse_args()
    board, fname, fallback = load_board_json()
    if board is None:
        print("futures-pairs: no futures-board JSON in data/ — run "
              "futures_board.py first.", file=sys.stderr)
        return 2
    from futures_board import MARKETS
    k_series = {m["heading"]: m["kalshi_series"] for m in MARKETS}
    p_slug = {m["heading"]: m["poly_slug"] for m in MARKETS}
    k_no = kalshi_no(k_series)
    p_no = poly_no(p_slug)
    # Cross-scan dedupe: teams already covered by arb states.
    covered = set()
    for other in ("arbitrage", "kalshi-poly-arb"):
        try:
            st = load_state(other)
            for key in st.get("entries", {}):
                covered.add(key.lower())
        except Exception:  # noqa: BLE001
            pass
    state_path = DATA_DIR / "futures-pairs-state.json"
    state = (json.load(open(state_path)) if state_path.exists()
             else {"pairs": {}, "created": today_str()})
    pairs = state.setdefault("pairs", {})
    deliverables, gaps = [], []
    lines = [f"## {now_stamp()} — futures pairs from {fname}"
             + (" (FALLBACK: not today's board)" if fallback else "")]
    for market in board.get("markets", []):
        heading = market["heading"]
        cols = market["cols"]
        for row in market["rows"]:
            team = row[0]
            cells = dict(zip(cols, row[1:]))
            best_book, best_price, best_impl = None, None, None
            for book in BOOK_COLS:
                cell = cells.get(book) or {}
                if cell.get("stale") or not cell.get("v"):
                    continue
                price = parse_american(cell["v"])
                impl = implied_pct(price)
                if impl is not None and (best_impl is None
                                         or impl < best_impl):
                    best_book, best_price, best_impl = book, price, impl
            for exchange, no_map, yes_col in (
                    ("Kalshi", k_no.get(heading), "Kalshi"),
                    ("Polymarket US", p_no.get(heading), "Polymarket US")):
                yes_pct = (cells.get(yes_col) or {}).get("pct")
                if best_impl is not None and yes_pct is not None and \
                        abs(best_impl - yes_pct) >= GAP_MIN:
                    gkey = f"{heading} | {team} | {best_book} | {exchange}"
                    if gkey not in pairs:
                        gap = {"market": heading, "team": team,
                               "book": best_book, "book_price": best_price,
                               "book_implied_pct": round(best_impl, 2),
                               "exchange": exchange, "yes_pct": yes_pct,
                               "gap_pts": round(best_impl - yes_pct, 2),
                               "kind": "gap", "first_reported": now_stamp(),
                               "last_seen": today_str()}
                        pairs[gkey] = gap
                        gaps.append(gap)
                no_cost = _match(no_map, team)
                if best_impl is None or no_cost is None:
                    continue
                if best_impl + no_cost >= ARB_SUM_MAX:
                    continue
                if any(team.lower() in c and heading.split()[0].lower()
                       in c for c in covered):
                    lines.append(f"skip (covered by arb state): "
                                 f"{heading} {team}")
                    continue
                d = decimal_odds(best_price)
                gross = 1000.0 * d
                c = no_cost / 100.0
                no_stake = gross * c
                profit = gross * (1 - c) - 1000.0
                total = 1000.0 + no_stake
                key = f"{heading} | {team} | {best_book} | {exchange}"
                prior = pairs.get(key)
                changed = (prior is None or
                           abs(profit / total * 100
                               - prior.get("profit_pct", 0)) >= 1.0)
                entry = {"market": heading, "team": team, "book": best_book,
                         "book_price": f"{best_price:+d}",
                         "book_implied_pct": round(best_impl, 2),
                         "exchange": exchange,
                         "no_price": f"{no_cost:g}%",
                         "no_cost_pct": no_cost,
                         "no_stake_usd": round(no_stake, 2),
                         "profit_usd": round(profit, 2),
                         "profit_pct": round(profit / total * 100, 2),
                         "kind": "arb",
                         "first_reported": (prior or {}).get(
                             "first_reported", now_stamp()),
                         "last_seen": today_str()}
                pairs[key] = entry
                if changed:
                    deliverables.append(entry)
                lines.append(
                    f"ARB {'NEW/CHANGED' if changed else 're-seen'}: "
                    f"{heading} {team} — {best_book} {best_price:+d} $1,000 / "
                    f"{exchange} No {no_cost:g}% ${no_stake:,.2f} -> "
                    f"${profit:,.2f} ({entry['profit_pct']}%)")
    state["last_run"] = now_stamp()
    with open(state_path, "w") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    bank(SCAN, lines)
    deliverables.sort(key=lambda e: -e["profit_pct"])
    for e in deliverables[:10]:
        print(f"FUTURES ARB: {e['team']} ({e['market']}) — Leg 1: "
              f"{e['book']} {e['book_price']} $1,000 | Leg 2: "
              f"{e['exchange']} No {e['no_price']} ${e['no_stake_usd']:,.2f} "
              f"| guaranteed profit ${e['profit_usd']:,.2f} "
              f"({e['profit_pct']}%)")
    for g in gaps:
        print(f"price gap — not a lock: {g['team']} ({g['market']}) — "
              f"{g['book']} implied {g['book_implied_pct']}% vs "
              f"{g['exchange']} Yes {g['yes_pct']}% ({g['gap_pts']:+} pts)")
    if not deliverables and not gaps:
        print("futures-pairs: no new/changed pairs (silent).")
    print("Footer: futures prices move slowly but stale cells exist — "
          "verify both legs live before placing; exchange No legs can "
          "partially fill.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
