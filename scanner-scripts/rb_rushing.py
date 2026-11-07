#!/usr/bin/env python3
"""Weekly RB rushing projections scan (PC port; added live Oct 31,
2026 at Michael's tap — Frank @linemakersports reel DeLCFXhgGbq.
New in the v4 package).

Formula (pure Frank): projection = RB rushing attempts/game
(season-to-date, per game PLAYED, Pro Football Reference) x the
opponent's rushing yards-per-attempt ALLOWED (season-to-date).

Michael/Chet display choices (Frank states none — labeled as such):
  - flag at |projection - book line| >= 10 yards, either direction,
  - qualifiers: 4+ games played AND 8+ attempts/game,
  - the RB's own yards-per-carry is deliberately NOT blended in.

Lines: FanDuel main rushing lines are not reliably machine-readable
from the PC side, so lines are read from data/rb-lines.json when
present ({"Player Name": 62.5, ...} — Chet can sync this file, or
fill it by hand). Without a line, the projection still banks with
its inputs and NO flag is claimed (a flag needs the book line).

Grading stays on Chet's side: the synced flags are graded from box
scores in the Thursday run's report, same as the live scan.

State: data/rb-rushing-state.json.
Banked: data/rb-rushing-<date>.md (every qualifier with inputs).
CALIBRATION: PFR table shapes and the ScoresAndOdds matchup parse
are best-effort; --dump saves raw pages for a selector patch.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import DATA_DIR
from odds_common import bank, now_stamp, today_str
from scrape import fetch_html, fnum, parse_tables, rows_to_dicts, \
    save_dump, uncomment
from state import load_state, save_state

SCAN = "rb-rushing"
FLAG_GAP = 10.0
MIN_GAMES = 4
MIN_ATT_G = 8.0
PFR = "https://www.pro-football-reference.com"

ABBR_NAME = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys", "DEN": "Denver Broncos",
    "DET": "Detroit Lions", "GNB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars", "KAN": "Kansas City Chiefs",
    "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams",
    "LVR": "Las Vegas Raiders", "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings", "NWE": "New England Patriots",
    "NOR": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers", "SEA": "Seattle Seahawks",
    "SFO": "San Francisco 49ers", "TAM": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}


def season_year():
    now = datetime.datetime.now()
    return now.year if now.month >= 3 else now.year - 1


def _tables(browser, url, dump_name, dump=False):
    html = fetch_html(browser, url)
    if dump:
        save_dump(dump_name, html)
    dicts = []
    for rows in parse_tables(uncomment(html)):
        dicts.extend(rows_to_dicts(rows))
    return dicts


def load_rushers(browser, year, dump=False):
    out = []
    for d in _tables(browser, f"{PFR}/years/{year}/rushing.htm",
                     "pfr-rushing", dump):
        name = (d.get("Player") or "").strip().strip("*+")
        att, g = fnum(d.get("Att")), fnum(d.get("G"))
        tm = (d.get("Tm") or "").strip()
        if name and att is not None and g:
            out.append({"player": name, "team": tm, "games": g,
                        "att": att, "att_g": att / g})
    return out


def load_opp_ypa(browser, year, dump=False):
    """Team -> rushing YPA allowed, from the opponent/defense page.
    The rushing Y/A column is picked by header match; calibration."""
    ypa = {}
    for d in _tables(browser, f"{PFR}/years/{year}/opp.htm",
                     "pfr-opp", dump):
        tm = (d.get("Tm") or d.get("Team") or "").strip()
        if not tm:
            continue
        for key, val in d.items():
            if key and "Y/A" in key:
                v = fnum(val)
                if v is not None and 2.0 < v < 7.0:
                    ypa[tm] = v
                    break
    return ypa


def opponent_map():
    """Team full name -> opponent full name, from the ScoresAndOdds
    NFL board (best-effort; empty when the board is unreadable)."""
    try:
        from so_board import load_board
        games = load_board("nfl")
    except Exception:
        return {}
    pairs = {}
    for g in games or []:
        if not isinstance(g, dict):
            continue
        away = g.get("away") or g.get("away_team") or ""
        home = g.get("home") or g.get("home_team") or ""
        if away and home:
            pairs[str(away)] = str(home)
            pairs[str(home)] = str(away)
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()
    year = season_year()
    state = load_state(SCAN)
    lines_path = Path(DATA_DIR) / "rb-lines.json"
    book_lines = {}
    if lines_path.exists():
        try:
            book_lines = json.loads(lines_path.read_text())
        except (json.JSONDecodeError, OSError):
            book_lines = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            rushers = load_rushers(browser, year, args.dump)
            opp_ypa = load_opp_ypa(browser, year, args.dump)
        finally:
            browser.close()
    if not rushers:
        print("rb-rushing: zero rushers parsed from PFR — run with "
              "--dump and send it back for a parser patch. No state "
              "written.", file=sys.stderr)
        return 2
    opps = opponent_map()
    lines = [f"## {now_stamp()} — rb-rushing projections "
             f"({year} season-to-date; flag |gap| >= {FLAG_GAP:g} "
             f"yards is a display choice, not Frank's)"]
    flags = []
    qualifiers = 0
    for r in sorted(rushers, key=lambda x: -x["att_g"]):
        if r["games"] < MIN_GAMES or r["att_g"] < MIN_ATT_G:
            continue
        team_name = ABBR_NAME.get(r["team"], r["team"])
        opp_name = opps.get(team_name, "")
        ypa = None
        for tm, v in opp_ypa.items():
            if tm and (tm in opp_name or opp_name and opp_name in tm
                       or tm == r["team"]):
                # opp page rows are keyed by the DEFENDING team
                if opp_name and (tm in opp_name or opp_name in tm):
                    ypa = v
        proj = (round(r["att_g"] * ypa, 1)
                if ypa is not None else None)
        qualifiers += 1
        line = book_lines.get(r["player"])
        gap = (round(proj - line, 1)
               if proj is not None and line is not None else None)
        row = (f"- {r['player']} ({team_name}"
               f"{' @ ' + opp_name if opp_name else ''}): "
               f"{r['att_g']:.2f} att/g x "
               f"{ypa if ypa is not None else '?'} opp YPA allowed "
               f"= proj {proj if proj is not None else '?'}"
               f"{f' vs line {line} (gap {gap:+})' if gap is not None else ' — no book line captured'}")
        lines.append(row)
        if gap is not None and abs(gap) >= FLAG_GAP:
            lean = "Over lean" if gap > 0 else "Under lean"
            flags.append((r["player"], opp_name, proj, line, gap,
                          lean))
            key = f"{r['player']} | {opp_name} | {today_str()}"
            state["entries"][key] = {
                "player": r["player"], "team": team_name,
                "opponent": opp_name, "att_g": round(r["att_g"], 2),
                "opp_ypa": ypa, "projection": proj, "line": line,
                "gap": gap, "lean": lean,
                "first_reported": now_stamp()}
    lines.append(f"qualifiers: {qualifiers}; flags: {len(flags)}")
    for p, o, proj, line, gap, lean in flags:
        lines.append(f"FLAG {lean}: {p} vs {o} — proj {proj} vs "
                     f"line {line} (gap {gap:+})")
    bank(SCAN, lines)
    save_state(SCAN, state)
    for ln in lines:
        print(ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
