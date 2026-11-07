"""Hung-number scan (PC port, package 1 template).

ScoresAndOdds public board, no sign-in. Per game, median spread/total
across books; flags a NJ book >=1.0 pt off median spread, >=1.5 off
median total, or >=5 pts of implied probability off median moneyline,
naming the value side (better number vs consensus) and the middle
pairing when the divergence spans 3 or 7.

CALIBRATION SURFACE (top of file): the ScoresAndOdds board markup is
not visible from the Muse side, so the table parser below is a
best-effort first pass. First run with --dump: it saves the raw board
text/tables to data/dump-<date>.txt. If zero games parse, the script
FAILS LOUD (exit 2, no state written) — send the dump and the parser
gets patched. The flagging/state/board/alert code below is complete;
only the DOM extraction is calibration-pending.

Alert-only: new flags print + append to data/alerts-<date>.log; an
existing flag re-alerts only if its divergence grows by 0.5+.
Bruno's caveat rides on every output: a lone hung number is the play;
a book off everywhere is opinionated pricing — leave it.
"""
import argparse
import datetime
import html as htmlmod
import re
import sys
from statistics import median

from playwright.sync_api import sync_playwright

from config import BOARDS_DIR, DATA_DIR, NJ_BOOKS, PAGE_TIMEOUT_MS
from github_push import put_file
from state import load_state, save_state

# --- calibration surface: tweak these after the first --dump run ---
BOARD_URLS = [
    ("NFL", "https://www.scoresandodds.com/nfl/odds"),
    ("NCAAF", "https://www.scoresandodds.com/ncaaf/odds"),
]
SPREAD_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)$")
ML_RE = re.compile(r"^([+-]\d+)$")
TOTAL_RE = re.compile(r"^(\d+(?:\.\d+)?)$")
SPREAD_MIN, TOTAL_MIN, ML_MIN = 1.0, 1.5, 5.0
RE_ALERT_GROWTH = 0.5


def implied_prob(american):
    if american > 0:
        return 100.0 / (american + 100.0) * 100.0
    return (-american) / (-american + 100.0) * 100.0


def parse_table_grid(cells_by_row):
    """Best-effort: one ScoresAndOdds game table -> (game, {book: line}).

    Expected shape: a header row naming books, then team rows whose
    cells carry spread/total/moneyline tokens. Returns None when the
    table does not look like a game table (no NJ book in the header).
    """
    if len(cells_by_row) < 2:
        return None
    header = [c.strip() for c in cells_by_row[0]]
    book_cols = {}
    for idx, cell in enumerate(header):
        for book in NJ_BOOKS:
            if book.lower() in cell.lower():
                book_cols[idx] = book
    if not book_cols:
        return None
    teams = []
    lines = {}
    for row in cells_by_row[1:]:
        cells = [c.strip() for c in row]
        if not cells:
            continue
        team = cells[0]
        if not team or team.lower() in ("team",):
            continue
        teams.append(team)
        for idx, book in book_cols.items():
            if idx < len(cells):
                lines.setdefault(book, {})[team] = cells[idx]
    if len(teams) < 2:
        return None
    game = f"{teams[0]} vs {teams[1]}"
    return game, lines


def extract_games(page, dump_path=None):
    """Return list of (game, {book: {team: cell}}) plus raw dump text."""
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
    raw_text = page.locator("body").inner_text(timeout=15000)
    if dump_path:
        with open(dump_path, "w", encoding="utf-8") as fh:
            fh.write(raw_text)
            fh.write("\n\n===== TABLES =====\n")
            for grid in grids:
                for row in grid:
                    fh.write(" | ".join(row) + "\n")
                fh.write("---\n")
    games = []
    for grid in grids:
        parsed = parse_table_grid(grid)
        if parsed:
            games.append(parsed)
    return games, raw_text


def split_cell(cell):
    """A book cell may carry spread/total/ML together; pull tokens out."""
    spread = total = ml = None
    for tok in cell.replace("/", " ").split():
        if ml is None and ML_RE.match(tok):
            ml = int(tok)
        elif spread is None and SPREAD_RE.match(tok):
            spread = float(tok)
        elif total is None and TOTAL_RE.match(tok):
            total = float(tok)
    return spread, total, ml


def flag_game(league, game, lines):
    """lines: {book: {team: cell}}. Returns flag dicts for this game."""
    teams = sorted({t for per in lines.values() for t in per})
    if len(teams) < 2:
        return []
    flags = []
    # Spreads: favorite spread per book = the negative number if present,
    # else derive from the dog's positive number.
    spreads, totals, mls = {}, {}, {}
    for book, per in lines.items():
        for team, cell in per.items():
            spread, total, ml = split_cell(cell)
            if spread is not None:
                spreads.setdefault(book, spread)
            if total is not None:
                totals.setdefault(book, total)
            if ml is not None:
                mls.setdefault(book, {})[team] = ml
    if len(spreads) >= 3:
        med = median(spreads.values())
        for book, val in spreads.items():
            div = abs(val - med)
            if div >= SPREAD_MIN:
                # Favorite spread higher than median -> dog number better.
                value_side = "dog" if val > med else "favorite"
                middle = ""
                for key in (3, 7):
                    if (val - key) * (med - key) < 0:
                        middle = f" spans key number {key}"
                flags.append({
                    "league": league, "game": game, "book": book,
                    "market": "spread", "value_side": value_side,
                    "book_value": val, "median_value": med,
                    "divergence": round(div, 2),
                    "note": f"middle pairing{middle}" if middle else "",
                })
    if len(totals) >= 3:
        med = median(totals.values())
        for book, val in totals.items():
            div = abs(val - med)
            if div >= TOTAL_MIN:
                value_side = "Over" if val > med else "Under"
                flags.append({
                    "league": league, "game": game, "book": book,
                    "market": "total", "value_side": value_side,
                    "book_value": val, "median_value": med,
                    "divergence": round(div, 2), "note": "",
                })
    if len(mls) >= 3:
        for team in teams:
            probs = {b: implied_prob(per[team]) for b, per in mls.items()
                     if team in per}
            if len(probs) < 3:
                continue
            med = median(probs.values())
            for book, prob in probs.items():
                div = abs(prob - med)
                if div >= ML_MIN:
                    flags.append({
                        "league": league, "game": game, "book": book,
                        "market": f"moneyline {team}",
                        "value_side": team,
                        "book_value": round(prob, 1),
                        "median_value": round(med, 1),
                        "divergence": round(div, 1), "note": "",
                    })
    return flags


def board_html(flags, last_scan):
    rows = "".join(
        f"<tr><td>{htmlmod.escape(f['game'])}</td>"
        f"<td>{htmlmod.escape(f['book'])}</td>"
        f"<td>{htmlmod.escape(f['market'])}</td>"
        f"<td>{htmlmod.escape(f['value_side'])}</td>"
        f"<td>{f['book_value']} vs median {f['median_value']}</td>"
        f"<td>{htmlmod.escape(f['note'])}</td></tr>"
        for f in flags
    ) or "<tr><td colspan='6'>No hung numbers right now.</td></tr>"
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Hung Numbers</title><style>
body{{background:#0d1117;color:#e6edf3;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:20px;line-height:1.5;font-size:15px}}
table{{border-collapse:collapse;font-size:14px;margin:8px 0}}th,td{{border:1px solid #30363d;padding:6px 10px;text-align:left}}th{{background:#161b22;color:#9da7b3}}.foot{{color:#8b949e;font-size:12px;margin-top:28px;line-height:1.5}}
</style></head><body><h1>Hung Numbers</h1>
<p>ScoresAndOdds public board: a NJ book >=1.0 pt off median spread,
>=1.5 off median total, or >=5 pts of implied probability off median
moneyline. The value side is the better number vs consensus.</p>
<table><tr><th>Game</th><th>Book</th><th>Market</th><th>Value side</th>
<th>Book vs median</th><th>Note</th></tr>{rows}</table>
<p class='foot'>Bruno's caveat: a lone hung number is the play; a book off
everywhere is opinionated pricing — leave it. A flag is a lean to
investigate, not a pick. Last scan: {htmlmod.escape(last_scan)}.
Sources: ScoresAndOdds public odds board.</p></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--repo-path", default="hung-number-board-pc.html")
    args = ap.parse_args()
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    all_flags = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for league, url in BOARD_URLS:
            try:
                page.goto(url, timeout=PAGE_TIMEOUT_MS,
                          wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                dump_path = DATA_DIR / f"dump-{today}.txt" if args.dump else None
                games, _ = extract_games(page, dump_path)
                for game, lines in games:
                    all_flags.extend(flag_game(league, game, lines))
            except Exception as e:  # noqa: BLE001 - one board failing
                print(f"Board fetch failed ({league}): {e}")
        browser.close()
    if not all_flags and not args.dump:
        # Fail loud: never write an empty state as if nothing flagged.
        print("0 flags parsed and no games yielded lines — refusing to")
        print("write state. Re-run with --dump and send the dump file.")
        return 2
    state = load_state("hung-number")
    entries, alerts = state["entries"], []
    for f in all_flags:
        key = f"{f['game']} | {f['book']} | {f['market']}"
        prev = entries.get(key)
        if prev is None or (f["divergence"] - prev["divergence"]) >= RE_ALERT_GROWTH:
            alerts.append(f)
        entries[key] = {**f, "last_seen": now}
    state["meta"]["last_scan"] = now
    save_state("hung-number", state)
    # Banked list.
    with open(DATA_DIR / f"hung-number-{today}.md", "w") as fh:
        fh.write(f"# Hung numbers — {today} (scan {now})\n\n")
        for f in all_flags:
            fh.write(f"- {f['game']} | {f['book']} | {f['market']} | "
                     f"value: {f['value_side']} | book {f['book_value']} "
                     f"vs median {f['median_value']} | div {f['divergence']}"
                     f" {f['note']}\n")
    # Board (local always; push only with --push, calibration name).
    board_path = BOARDS_DIR / "hung-number-board.html"
    with open(board_path, "w") as fh:
        fh.write(board_html(all_flags, now))
    if alerts:
        line = "\n".join(
            f"HUNG NUMBER: {f['game']} — {f['book']} {f['market']} "
            f"value {f['value_side']} ({f['book_value']} vs median "
            f"{f['median_value']}) {f['note']}" for f in alerts)
        print(line)
        with open(DATA_DIR / f"alerts-{today}.log", "a") as fh:
            fh.write(line + "\n")
    else:
        print(f"No new hung numbers ({len(all_flags)} current flags).")
    if args.push:
        ok, detail = put_file(args.repo_path, board_path,
                              "Update hung-number board (PC scan)")
        print(f"Push {args.repo_path}: {'OK ' + detail if ok else 'FAILED ' + detail}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
