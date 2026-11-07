"""Weekly first-half scan (PC port of the Civittolo alt-1H strategy).

For each game on the week's NFL slate, pull head-to-head meetings
over the last 4-5 seasons and count how often the FIRST-HALF total
went OVER 28.5 points. Qualifier (display rule, not the reel's):
over-28.5 rate <=25% across >=6 meetings. The play is UNDER 28.5
first-half points. <6 meetings shows its rate but never qualifies.

Corrections carried on the board: breakeven math (-500 needs 83.3%,
-400 needs 80.0%, -300 needs 75.0% — an 80% win rate loses at -500)
and the small-sample caveat. Grading is by money ($100 flat at the
recorded price), record-only when no price was recorded.

Boxscore fetching is cached under data/fh-cache/ by game id so
reruns are fast. CALIBRATION: PFR team-page markup and book 1H alt
lines are not visible from the Muse side; --dump saves raw pages,
and any game whose history cannot be read is skipped and noted,
never estimated.
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from boards import render, write_board
from config import DATA_DIR
from github_push import put_file
from hung_number import parse_table_grid
from scrape import fetch_html, fnum, parse_tables, rows_to_dicts, save_dump
from state import load_state, save_state

PFR = "https://www.pro-football-reference.com"
CACHE = DATA_DIR / "fh-cache"
CACHE.mkdir(exist_ok=True)

TEAM_ABBR = {
    "Arizona Cardinals": "crd", "Atlanta Falcons": "atl", "Baltimore Ravens": "rav",
    "Buffalo Bills": "buf", "Carolina Panthers": "car", "Chicago Bears": "chi",
    "Cincinnati Bengals": "cin", "Cleveland Browns": "cle", "Dallas Cowboys": "dal",
    "Denver Broncos": "den", "Detroit Lions": "det", "Green Bay Packers": "gnb",
    "Houston Texans": "htx", "Indianapolis Colts": "clt", "Jacksonville Jaguars": "jax",
    "Kansas City Chiefs": "kan", "Las Vegas Raiders": "rai", "Los Angeles Chargers": "sdg",
    "Los Angeles Rams": "ram", "Miami Dolphins": "mia", "Minnesota Vikings": "min",
    "New England Patriots": "nwe", "New Orleans Saints": "nor", "New York Giants": "nyg",
    "New York Jets": "nyj", "Philadelphia Eagles": "phi", "Pittsburgh Steelers": "pit",
    "San Francisco 49ers": "sfo", "Seattle Seahawks": "sea", "Tampa Bay Buccaneers": "tam",
    "Tennessee Titans": "oti", "Washington Commanders": "was",
}


def slate(browser):
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
                games.append(parsed[0])
        return games
    finally:
        page.close()


def meeting_boxscores(browser, team, opp, years=(2021, 2022, 2023, 2024, 2025)):
    """Boxscore URLs for team-vs-opp meetings in the given seasons."""
    abbr = TEAM_ABBR.get(team)
    if not abbr:
        return []
    urls = []
    for year in years:
        html_str = fetch_html(browser, f"{PFR}/teams/{abbr}/{year}.htm")
        tables, _ = parse_tables(html_str)
        # Re-parse with links: find the schedule table's boxscore hrefs.
        from scrape import TableParser, uncomment
        parser = TableParser()
        parser.feed(uncomment(html_str))
        for tid, rows in parser.tables.items():
            if "games" not in tid:
                continue
            d = rows_to_dicts(rows)
            for i, r in enumerate(d):
                opp_cell = r.get("Opp", "")
                if opp and opp.split()[-1].lower() in opp_cell.lower():
                    href = parser.links.get((tid, i + 1, 0))
                    if href and "/boxscores/" in href:
                        urls.append("https://www.pro-football-reference.com" + href)
    return urls


def first_half_total(browser, url):
    """1H total from a boxscore page (Q1+Q2 both teams), cached."""
    game_id = re.search(r"/boxscores/([^/]+)\.htm", url)
    cache_path = CACHE / f"{game_id.group(1)}.html" if game_id else None
    if cache_path and cache_path.exists():
        html_str = cache_path.read_text(encoding="utf-8")
    else:
        html_str = fetch_html(browser, url)
        if cache_path:
            cache_path.write_text(html_str, encoding="utf-8")
    tables, _ = parse_tables(html_str)
    for tid, rows in tables.items():
        if "quarter" in tid:
            d = rows_to_dicts(rows)
            total = 0
            for r in d:
                q1, q2 = fnum(r.get("1st")), fnum(r.get("2nd"))
                if q1 is not None and q2 is not None:
                    total += q1 + q2
            if total:
                return total
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--week", default="Current slate")
    ap.add_argument("--repo-path", default="first-half-board-pc.html")
    args = ap.parse_args()
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        games = slate(browser)
        if args.dump:
            save_dump(f"fh-dump-{today}.txt", f"games={games}")
        rows = []
        for game in games:
            parts = [s.strip() for s in game.split(" vs ")]
            if len(parts) != 2:
                rows.append([game, "unparsed matchup", "", "", ""])
                continue
            away, home = parts
            urls = meeting_boxscores(browser, away, home)
            overs = total = 0
            for url in urls:
                fh_total = first_half_total(browser, url)
                if fh_total is not None:
                    total += 1
                    if fh_total > 28.5:
                        overs += 1
            rate = round(overs / total * 100, 1) if total else None
            qual = rate is not None and rate <= 25 and total >= 6
            rows.append([game, f"{total} ({overs})",
                         f"{rate}%" if rate is not None else "n/a",
                         "price unreadable (calibration)",
                         "QUALIFIES" if qual else ""])
        browser.close()
    if not games:
        print("0 games on the slate — refusing to write state. "
              "Re-run with --dump and send the dump file.")
        return 2
    state = load_state("fh")
    state["meta"]["last_scan"] = now
    save_state("fh", state)
    html_str = render(
        "First Half Board",
        "Weekly NFL alternate first-half strategy (PC port).",
        ["<p class='flag'>Correction 1 — price beats win rate: at -500 "
         "the breakeven win rate is 83.3%, at -400 it is 80.0%, at -300 "
         "it is 75.0%. Correction 2 — small samples: matchups with "
         "under 6 meetings show their rate but never qualify.</p>"],
        [("Qualifiers + full field", ["Matchup", "H2H meetings (overs)",
                                      "H2H over-28.5 rate", "Under 28.5 price",
                                      "Qualifier"], rows)],
        "Snapshot only — verify in the book app before placing. You "
        "place every bet yourself; nothing here places a bet. Sources: "
        "public boxscores (Pro Football Reference) and publicly "
        "readable book pages.", now)
    board_path = write_board("first-half-board.html", html_str)
    n_qual = sum(1 for r in rows if r[-1] == "QUALIFIES")
    print(f"First half: {len(rows)} games, {n_qual} qualifiers.")
    if args.push:
        ok, detail = put_file(args.repo_path, board_path, "Update First Half Board (PC scan)")
        print(f"Push {args.repo_path}: {'OK ' + detail if ok else 'FAILED ' + detail}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
