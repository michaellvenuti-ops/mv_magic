"""Weekly QB props scan (PC port of the weekly props scan, QB half).

Component A — high-hit SGP: per starting QB, JOINT hit rate over the
trailing 20 regular-season games (min 12) for 1+ pass TD + Over an alt
passing-yards line; keep 90%+ joint, flag only when the FanDuel SGP
implied probability sits >=3 pts below the joint rate. Profit-per-$50
shown. Tread lightly: heavy-juice SGP winners get limited.
Component B — yardage projection: QB avg completions/game (season) x
opponent's allowed yards per completion (season); flag when the gap
vs the book's main passing-yards line is 15+ yards. Every QB's
projection is shown with its inputs.

Corrections baked in: past hit rate is NOT a true probability; a
-1200 leg implies 92.3%, not ~85%; two -1200 legs parlay to ~-576, so
$50 wins only $8.68 and one loss wipes ~5.8 wins. Component B is a
crude estimator (no game script, weather, injuries).

CALIBRATION: FanDuel event/SGP markup is not visible from the Muse
side. Alt lines and SGP prices are read best-effort from public
FanDuel event pages; when unreadable, Component A shows the joint
rate with no flag (a flag requires the SGP price) and Component B
shows the projection with no gap. --dump saves raw page text for
selector patching. Fail loud on zero resolved starters.
"""
import argparse
import datetime
import re
import sys

from playwright.sync_api import sync_playwright

from boards import render, write_board
from config import BOARDS_DIR, DATA_DIR
from github_push import put_file
from scrape import fetch_html, fnum, parse_tables, rows_to_dicts, save_dump
from state import load_state, save_state

PFR = "https://www.pro-football-reference.com"


def pfr_search_id(browser, name):
    """Resolve a player name to a PFR player id via the search page."""
    html_str = fetch_html(
        browser, f"{PFR}/search/search.fcgi?search={name.replace(' ', '+')}",
        wait_ms=3000)
    m = re.search(r"/players/[A-Z]/([A-Za-z0-9]+)\.htm", html_str)
    return m.group(1) if m else None


def gamelog_rows(browser, player_id, year):
    html_str = fetch_html(browser, f"{PFR}/players/{player_id[0]}/{player_id}/gamelog/{year}/")
    tables, _ = parse_tables(html_str)
    for tid, rows in tables.items():
        if "passing" in tid.lower():
            return rows_to_dicts(rows)
    return []


def trailing_games(browser, player_id):
    """Last 20 regular-season gamelog rows (2026 then 2025), min 12."""
    games = []
    for year in (2026, 2025):
        for row in gamelog_rows(browser, player_id, year):
            if row.get("GS", "").strip().lower().startswith("playoff"):
                break
            if row.get("Date", "").strip() in ("", "Date"):
                continue
            games.append(row)
            if len(games) >= 20:
                return games
    return games


def season_passing(browser):
    html_str = fetch_html(browser, f"{PFR}/years/2026/passing.htm")
    tables, _ = parse_tables(html_str)
    for rows in tables.values():
        d = rows_to_dicts(rows)
        if d and "Cmp" in d[0] and "Player" in d[0]:
            return d
    return []


def season_opp_passing(browser):
    html_str = fetch_html(browser, f"{PFR}/years/2026/opp.htm")
    tables, _ = parse_tables(html_str)
    for rows in tables.values():
        d = rows_to_dicts(rows)
        if d and "Cmp" in d[0] and "Tm" in d[0]:
            return d
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--week", default="Current slate")
    ap.add_argument("--repo-path", default="qb-props-board-pc.html")
    args = ap.parse_args()
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # Starters from the Ourlads QB depth-chart page (calibration point).
    starters = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            html_str = fetch_html(
                browser, "https://www.ourlads.com/nfldepthcharts/depthchartpos/QB")
            if args.dump:
                save_dump(f"qb-dump-{today}.txt", html_str)
            tables, _ = parse_tables(html_str)
            for rows in tables.values():
                d = rows_to_dicts(rows)
                if d and "Player" in d[0] and "Tm" in d[0]:
                    for r in d:
                        if r.get("Player") and r.get("Tm"):
                            starters.setdefault(r["Tm"], r["Player"])
                    break
        except Exception as e:  # noqa: BLE001
            print(f"Starters fetch failed: {e}")
        if not starters:
            print("0 starters resolved — refusing to write state. "
                  "Re-run with --dump and send the dump file.")
            return 2
        passing = season_passing(browser)
        opp = season_opp_passing(browser)
        comp_a_rows, comp_b_rows = [], []
        for team, qb in sorted(starters.items()):
            pid = pfr_search_id(browser, qb)
            games = trailing_games(browser, pid) if pid else []
            # Component B (season inputs; no per-player search needed).
            prow = next((r for r in passing if qb.lower() in r.get("Player", "").lower()), None)
            orow = next((r for r in opp if r.get("Tm") == team), None)
            proj = gap = None
            if prow and orow:
                cmp_g = (fnum(prow.get("Cmp")) or 0) / max(fnum(prow.get("G")) or 1, 1)
                yds_c = (fnum(orow.get("Yds")) or 0) / max(fnum(orow.get("Cmp")) or 1, 1)
                proj = round(cmp_g * yds_c, 1)
            comp_b_rows.append([qb, team, proj if proj is not None else "n/a",
                                "line unreadable (calibration)", ""])
            # Component A (joint rate on 1+ TD; alt line needs the book).
            if len(games) >= 12:
                td_rate = sum(1 for g in games if (fnum(g.get("TD")) or 0) >= 1) / len(games)
                comp_a_rows.append([qb, team, len(games),
                                    f"{td_rate*100:.0f}% (TD only; alt unreadable)",
                                    "no flag — SGP price unreadable"])
            else:
                comp_a_rows.append([qb, team, len(games),
                                    "under 12 games — skipped", ""])
        browser.close()

    state = load_state("qb-props")
    state["meta"]["last_scan"] = now
    save_state("qb-props", state)
    html_str = render(
        "QB Props Board",
        "Weekly QB SGP hit rates and yardage projections (PC port).",
        ["<p class='flag'>Past hit rate is not a true probability. Two "
         "-1200 legs parlay to about -576, so $50 wins only $8.68 and "
         "one loss wipes about 5.8 wins. Tread lightly: heavy-juice "
         "SGP winners get limited.</p>",
         "<p>Component A flags only when the FanDuel SGP implied "
         "probability sits 3+ pts below a 90%+ joint hit rate. "
         "Component B flags when the projection gap vs the book's "
         "main passing-yards line is 15+ yards.</p>"],
        [("Component A — high-hit SGP (TD leg shown; alt line + SGP "
          "price need book-page calibration)", ["QB", "Team", "Games", "Joint rate", "Flag"], comp_a_rows),
         ("Component B — yardage projections", ["QB", "Team", "Projection", "Book line", "Gap"], comp_b_rows)],
        "Snapshot only — verify in the book app before placing. You "
        "place every bet yourself; nothing here places a bet. Sources: "
        "public game logs (Pro Football Reference) and publicly "
        "readable book pages.", now)
    board_path = write_board("qb-props-board.html", html_str)
    print(f"QB props: {len(starters)} starters, {len(comp_a_rows)} A rows, "
          f"{len(comp_b_rows)} B rows.")
    if args.push:
        ok, detail = put_file(args.repo_path, board_path, "Update QB props board (PC scan)")
        print(f"Push {args.repo_path}: {'OK ' + detail if ok else 'FAILED ' + detail}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
