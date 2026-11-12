#!/usr/bin/env python3
"""NHL Totals scan (PC port; @linemakersports reel Oct 27, 2026).

Predicted total = [Team A SOG/game x (1 - Team B save %)]
                + [Team B SOG/game x (1 - Team A save %)],
season-to-date. Team save % is derived from team shots-against and
goals-against per game (save% = 1 - GA/SA) from the NHL's public
club-stats API (api-web.nhle.com) — actual team figures, not invented.

Threshold = Chet's DISPLAY CHOICE (the reel states none): flag OVER
when predicted >= book total + 1.0, UNDER when predicted <= book
total - 1.0. Book totals: ESPN scoreboard (DraftKings). No ESPN total
= game unavailable, never flagged (no fallback source).

FRANK-ADJUSTED 85% LINE (added Nov 12, 2026 at Michael's ask):
Frank buys the total up/down to the number where the model gives the
side about an 85% chance. Total goals are modeled Poisson with
lambda = predicted total (the standard first-order model for hockey
goals; labeled "modeled", never a guarantee). UNDER flag: smallest
integer k with P(total <= k) >= 0.85, adjusted line = k + 0.5.
OVER flag: largest integer k with P(total >= k) >= 0.85, adjusted
line = k - 0.5. Example: predicted 4.95 -> Under 7.5 (P<=7 = 87%).
Grading is UNCHANGED: graded at the book total as flagged, never at
the adjusted line — the adjusted line is Frank's buy-up guide only.

BOTH sides' prices are recorded for every flagged game so grading can
score the inverse side too (see nhl_totals_grade.py).

BACKUP-STARTER CAVEAT rides every flag list: a backup goalie starting
can move the real expectation a full goal; the formula uses team
season save %.

Writes data/nhl-totals-snapshot-<date>.json, updates
data/nhl-totals-state.json ({"days": {...}}), banks
data/nhl-totals-<date>.md. Public sources only; no sign-in.
"""
import argparse
import json
import math
import sys

import requests

from config import DATA_DIR
from odds_common import bank, now_stamp, today_str
from state import load_state, save_state

SCAN = "nhl-totals"
UA = {"User-Agent": "Mozilla/5.0"}
NHL_TEAMS = ["ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL",
             "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NJD",
             "NSH", "NYI", "NYR", "OTT", "PHI", "PIT", "SEA", "SJS",
             "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WPG", "WSH"]
ESPN_SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/"
                   "hockey/nhl/scoreboard")
CAVEAT = ("BACKUP-STARTER CAVEAT: a backup goalie starting can move "
          "the real expectation a full goal; predictions use team "
          "season save %.")
DISPLAY = ("Threshold note: the 1.0-goal flag gap is Chet's display "
           "choice, not a claim from the reel.")


def pois_cdf(lam, k):
    """P(X <= k) for X ~ Poisson(lam), recurrence form (no scipy)."""
    p = math.exp(-lam)
    cdf = p
    for i in range(1, k + 1):
        p *= lam / i
        cdf += p
    return cdf


def adj85(pred, flag):
    """Frank-adjusted 85% line for a flag.

    Returns (line, prob): line is the bought total (e.g. 7.5 for
    "Under 7.5"), prob is the model's P of that side hitting at that
    line. UNDER: smallest k with P(X<=k) >= 0.85, line k+0.5.
    OVER: largest k with P(X>=k) >= 0.85, line k-0.5.
    """
    if flag == "under":
        for k in range(0, 30):
            prob = pois_cdf(pred, k)
            if prob >= 0.85:
                return k + 0.5, round(prob, 3)
        return 30.5, round(pois_cdf(pred, 30), 3)
    for k in range(30, -1, -1):
        prob = 1.0 - pois_cdf(pred, k - 1) if k > 0 else 1.0
        if prob >= 0.85:
            return k - 0.5, round(prob, 3)
    return -0.5, 1.0


def fetch_team_stats(abbr):
    """{sog_per_game, save_pct} from the NHL club-stats API."""
    url = f"https://api-web.nhle.com/v1/club-stats/{abbr}/now"
    data = requests.get(url, headers=UA, timeout=20).json()
    skaters = data.get("skaters") or []
    goalies = data.get("goalies") or []
    # Team aggregates are repeated per player row via team totals in
    # some payloads; fall back to games-weighted player sums.
    shots_for = sum(s.get("shots", 0) for s in skaters)
    games = max((s.get("gamesPlayed", 0) for s in skaters), default=0)
    saves = sum(g.get("saves", 0) for g in goalies)
    shots_against = sum(g.get("shotsAgainst", 0) for g in goalies)
    if not games or not shots_against:
        return None
    return {"sog_per_game": shots_for / games,
            "save_pct": saves / shots_against,
            "games": games}


def fetch_games():
    data = requests.get(ESPN_SCOREBOARD, headers=UA, timeout=20).json()
    games = []
    for event in data.get("events", []):
        comp = event["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        odds = (comp.get("odds") or [{}])[0]
        games.append({
            "away": teams["away"]["team"]["abbreviation"],
            "home": teams["home"]["team"]["abbreviation"],
            "total": odds.get("overUnder"),
            "over_juice": odds.get("overOdds"),
            "under_juice": odds.get("underOdds"),
            "start": event.get("date", "")})
    return games


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()
    stats = {}
    for abbr in NHL_TEAMS:
        try:
            s = fetch_team_stats(abbr)
        except (requests.RequestException, ValueError):
            s = None
        if s:
            stats[abbr] = s
    if len(stats) < 20:
        print(f"nhl-totals: only {len(stats)} team stat lines parsed — "
              f"failing loud, nothing written.", file=sys.stderr)
        return 2
    try:
        games = fetch_games()
    except (requests.RequestException, ValueError) as e:
        print(f"nhl-totals: ESPN scoreboard unreadable ({e})",
              file=sys.stderr)
        return 2
    state = load_state(SCAN)
    days = state.setdefault("days", {})
    snapshot_games, flags = [], []
    for g in games:
        a, h = stats.get(g["away"]), stats.get(g["home"])
        rec = {**g, "predicted": None, "flag": None}
        if a and h and g["total"] is not None:
            pred = (a["sog_per_game"] * (1 - h["save_pct"])
                    + h["sog_per_game"] * (1 - a["save_pct"]))
            rec["predicted"] = round(pred, 2)
            rec["away_pred"] = round(a["sog_per_game"]
                                     * (1 - h["save_pct"]), 2)
            rec["home_pred"] = round(h["sog_per_game"]
                                     * (1 - a["save_pct"]), 2)
            if pred >= g["total"] + 1.0:
                rec["flag"] = "over"
            elif pred <= g["total"] - 1.0:
                rec["flag"] = "under"
            if rec["flag"]:
                line85, prob85 = adj85(pred, rec["flag"])
                rec["adj85_line"] = line85
                rec["adj85_prob"] = prob85
        snapshot_games.append(rec)
        if rec["flag"]:
            flags.append(rec)
    snap = {"date": today_str(), "source": "NHL club-stats API + ESPN "
            "DraftKings totals", "games": snapshot_games,
            "flags": flags}
    snap_path = DATA_DIR / f"nhl-totals-snapshot-{today_str()}.json"
    with open(snap_path, "w") as fh:
        json.dump(snap, fh, indent=1)
    days[today_str()] = snap
    save_state(SCAN, state)
    lines = [f"## {now_stamp()} — {len(games)} games, {len(flags)} flags",
             CAVEAT, DISPLAY]
    for f in flags:
        lines.append(
            f"FLAG {f['flag'].upper()}: {f['away']} @ {f['home']} — "
            f"predicted {f['predicted']} ({f['away_pred']} + "
            f"{f['home_pred']}) vs book {f['total']} "
            f"(over {f['over_juice']}, under {f['under_juice']}) — "
            f"Frank-adjusted {f['flag'].upper()} {f['adj85_line']} "
            f"(~{f['adj85_prob'] * 100:.0f}% modeled, Poisson on "
            f"predicted total)")
    bank(SCAN, lines)
    if flags:
        print(CAVEAT)
        print(DISPLAY)
    for f in flags:
        juice = f["over_juice"] if f["flag"] == "over" else f["under_juice"]
        print(f"NHL TOTALS FLAG {f['flag'].upper()}: {f['away']} @ "
              f"{f['home']} — predicted {f['predicted']} vs book "
              f"{f['total']} ({juice}) — Frank-adjusted "
              f"{f['flag'].upper()} {f['adj85_line']} "
              f"(~{f['adj85_prob'] * 100:.0f}% modeled)")
    if not flags:
        print(f"nhl-totals: {len(games)} games, no flags.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
