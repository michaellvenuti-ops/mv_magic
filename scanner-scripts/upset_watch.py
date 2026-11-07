#!/usr/bin/env python3
"""Big-favorite-live-losing watch (NFL / college football / NBA / NCAAB).

Polls ESPN's public scoreboards. Pregame spreads exist on the scoreboard
only while a game is SCHEDULED — they vanish once it goes live — so this
script snapshots each scheduled game's spread first, then judges live
games against that frozen pregame line. A game whose pregame spread was
never captured (odds missing, or the script started mid-game) is NEVER
judged: no invented lines, ever.

Football trigger: pregame favorite laying 10.5+ points is strictly
TRAILING on the live scoreboard, and this game has not alerted before.
One alert per game (a favorite that goes back ahead and trails again
does not re-fire).

Basketball trigger (NBA + NCAAB; changed 2026-11-05 at Michael's tap):
the same trailing condition, PLUS the favorite's LIVE moneyline must be
-200 or better (bettor-friendly: -200, -180, any plus price) at the
moment of the check. ESPN's basketball scoreboards carry no usable live
moneyline (live NCAAB games have no odds object at all), so the live ML
is read from ScoresAndOdds' public board — fetched ONLY on runs where a
basketball candidate exists. If the live ML cannot be read for a game,
that game does not alert: never an invented price. One alert per game,
fired on the first check where BOTH conditions hold.

Output: one "TRIGGER: ..." line per new trigger, then a SUMMARY line.
No triggers -> prints "NO TRIGGER" (the cron stays silent on that).
"""
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR
STATE_PATH = DATA_DIR / "upset-watch-state.json"
THRESHOLD = 10.5
ML_GATE = -200  # basketball: live ML must be -200 or better (bettor-friendly)

SPORTS = [
    ("NFL", "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"),
    # ESPN's default CFB scoreboard silently omits some games (found
    # 2026-11-04: Toledo at Sacramento St, an FBS-at-FCS game, was missing
    # from the default board all game, so its upset trigger never fired).
    # Fetch both Division I groups explicitly: 80 = FBS, 81 = FCS.
    ("CFB", "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=80"),
    ("CFB", "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=81"),
    ("NBA", "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"),
    # NCAAB added 2026-11-05 with the basketball ML-gate tweak. groups=50
    # is ESPN's Division I group (the board the closing-lines work uses).
    ("NCAAB", "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?groups=50"),
]

BASKETBALL = {"NBA", "NCAAB"}
SAO_URLS = {
    "NBA": "https://www.scoresandodds.com/nba",
    "NCAAB": "https://www.scoresandodds.com/ncaab",
}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def fetch_text(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def norm_team(name):
    n = name.lower().strip()
    n = n.replace("&", "and").replace(".", "").replace("'", "")
    n = re.sub(r"\s+", " ", n)
    if n.endswith(" st"):
        n = n[:-3] + " state"
    return {"uconn": "connecticut"}.get(n, n)


def parse_price(text):
    t = text.strip().lower()
    if t == "even":
        return 100
    try:
        return int(t.replace("+", ""))
    except ValueError:
        return None


def sao_live_moneylines(html):
    """Map normalized team name -> live moneyline (int) from a
    ScoresAndOdds board page. Rows without a readable live ML are
    simply absent from the map."""
    out = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        td = re.search(r'<td[^>]*data-field="live-moneyline"[^>]*>(.*?)</td>',
                       row, re.S)
        if not td:
            continue
        name_m = re.search(r'aria-label="([^"]+)"', row)
        val_m = re.search(r'<span class="data-value">\s*([^<]+?)\s*</span>',
                          td.group(1))
        if not name_m or not val_m:
            continue
        price = parse_price(val_m.group(1))
        if price is not None:
            out[norm_team(name_m.group(1))] = price
    return out


def parse_game(sport, event):
    comp = event["competitions"][0]
    status = comp["status"]["type"]["name"]
    detail = comp["status"]["type"].get("detail", "")
    competitors = {c["homeAway"]: c for c in comp["competitors"]}
    home, away = competitors["home"], competitors["away"]
    game = {
        "sport": sport,
        "id": event["id"],
        "matchup": f"{away['team']['shortDisplayName']} at {home['team']['shortDisplayName']}",
        "home": home["team"]["shortDisplayName"],
        "away": away["team"]["shortDisplayName"],
        "home_score": home.get("score"),
        "away_score": away.get("score"),
        "status": status,
        "detail": detail,
        "date": event.get("date", "")[:10],
    }
    odds = comp.get("odds") or []
    if odds:
        o = odds[0]
        fav = None
        if o.get("awayTeamOdds", {}).get("favorite"):
            fav = "away"
        elif o.get("homeTeamOdds", {}).get("favorite"):
            fav = "home"
        spread = o.get("spread")
        if fav is None and isinstance(spread, (int, float)):
            # Fallback: ESPN spread is the home team's line
            # (negative = home favorite, positive = home underdog).
            fav = "home" if spread < 0 else "away"
        if fav and isinstance(spread, (int, float)):
            game["fav"] = fav
            game["spread"] = abs(float(spread))
    return game


def main():
    state = {"games": {}}
    if os.path.exists(STATE_PATH):
        try:
            state = json.load(open(STATE_PATH))
        except Exception:
            state = {"games": {}}
    games = state.setdefault("games", {})
    triggers = []
    bb_candidates = []  # basketball: trailing 10.5+ favs awaiting the ML gate
    seen = 0
    for sport, url in SPORTS:
        try:
            data = fetch(url)
        except Exception as exc:  # a failed feed is a missed check, not no-news
            print(f"FEED FAIL {sport}: {exc}", file=sys.stderr)
            continue
        for event in data.get("events", []):
            g = parse_game(sport, event)
            seen += 1
            rec = games.setdefault(g["id"], {
                "sport": sport, "matchup": g["matchup"],
                "date": g["date"], "alerted": False,
            })
            if g["status"] == "STATUS_SCHEDULED":
                # Snapshot the pregame line; refresh it while still scheduled.
                if g.get("fav") and g.get("spread"):
                    rec["fav"] = g["fav"]
                    rec["spread"] = g["spread"]
                    rec["fav_team"] = g["home"] if g["fav"] == "home" else g["away"]
                    rec["dog_team"] = g["away"] if g["fav"] == "home" else g["home"]
            elif g["status"] == "STATUS_IN_PROGRESS":
                if rec.get("fav") and rec.get("spread") and rec["spread"] >= THRESHOLD \
                        and not rec.get("alerted"):
                    try:
                        fav_score = int(g["home_score"] if rec["fav"] == "home" else g["away_score"])
                        dog_score = int(g["away_score"] if rec["fav"] == "home" else g["home_score"])
                    except (TypeError, ValueError):
                        continue
                    if fav_score < dog_score:
                        if sport in BASKETBALL:
                            bb_candidates.append(
                                (sport, rec, g, fav_score, dog_score))
                        else:
                            rec["alerted"] = True
                            triggers.append(
                                f"TRIGGER: {sport} | {g['matchup']} | pregame favorite "
                                f"{rec['fav_team']} -{rec['spread']:g} is LOSING "
                                f"{rec['dog_team']} {dog_score}-{rec['fav_team']} {fav_score} "
                                f"| {g['detail']}")
            elif g["status"] in ("STATUS_FINAL", "STATUS_CANCELED"):
                rec["final"] = True
    # Basketball ML gate: one ScoresAndOdds fetch per sport that has a
    # candidate this run; a game whose live ML is unreadable never alerts.
    if bb_candidates:
        by_sport = {}
        for sport, rec, g, fav_score, dog_score in bb_candidates:
            by_sport.setdefault(sport, []).append((rec, g, fav_score, dog_score))
        for sport, cands in by_sport.items():
            try:
                mls = sao_live_moneylines(fetch_text(SAO_URLS[sport]))
            except Exception as exc:  # a failed board read is a missed check
                print(f"SAO FAIL {sport}: {exc}", file=sys.stderr)
                continue
            for rec, g, fav_score, dog_score in cands:
                ml = mls.get(norm_team(rec["fav_team"]))
                if ml is None:
                    continue
                rec["live_ml"] = ml
                if ml >= ML_GATE:
                    rec["alerted"] = True
                    triggers.append(
                        f"TRIGGER: {sport} | {g['matchup']} | pregame favorite "
                        f"{rec['fav_team']} -{rec['spread']:g} is LOSING "
                        f"{rec['dog_team']} {dog_score}-{rec['fav_team']} {fav_score} "
                        f"| {g['detail']} | live ML {ml:+d} (ScoresAndOdds)")
    # Prune games from before yesterday (keeps the file small; finals stay
    # out of future checks either way).
    with open(STATE_PATH, "w") as fh:
        json.dump(state, fh, indent=1)
    for t in triggers:
        print(t)
    if not triggers:
        print("NO TRIGGER")
    print(f"SUMMARY: {seen} games checked, {len(triggers)} new trigger(s)")


if __name__ == "__main__":
    main()
