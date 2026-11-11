#!/usr/bin/env python3
"""DarkHorse DERIVATIVE +EV scan (PC port, 2026-11-06) — twin of the
Muse-side daily derivative scan; feeds the Positive EV Tracker.

Spanky's edge: when a book moves its full-game line, its derivative
markets (first half, quarters, team totals) often do NOT move in
unison — this scan hunts +EV in derivative segments ONLY and logs
qualifying plays at $100 flat.

Rules (identical to the Muse scan + the PC main +EV scan):
  - EV >= 5% as displayed (same parse as dh_positive_ev),
  - BAND RULE: price -500..+500 INCLUSIVE only,
  - DERIVATIVE ONLY: the row text must carry a derivative segment
    marker (1st/2nd half, quarter, team total) — full-game sides,
    full-game totals, moneylines and player props are excluded by
    construction (they carry no segment marker),
  - NJ-placeable book, deduped, cap top 8 per day,
  - DEDUPE IS SHARED with the PC main scan and the sleeping-book
    sweep through data/positive-ev-state.json: a play logged by any
    of the three is skipped by the others, so nothing double-logs.

OUTPUT: data/derivative-ev-<date>.csv in the PC tracker columns for
manual import into the "Positive EV Tracker" sheet (the PC writes
no Google Sheets — no credentials on the PC, by design; same as
dh_positive_ev). Notes read "Paper track only. Added by derivative
+EV scan (derivatives lag full-game moves -- Spanky)." Paper track
only — never places a bet.
"""
import argparse
import csv
import re
import sys

from config import DATA_DIR
from dh_common import (FINDER_URLS, extract_rows, open_session,
                       parse_pair_row, signed_in)
from dh_pairs import legs_from_candidate
from dh_positive_ev import COLUMNS, MIN_EV, in_band
from odds_common import bank, book_placeable, now_stamp, today_str
from state import load_state, save_state

SCAN = "derivative-ev"
SHARED_STATE = "positive-ev"   # shared with dh_positive_ev + sweep
DAILY_CAP = 8
NOTE = ("Paper track only. Added by derivative +EV scan "
        "(derivatives lag full-game moves -- Spanky).")

DERIVATIVE_RE = re.compile(
    r"\b(1st|first|2nd|second)\s*(half|quarter)\b"
    r"|\b[1-4](st|nd|rd|th)\s*quarter\b"
    r"|\bteam\s*total\b|\b1h\b|\b2h\b|\bq[1-4]\b", re.I)


def parse_derivative_rows(page):
    rows, _raw = extract_rows(page)
    plays = []
    for cells in rows:
        cand = parse_pair_row(cells)
        if not cand:
            continue
        if not DERIVATIVE_RE.search(cand["raw"]):
            continue
        legs = legs_from_candidate(cand)
        if not legs:
            continue
        leg = legs[0]
        ev = max(cand["pcts"]) if cand["pcts"] else None
        plays.append({"event": cells[0] if cells else "",
                      "play": leg["side"], "book": leg["book"],
                      "price": leg["price"], "ev_pct": ev})
    return plays


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()
    state = load_state(SHARED_STATE)
    day_key = f"derivative_logged_{today_str()}"
    logged_today = state["meta"].get(day_key, 0)
    pw, ctx = open_session()
    try:
        page = ctx.new_page()
        # Gate on the SESSION via the middles finder — the page
        # dh_common.signed_in was calibrated on. The /positive-ev
        # page never satisfies the gate in this profile (its SPA
        # stays a shell through the gate's full 45s poll), so gating
        # on it skipped every run Nov 5-10 while middles/arbitrage/
        # low-holds passed minutes apart in the same profile. After
        # the session gate, navigate to the +EV finder and judge it
        # by table rows; no rows = loud failure with a body dump,
        # never a silent skip. (Fix drafted Nov 10, 2026.)
        if not signed_in(page, FINDER_URLS["middles"]):
            print("DarkHorse check unavailable (signed out) -- "
                  "derivative-ev skipped, no prices invented.")
            return 0
        page.goto(FINDER_URLS["positive_ev"], timeout=60000,
                  wait_until="domcontentloaded")
        try:
            page.wait_for_function(
                "document.querySelectorAll('table tr').length > 5",
                timeout=60000)
        except Exception:  # noqa: BLE001 - judged by the count below
            pass
        if page.locator("table tr").count() == 0:
            dump_path = DATA_DIR / f"dump-derivative-ev-{today_str()}.txt"
            with open(dump_path, "w", encoding="utf-8") as fh:
                fh.write(page.locator("body").inner_text(timeout=20000))
            print("derivative-ev: session OK but the +EV finder rendered "
                  f"no table rows — body dumped to {dump_path}. "
                  "No prices invented.", file=sys.stderr)
            return 2
        plays = parse_derivative_rows(page)
        kept = []
        for play in sorted(plays, key=lambda p: -(p["ev_pct"] or 0)):
            if len(kept) + logged_today >= DAILY_CAP:
                break
            if play["ev_pct"] is None or play["ev_pct"] < MIN_EV:
                continue
            if not in_band(play["price"]) or not book_placeable(play["book"]):
                continue
            key = f"{play['event']} | {play['play']}"
            if key in state["entries"]:
                continue
            state["entries"][key] = {**play,
                                     "first_logged": now_stamp(),
                                     "source_scan": SCAN}
            kept.append(play)
        state["meta"][day_key] = logged_today + len(kept)
        save_state(SHARED_STATE, state)
        out_rows = [[today_str(), p["event"], p["play"], p["book"],
                     p["price"], "", p["ev_pct"], "PENDING", NOTE]
                    for p in kept]
        csv_path = DATA_DIR / f"derivative-ev-{today_str()}.csv"
        if out_rows:
            with open(csv_path, "w", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(COLUMNS)
                writer.writerows(out_rows)
        bank(SCAN, [f"## {now_stamp()} -- derivative-ev: {len(kept)} "
                    f"new derivative plays logged; CSV rows: "
                    f"{len(out_rows)} -> {csv_path}"]
             + [f"- {p['event']} | {p['play']} | {p['book']} "
                f"{p['price']} | EV {p['ev_pct']}%" for p in kept])
        print(f"derivative-ev: {len(kept)} new derivative plays "
              f"(shared +EV state; cap {DAILY_CAP}/day).")
        return 0
    finally:
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
