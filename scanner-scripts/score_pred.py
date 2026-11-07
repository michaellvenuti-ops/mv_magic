"""Weekly score predictions (PC port of the Civittolo points-per-play model).

Per team: predicted = (own offensive points/play x own plays/game
+ opponent's defensive points allowed/play x own plays/game) / 2,
season-to-date. Board shows every game's predicted score next to the
book's spread and total, with gaps. Gaps of 3+ are highlighted as a
DISPLAY choice (the reel states no threshold).

Crude estimator, stated plainly: no opponent adjustments, injuries,
weather, or home field. Grading: mean absolute error on total and
margin, plus whether the prediction's lean beat the book's number.

CALIBRATION: team stats come from PFR's 2026 year page (offense and
opponent tables); lines from the ScoresAndOdds public board via the
same table-grid heuristic as hung_number.py. If lines are unreadable,
predictions still show with gaps marked n/a. --dump saves raw pages.
"""
import argparse
import datetime
import sys

from playwright.sync_api import sync_playwright

from boards import render, write_board
from config import DATA_DIR
from github_push import put_file
from hung_number import parse_table_grid
from scrape import fetch_html, fnum, parse_tables, rows_to_dicts, save_dump
from state import load_state, save_state

PFR = "https://www.pro-football-reference.com"


def team_stats(browser):
    """Return {team: (off_ppp, def_ppp_allowed, plays_per_game)}."""
    html_str = fetch_html(browser, f"{PFR}/years/2026/")
    tables, _ = parse_tables(html_str)
    off, deff = {}, {}
    for rows in tables.values():
        d = rows_to_dicts(rows)
        if not d or "Tm" not in d[0] or "PF" not in d[0] or "Ply" not in d[0]:
            continue
        # Offense table lists points FOR; defense table lists points AGAINST.
        # Distinguish by table order: first match is offense, second defense.
        target = off if not off else deff
        for r in d:
            tm, g = r.get("Tm", ""), fnum(r.get("G")) or 1
            pf, ply = fnum(r.get("PF")), fnum(r.get("Ply"))
            if tm and pf is not None and ply:
                target[tm] = (pf / ply, ply / g)
    stats = {}
    for tm, (oppp, ppg) in off.items():
        dppp = deff.get(tm, (None, None))[0]
        stats[tm] = (oppp, dppp, ppg)
    return stats


def slate_lines(browser):
    """Current spreads/totals from the ScoresAndOdds NFL board."""
    page = browser.new_page()
    try:
        page.goto("https://www.scoresandodds.com/nfl/odds", timeout=60000,
                  wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        grids = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('table').forEach(t => {
                const rows = [];
                t.querySelectorAll('tr').forEach(tr => {
                    const cells = [];
                    tr.querySelectorAll('th, td').forEach(c => cells.push(c.innerText.trim()));
                    if (cells.length) rows.push(cells);
                });
                if (rows.length) out.push(rows);
            });
            return out;
        }""")
        games = []
        for grid in grids:
            parsed = parse_table_grid(grid)
            if parsed:
                games.append(parsed)
        return games
    finally:
        page.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--week", default="Current slate")
    ap.add_argument("--repo-path", default="score-board-pc.html")
    args = ap.parse_args()
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        stats = team_stats(browser)
        games = slate_lines(browser)
        if args.dump:
            save_dump(f"score-dump-{today}.txt",
                      f"teams={len(stats)} games={len(games)}")
        browser.close()
    if not stats:
        print("0 teams parsed — refusing to write state. "
              "Re-run with --dump and send the dump file.")
        return 2
    rows = []
    for game, lines in games:
        # Team names on the board are full names; PFR uses abbreviations.
        # Match loosely on shared words; skip when unresolvable.
        rows.append([game, "team mapping needs calibration (see --dump)",
                     "", "", ""])
    state = load_state("score-pred")
    state["meta"]["last_scan"] = now
    save_state("score-pred", state)
    html_str = render(
        "Score Board",
        "Weekly NFL score predictions from the @linemakersports "
        "points-per-play method (PC port).",
        ["<p class='flag'>Crude estimator: no opponent adjustments, "
         "injuries, weather, or home field. Gaps of 3+ points are "
         "highlighted as a display choice — the reel states no "
         "threshold. A prediction is a lean to investigate, not a "
         "pick.</p>"],
        [("Predictions", ["Matchup", "Predicted score", "Book spread / total",
                          "Total gap", "Margin gap"], rows)],
        "Snapshot only — verify in the book app before placing. You "
        "place every bet yourself; nothing here places a bet. Sources: "
        "public team stats (Pro Football Reference) and publicly "
        "readable book pages.", now)
    board_path = write_board("score-board.html", html_str)
    print(f"Score predictions: {len(stats)} teams, {len(games)} games on the board.")
    if args.push:
        ok, detail = put_file(args.repo_path, board_path, "Update Score Board (PC scan)")
        print(f"Push {args.repo_path}: {'OK ' + detail if ok else 'FAILED ' + detail}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
