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
import re
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
     "kalshi_series": "KXSB", "poly_slug": "super-bowl-champion-2027",
     "poly_query": "Super Bowl champion",
     "poly_match": [["super", "bowl", "champion"],
                        ["pro", "football", "champion"]]},
    {"heading": "NFL MVP 2026",
     "vi": "https://www.vegasinsider.com/nfl/odds/futures/",
     "kalshi_series": "KXNFLMVP", "poly_slug": "nfl-mvp-2026",
     "poly_query": "NFL MVP",
     "poly_match": [["pro", "football", "mvp"],
                        ["nfl", "mvp"]]},
    {"heading": "NBA Championship 2026-27",
     "vi": "https://www.vegasinsider.com/nba/odds/futures/",
     "kalshi_series": "KXNBACHAMP", "poly_slug": "nba-champion-2027",
     "poly_query": "NBA champion",
     "poly_match": [["nba", "champion"]]},
    {"heading": "Stanley Cup 2026-27",
     "vi": "https://www.vegasinsider.com/nhl/odds/futures/",
     "kalshi_series": "KXNHLCHAMP", "poly_slug": "stanley-cup-champion-2027",
     "poly_query": "Stanley Cup champion",
     "poly_match": [["nhl", "champion"],
                        ["stanley", "cup"]]},
    {"heading": "Heisman Trophy 2026",
     "vi": "https://www.vegasinsider.com/college-football/odds/futures/",
     "kalshi_series": "KXHEISMAN", "poly_slug": "heisman-trophy-2026",
     "poly_query": "Heisman Trophy",
     "poly_match": [["heisman", "winner"],
                        ["heisman"]]},
]


def _clean_team(cell):
    """VI team cells now embed a JSON-LD blob and the price, e.g.
    '{"@type":"SportsTeam","name":"49ers (+540)",...}   49ers'.
    Keep the trailing display name; drop any '(+540)' suffix."""
    text = str(cell).rsplit("}", 1)[-1].strip()
    return re.sub(r"\s*\([+-]\d+\)\s*$", "", text).strip()


def _clean_price(cell):
    """VI price cells now read '+550     +' (price + expander glyph)."""
    m = re.search(r"[+-]\d+", str(cell))
    return m.group(0) if m else None


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
        if not rows:
            continue
        header = rows[0]
        # Team column: an explicit Team/Player header, else the
        # first column (VI now heads it "Time" on futures pages).
        team_idx = 0
        for i, h in enumerate(header):
            if h.strip().lower() in ("team", "player"):
                team_idx = i
                break
        # Book columns by header, spaces removed ("HardRock" on the
        # NBA page must match BOOKS' "Hard Rock").
        book_cols = {}
        for i, h in enumerate(header):
            for book in BOOKS:
                if book.lower().replace(" ", "") == \
                        h.strip().lower().replace(" ", ""):
                    book_cols[book] = i
        dicts = rows_to_dicts(rows)
        for d in dicts:
            raw_team = d.get(header[team_idx], "")
            team = _clean_team(raw_team) if raw_team.strip() else None
            if not team:
                continue
            for book, idx in book_cols.items():
                val = _clean_price(d.get(header[idx], ""))
                if val:
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
        if not isinstance(ask, (int, float)):
            # Kalshi's fixed-point migration removed the integer-cent
            # yes_ask; the live field is a dollar string ("0.1600").
            try:
                ask = round(float(m.get("yes_ask_dollars")) * 100)
            except (TypeError, ValueError):
                ask = None
        if title and isinstance(ask, (int, float)):
            out[title] = ask  # cents (or dollars x100) = percent
    return out


def _poly_event_by_slug(slug):
    try:
        data = fetch("https://gamma-api.polymarket.com/events"
                     f"?slug={slug}").json()
    except (requests.RequestException, ValueError):
        return None
    events = data if isinstance(data, list) else [data]
    return events[0] if events else None


def _poly_resolve_slug(market):
    """Configured slugs rot (all five returned [] on 2026-11-10).
    Fall back to Gamma public-search. Polymarket's live titles use
    euphemisms ('Pro Football: 2027 Champion' is the Super Bowl
    market), so each market carries ordered word-sets to try, and
    side markets (conference / division / winning-X) are excluded
    unless a word-set itself names them."""
    query = market.get("poly_query") or market["heading"]
    try:
        data = fetch("https://gamma-api.polymarket.com/public-search"
                     f"?q={query}&limit_per_type=10").json()
    except (requests.RequestException, ValueError):
        return None
    events = [ev for ev in data.get("events", [])
              if ev.get("active") and not ev.get("closed")]
    word_sets = market.get("poly_match") or [
        [w for w in re.split(r"\s+", query.lower()) if len(w) > 2]]
    for words in word_sets:
        excludes = [x for x in ("conference", "division", "winning")
                    if x not in words]
        for ev in events:
            title = (ev.get("title") or "").lower()
            if all(w in title for w in words) and \
                    not any(x in title for x in excludes):
                print(f"futures-board: poly slug for "
                      f"{market['heading']} resolved to "
                      f"{ev.get('slug')}", file=sys.stderr)
                return ev.get("slug")
    return None


def poly_yes(market):
    """Polymarket public Gamma API -> {outcome: yes_pct}."""
    ev = _poly_event_by_slug(market["poly_slug"])
    if ev is None:
        slug = _poly_resolve_slug(market)
        if slug:
            ev = _poly_event_by_slug(slug)
    if ev is None:
        return {}
    out = {}
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
        pyes = poly_yes(market)
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
