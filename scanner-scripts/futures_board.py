#!/usr/bin/env python3
"""Futures board builder (PC port).

Reads futures prices from public pages and writes
data/futures-board-<date>.json in the SAME shape Chet's live board
uses: {"markets": [{"heading", "note", "cols": [...], "rows":
[[team, {cell}, ...], ...]}]} — book cells {"v": "+600"}; Kalshi /
Polymarket US cells {"v": "16%", "pct": 16} (the team's YES %).

Sources: VegasInsider + Covers futures pages for the books; Kalshi and
Polymarket US public APIs/pages for exchange Yes %.

STALE CELLS (manual review): futures pages keep dead prices up for
days. Chet's live board excludes cells it has verified as stale; on
this PC port that review is MANUAL — edit STALE_CELLS below with
"Market | Team | Book" keys after eyeballing the board against a
second source. Excluded cells are written with "stale": true so
futures_pairs.py skips them in all pairing math. Seed entries below
are the cells Chet's Oct 31, 2026 board flagged stale — re-verify
before trusting them on a new date.

CALIBRATION: page layouts change; run with --dump to save raw pages
to data/. Zero parsed markets = exit 2, nothing written. No price is
ever invented: a cell that does not parse is simply absent.
"""
import argparse
import json
import sys

import requests

from config import DATA_DIR
from odds_common import now_stamp, today_str
from scrape import parse_tables, rows_to_dicts

UA = {"User-Agent": "Mozilla/5.0"}

# "Market heading | Team | Book" — manual stale-cell review list.
STALE_CELLS = [
    # Verified stale on Chet's 2026-10-31 board (re-verify for new dates):
    "Super Bowl LXI Winner | Rams | DraftKings",
    "Super Bowl LXI Winner | Eagles | DraftKings",
    "NBA Championship 2026-27 | 76ers | FanDuel",
    "NBA Championship 2026-27 | Lakers | FanDuel",
    "Stanley Cup 2026-27 | Panthers | Hard Rock",
    "Stanley Cup 2026-27 | Capitals | Caesars",
    "Stanley Cup 2026-27 | Devils | Hard Rock",
    "Stanley Cup 2026-27 | Mammoth | Hard Rock",
    "Stanley Cup 2026-27 | Senators | Caesars",
]

BOOKS = ["Bet365", "BetMGM", "DraftKings", "Caesars", "FanDuel",
         "BetRivers", "Hard Rock"]

MARKETS = [
    {"heading": "Super Bowl LXI Winner",
     "vi": "https://www.vegasinsider.com/nfl/odds/futures/",
     "kalshi_series": "KXSB", "poly_slug": "super-bowl-champion-2027"},
    {"heading": "NFL MVP 2026",
     "vi": "https://www.vegasinsider.com/nfl/odds/futures/",
     "kalshi_series": "KXNFLMVP", "poly_slug": "nfl-mvp-2026"},
    {"heading": "NBA Championship 2026-27",
     "vi": "https://www.vegasinsider.com/nba/odds/futures/",
     "kalshi_series": "KXNBACHAMP", "poly_slug": "nba-champion-2027"},
    {"heading": "Stanley Cup 2026-27",
     "vi": "https://www.vegasinsider.com/nhl/odds/futures/",
     "kalshi_series": "KXNHLCHAMP", "poly_slug": "stanley-cup-champion-2027"},
    {"heading": "Heisman Trophy 2026",
     "vi": "https://www.vegasinsider.com/college-football/odds/futures/",
     "kalshi_series": "KXHEISMAN", "poly_slug": "heisman-trophy-2026"},
]


def fetch(url):
    resp = requests.get(url, headers=UA, timeout=30)
    resp.raise_for_status()
    return resp


def book_prices(market):
    """Best-effort VegasInsider parse -> {team: {book: "+600"}}."""
    try:
        html_text = fetch(market["vi"]).text
    except requests.RequestException:
        return {}
    tables, anon = parse_tables(html_text)
    out = {}
    for rows in list(tables.values()) + anon:
        dicts = rows_to_dicts(rows)
        for d in dicts:
            team = None
            for k, v in d.items():
                if k.strip().lower() in ("team", "player", "") and v.strip():
                    team = v.strip()
            if not team:
                continue
            for book in BOOKS:
                for k, v in d.items():
                    if book.lower() in k.lower():
                        val = str(v).strip()
                        if val and (val[0] in "+-" or val.isdigit()):
                            out.setdefault(team, {})[book] = val
    return out


def kalshi_yes(series):
    """Kalshi public API -> {team-ish title: yes_pct}. Best-effort."""
    try:
        data = fetch("https://api.elections.kalshi.com/trade-api/v2/"
                     f"markets?series_ticker={series}&status=open"
                     "&limit=200").json()
    except (requests.RequestException, ValueError):
        return {}
    out = {}
    for m in data.get("markets", []):
        title = m.get("yes_sub_title") or m.get("title") or ""
        ask = m.get("yes_ask")
        if title and isinstance(ask, (int, float)):
            out[title] = ask  # Kalshi quotes cents = percent
    return out


def poly_yes(slug):
    """Polymarket public Gamma API -> {outcome: yes_pct}."""
    try:
        data = fetch("https://gamma-api.polymarket.com/events"
                     f"?slug={slug}").json()
    except (requests.RequestException, ValueError):
        return {}
    out = {}
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
                if str(outcome).lower() == "yes" and name:
                    try:
                        out[name] = round(float(price) * 100)
                    except (TypeError, ValueError):
                        pass
    return out


def _match(name_map, team):
    if team in name_map:
        return name_map[team]
    low = team.lower()
    for k, v in name_map.items():
        if k.lower() in low or low in k.lower():
            return v
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()
    markets_out = []
    for market in MARKETS:
        books = book_prices(market)
        kyes = kalshi_yes(market["kalshi_series"])
        pyes = poly_yes(market["poly_slug"])
        teams = sorted(set(books) | set(kyes) | set(pyes))
        if not teams:
            print(f"futures-board: {market['heading']}: nothing parsed",
                  file=sys.stderr)
            continue
        cols = BOOKS + ["Kalshi", "Polymarket US"]
        rows = []
        for team in teams:
            cells = []
            for book in BOOKS:
                price = books.get(team, {}).get(book)
                cell = {"v": price} if price else {}
                if price and (f"{market['heading']} | {team} | {book}"
                              in STALE_CELLS):
                    cell["stale"] = True
                cells.append(cell)
            for source in (kyes, pyes):
                pct = _match(source, team)
                cells.append({"v": f"{pct}%", "pct": pct}
                             if pct is not None else {})
            rows.append([team] + cells)
        markets_out.append({"heading": market["heading"],
                            "note": f"PC board, read {now_stamp()}",
                            "cols": cols, "rows": rows})
    if not markets_out:
        print("futures-board: zero markets parsed — see --dump",
              file=sys.stderr)
        return 2
    out_path = DATA_DIR / f"futures-board-{today_str()}.json"
    with open(out_path, "w") as fh:
        json.dump({"markets": markets_out}, fh, indent=1)
    print(f"futures-board: wrote {out_path} "
          f"({len(markets_out)} markets).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
