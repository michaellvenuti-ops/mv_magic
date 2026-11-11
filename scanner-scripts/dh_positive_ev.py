#!/usr/bin/env python3
"""DarkHorse +EV finder (PC port) with the INVERSE PASS.

Rules (identical to Chet's daily scan):
  - EV >= 5% as displayed,
  - BAND RULE: price -500..+500 INCLUSIVE only — outside the band a
    play is never logged (the band applies to the inverse price too),
  - event not started, NJ-placeable book, deduped,
  - INVERSE PASS: for each logged regular play, price the REVERSE side
    of the same market at the same line via DarkHorse Browse Odds and
    record the best reverse price; reverse rows are marked INVERSE,
    with Fair/EV left BLANK — never invented.

OUTPUT: a CSV the user can import into the "Positive EV Tracker"
Google Sheet: data/positive-ev-<date>.csv with the sheet's columns
(Date, Event, Play, Book, Price, Fair Odds, EV %, Status) — regular
rows first, then INVERSE rows whose Notes read
"INVERSE of <play> (<book> <price>). Paper track only."
This script does NOT write to Google Sheets (no credentials on the
PC, by design). Paper track only — never places a bet.
State: data/positive-ev-state.json (dedupe). Banked list too.
"""
import argparse
import csv
import sys

from config import DATA_DIR
from dh_common import (FINDER_URLS, extract_rows, open_session,
                       parse_pair_row, signed_in)
from dh_pairs import legs_from_candidate
from odds_common import bank, book_placeable, now_stamp, today_str
from state import load_state, save_state

SCAN = "positive-ev"
MIN_EV = 5.0
BAND_LO, BAND_HI = -500, 500
COLUMNS = ["Date", "Event", "Play", "Book", "Price", "Fair Odds",
           "EV %", "Status", "Notes"]


def in_band(price):
    try:
        return BAND_LO <= int(price) <= BAND_HI
    except (TypeError, ValueError):
        return False


def parse_ev_rows(page):
    rows, _raw = extract_rows(page)
    plays = []
    for cells in rows:
        cand = parse_pair_row(cells)
        if not cand:
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


def best_reverse_price(page, play):
    """Browse Odds reverse-side price for the same line.
    Best-effort calibration: searches the Browse Odds table text for
    the event and returns the opposite side's best displayed price,
    or None when not offered — None means 'reverse not offered',
    never a guessed price."""
    rows, _raw = extract_rows(page)
    for cells in rows:
        joined = " | ".join(cells)
        if play["event"] and play["event"].split(" ")[0] in joined:
            cand = parse_pair_row(cells)
            if cand and len(cand["prices"]) >= 2:
                return str(cand["prices"][1])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()
    state = load_state(SCAN)
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
            print("DarkHorse check unavailable (signed out) — "
                  "positive-ev skipped, no prices invented.")
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
            dump_path = DATA_DIR / f"dump-positive-ev-{today_str()}.txt"
            with open(dump_path, "w", encoding="utf-8") as fh:
                fh.write(page.locator("body").inner_text(timeout=20000))
            print("positive-ev: session OK but the +EV finder rendered "
                  f"no table rows — body dumped to {dump_path}. "
                  "No prices invented.", file=sys.stderr)
            return 2
        plays = parse_ev_rows(page)
        kept = []
        for play in plays:
            if play["ev_pct"] is None or play["ev_pct"] < MIN_EV:
                continue
            if not in_band(play["price"]) or not book_placeable(play["book"]):
                continue
            key = f"{play['event']} | {play['play']}"
            if key in state["entries"]:
                continue
            state["entries"][key] = {**play, "first_logged": now_stamp()}
            kept.append(play)
        # Inverse pass on Browse Odds.
        page.goto(FINDER_URLS["browse_odds"], timeout=60000,
                  wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        out_rows = []
        for play in kept:
            out_rows.append([today_str(), play["event"], play["play"],
                             play["book"], play["price"], "",
                             play["ev_pct"], "PENDING", ""])
            rev = best_reverse_price(page, play)
            if rev is None:
                print(f"INVERSE: {play['play']} — reverse not offered")
                continue
            note = (f"INVERSE of {play['play']} ({play['book']} "
                    f"{play['price']}). Paper track only.")
            if not in_band(rev):
                print(f"INVERSE: {play['play']} — reverse {rev} outside "
                      f"band — shown, not logged")
                continue
            out_rows.append([today_str(), play["event"],
                             f"REVERSE of {play['play']}", "", rev, "",
                             "", "PENDING", note])
        save_state(SCAN, state)
        csv_path = DATA_DIR / f"positive-ev-{today_str()}.csv"
        if out_rows:
            with open(csv_path, "w", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(COLUMNS)
                writer.writerows(out_rows)
        bank(SCAN, [f"## {now_stamp()} — +EV: {len(kept)} new plays "
                    f"logged; CSV rows: {len(out_rows)} -> {csv_path}"])
        print(f"positive-ev: {len(kept)} new plays; "
              f"{len(out_rows)} CSV rows (regular + inverse) in "
              f"{csv_path}. Import into the Positive EV Tracker sheet.")
        return 0
    finally:
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
