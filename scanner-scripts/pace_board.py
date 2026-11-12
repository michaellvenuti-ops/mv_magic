#!/usr/bin/env python3
"""Pace & defense board data (PC, 2026-11-11) — built at Michael's
ask: a reference list for live totals betting — who plays fast,
who scores a lot, who defends — so in-play over/under reads start
from each team's scoring environment instead of a guess.

Source: TeamRankings public stat pages (pace, points per game,
opponent points per game) for the NBA, NCAA men's basketball,
NFL, and NCAA football. Basketball pace is possessions per game;
football pace is plays per game (TeamRankings publishes no
possessions stat for football). TeamRankings names are used as
published.
Avg game total = points/g + opponent points/g (every game a team
plays totals its own points plus its opponent's, so the sum of
the two averages is the average total of that team's games).

Runs on the 30-minute scheduler interval but SELF-GATES to one
build per ET day (pace data moves slowly). Writes only
data/pace-board.json — deliberately NO banked chat lines: this is
a reference page, not an alert scan. If a league's three pages do
not all parse, that league keeps its previous data (or is left
out on the first run) — never a partial or guessed table.
"""
import html as htmlmod
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from config import DATA_DIR  # noqa: E402
from state import load_state, save_state  # noqa: E402

ET = ZoneInfo("America/New_York")
SCAN = "pace"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"}
LEAGUES = {
    # name: (teamrankings path, min teams, pace stat)
    # Basketball pace = possessions per game; football has no
    # possessions stat on TeamRankings, so pace = plays per game.
    "NBA": ("nba", 20, "possessions-per-game"),
    "NCAAB": ("ncaa-basketball", 200, "possessions-per-game"),
    "NFL": ("nfl", 20, "plays-per-game"),
    "NCAAF": ("college-football", 100, "plays-per-game"),
}
STATS = ("points-per-game", "opponent-points-per-game")


def fetch_stat(league_path, stat):
    url = f"https://www.teamrankings.com/{league_path}/stat/{stat}"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")
    out = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 3:
            continue
        try:
            out[htmlmod.unescape(cells[1])] = float(cells[2])
        except ValueError:
            continue
    return out


def build_league(league_path, min_teams, pace_stat):
    tables = {}
    for stat in (pace_stat,) + STATS:
        t = fetch_stat(league_path, stat)
        if len(t) < min_teams:
            return None
        tables[stat] = t
    teams = []
    for team, pace in tables[pace_stat].items():
        pts = tables["points-per-game"].get(team)
        opp = tables["opponent-points-per-game"].get(team)
        if pts is None or opp is None:
            continue
        teams.append({"team": team, "pace": pace, "pts": pts,
                      "opp_pts": opp,
                      "avg_total": round(pts + opp, 1)})
    if len(teams) < min_teams:
        return None
    teams.sort(key=lambda r: -r["pace"])
    return teams


def main():
    now = datetime.now(ET)
    today = now.strftime("%Y-%m-%d")
    state = load_state(SCAN)
    force = "--force" in sys.argv
    if state["meta"].get("last_built_date") == today and not force:
        print(f"pace: already built for {today}; skipping.")
        return 0
    out_path = Path(DATA_DIR) / "pace-board.json"
    existing = {}
    if out_path.exists():
        try:
            existing = json.loads(
                out_path.read_text(encoding="utf-8")).get("leagues", {})
        except Exception:  # noqa: BLE001
            existing = {}
    leagues = {}
    notes = []
    for name, (path, min_teams, pace_stat) in LEAGUES.items():
        try:
            built = build_league(path, min_teams, pace_stat)
        except Exception as exc:  # noqa: BLE001
            built = None
            notes.append(f"{name}: fetch failed ({exc})")
        if built:
            leagues[name] = built
            notes.append(f"{name}: {len(built)} teams")
        elif name in existing:
            leagues[name] = existing[name]
            notes.append(f"{name}: kept previous data")
        else:
            notes.append(f"{name}: no data")
    if leagues:
        out_path.write_text(json.dumps({
            "updated_et": now.strftime("%Y-%m-%d %H:%M"),
            "source": "TeamRankings (pace, points, "
                      "opponent points per game)",
            "leagues": leagues,
        }, indent=1), encoding="utf-8")
    state["meta"]["last_built_date"] = today
    state["meta"]["last_run"] = now.strftime("%Y-%m-%dT%H:%M:%S")
    save_state(SCAN, state)
    print("pace: " + "; ".join(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
