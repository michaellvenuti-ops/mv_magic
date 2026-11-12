#!/usr/bin/env python3
"""Football Totals scan (PC port; @linemakersports reel Oct 24, 2026 —
Frank's $25k New Mexico vs North Dakota State UNDER).

FLAG RULE (Chet's display choice — the reel states no thresholds):
  UNDER when ALL hold:
    - BOTH defenses top-10 by opponent points-per-play (TeamRankings,
      season-to-date),
    - the total moved DOWN >= 1.5 from its OPEN (ScoresAndOdds OPEN
      column; on this PC port the first-seen total is stored as the
      open baseline when no OPEN column parses),
    - money majority on Over (> 50%) OR money% Over exceeds bets%
      Over by 5+ (splits leg ONLY when a public splits source
      validates — otherwise the splits leg is skipped and noted).
  OVER is the mirror (both defenses bottom-10, total up >= 1.5,
  money majority on Under / divergence the other way).

State shape matches the live file: {"days": {date: {"flags": [...],
"games_cfb": [...], "games_nfl": [...]}}, "meta": {...}}.
Banked: data/totals-<date>.md. Alert-only: NEW flags print. $100-flat
grading happens on Chet's side.
"""
import argparse
import sys

import requests

from config import DATA_DIR
from odds_common import bank, now_stamp, today_str
from scrape import parse_tables, rows_to_dicts
from so_board import BoardUnreadable, load_board, medians, parse_splits
from state import load_state, save_state

SCAN = "totals"
UA = {"User-Agent": "Mozilla/5.0"}
PPP_URLS = {
    "NFL": "https://www.teamrankings.com/nfl/stat/opponent-points-per-play",
    "NCAAF": "https://www.teamrankings.com/college-football/stat/opponent-points-per-play",
}
MOVE_MIN = 1.5


def fetch_ppp(league, dump_path=None):
    """TeamRankings opponent points-per-play -> {team: (ppp, rank)}.

    Rank: competition ranking, rank 1 = FEWEST points allowed per play
    (best defense). Best-effort parse; raises on zero rows so the
    caller fails loud instead of flagging on invented ranks.
    """
    resp = requests.get(PPP_URLS[league], headers=UA, timeout=30)
    resp.raise_for_status()
    if dump_path:
        dump_path.write_text(resp.text, encoding="utf-8")
    tables, anon = parse_tables(resp.text)
    best = None
    for rows in list(tables.values()) + anon:
        dicts = rows_to_dicts(rows)
        if dicts and any("Team" in d for d in dicts):
            best = dicts
            break
    if not best:
        raise RuntimeError(f"{league} TeamRankings PPP: no table parsed")
    vals = {}
    for d in best:
        team = (d.get("Team") or "").strip()
        ppp = None
        for k, v in d.items():
            if k.strip().lower() in ("2026", "opp ppp", "points/play", ""):
                try:
                    ppp = float(str(v).strip())
                except ValueError:
                    pass
        if team and ppp is not None:
            vals[team] = ppp
    if not vals:
        raise RuntimeError(f"{league} TeamRankings PPP: zero teams parsed")
    ranks = {t: 1 + sum(1 for o in vals.values() if o < v)
             for t, v in vals.items()}
    return {t: (vals[t], ranks[t]) for t in vals}


def match_ppp(ppp_map, team_name):
    """Fuzzy team match: alias, then exact, then substring either way."""
    alias = TEAM_ALIASES.get(team_name.strip().lower())
    if alias and alias in ppp_map:
        return ppp_map[alias]
    if team_name in ppp_map:
        return ppp_map[team_name]
    low = team_name.lower()
    for name, val in ppp_map.items():
        if name.lower() in low or low in name.lower():
            return val
    return None


# Board names (ScoresAndOdds nicknames / short forms) -> TeamRankings names.
# Without these the NFL leg is blind: TeamRankings lists cities ("Washington")
# while the board lists nicknames ("Commanders"), and the substring fallback
# only matches Giants/Jets/Rams/Chargers. CFB short forms likewise.
TEAM_ALIASES = {
    # NFL nickname -> TeamRankings city name
    "49ers": "San Francisco", "bengals": "Cincinnati", "bills": "Buffalo",
    "browns": "Cleveland", "cardinals": "Arizona", "chiefs": "Kansas City",
    "colts": "Indianapolis", "commanders": "Washington", "cowboys": "Dallas",
    "dolphins": "Miami", "falcons": "Atlanta", "jaguars": "Jacksonville",
    "lions": "Detroit", "packers": "Green Bay", "panthers": "Carolina",
    "patriots": "New England", "raiders": "Las Vegas", "ravens": "Baltimore",
    "saints": "New Orleans", "seahawks": "Seattle", "steelers": "Pittsburgh",
    "texans": "Houston", "titans": "Tennessee", "vikings": "Minnesota",
    "bears": "Chicago", "broncos": "Denver", "buccaneers": "Tampa Bay",
    "eagles": "Philadelphia", "giants": "NY Giants", "jets": "NY Jets",
    "rams": "LA Rams", "chargers": "LA Chargers",
    # CFB short form -> TeamRankings name
    "appalachian state": "App State", "east carolina": "E Carolina",
    "james madison": "J Madison", "ole miss": "Mississippi",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()
    state = load_state(SCAN)
    days = state.setdefault("days", {})
    day = days.setdefault(today_str(), {"flags": [], "games_nfl": [],
                                        "games_cfb": []})
    opens = state["meta"].setdefault("opens", {})
    new_flags = []
    splits_ok = False
    for league in ("NFL", "NCAAF"):
        try:
            ppp = fetch_ppp(league, (DATA_DIR / f"dump-ppp-{league}.html")
                            if args.dump else None)
        except Exception as e:  # noqa: BLE001 - fail loud per league
            bank(SCAN, [f"## {now_stamp()} — {league} PPP unreadable: {e}"])
            continue
        try:
            games, html_text = load_board(league)
        except BoardUnreadable as e:
            bank(SCAN, [f"## {now_stamp()} — {league} board unreadable: {e}"])
            continue
        if parse_splits(html_text):
            splits_ok = True
        n_teams = len(ppp)
        for game in games:
            _, med_total = medians(game)
            if med_total is None:
                continue
            key = f"{game['away']} @ {game['home']} | {today_str()}"
            open_total = game.get("open", {}).get("total")
            if open_total is None:
                open_total = opens.setdefault(key, med_total)
            move = med_total - open_total
            a = match_ppp(ppp, game["away"])
            h = match_ppp(ppp, game["home"])
            rec = {"away": game["away"], "home": game["home"],
                   "open_total": open_total, "current_total": med_total,
                   "away_ppp": a[0] if a else None,
                   "away_rank": a[1] if a else None,
                   "home_ppp": h[0] if h else None,
                   "home_rank": h[1] if h else None, "league": league}
            day["games_nfl" if league == "NFL" else "games_cfb"].append(rec)
            if not a or not h:
                continue
            sp = game.get("splits") or {}
            money_over = sp.get("money_total_over_pct")
            bets_over = sp.get("bets_total_over_pct")
            splits_leg_under = True
            if splits_ok and money_over is not None:
                splits_leg_under = (money_over > 50 or
                                    (bets_over is not None and
                                     money_over - bets_over >= 5))
            splits_leg_over = True
            if splits_ok and money_over is not None:
                splits_leg_over = (money_over < 50 or
                                   (bets_over is not None and
                                    bets_over - money_over >= 5))
            direction = None
            if a[1] <= 10 and h[1] <= 10 and move <= -MOVE_MIN \
                    and splits_leg_under:
                direction = "under"
            elif a[1] >= n_teams - 9 and h[1] >= n_teams - 9 \
                    and move >= MOVE_MIN and splits_leg_over:
                direction = "over"
            if direction and not any(
                    f["away"] == game["away"] and f["home"] == game["home"]
                    for f in day["flags"]):
                flag = {**rec, "direction": direction, "date": today_str(),
                        "money_over_pct": money_over,
                        "splits_leg": ("applied" if splits_ok else
                                       "skipped — no public splits source"),
                        "first_flagged": now_stamp()}
                day["flags"].append(flag)
                new_flags.append(flag)
    save_state(SCAN, state)
    splits_note = ("validated" if splits_ok
                   else "NOT validated (splits leg skipped)")
    lines = [f"## {now_stamp()} — totals scan: {len(new_flags)} NEW "
             f"flags; splits source {splits_note}"]
    for f in new_flags:
        lines.append(
            f"FLAG {f['direction'].upper()}: {f['away']} @ {f['home']} "
            f"({f['league']}) — def ranks {f['away_rank']}/{f['home_rank']} "
            f"by opp PPP; total {f['open_total']} -> {f['current_total']}")
    bank(SCAN, lines)
    for f in new_flags:
        print(f"TOTALS FLAG {f['direction'].upper()}: {f['away']} @ "
              f"{f['home']} — total {f['open_total']} -> {f['current_total']} "
              f"(def PPP ranks {f['away_rank']}, {f['home_rank']})")
    if not new_flags:
        print("totals: no new flags (silent).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
