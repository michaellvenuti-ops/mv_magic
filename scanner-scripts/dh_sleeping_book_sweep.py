#!/usr/bin/env python3
"""DarkHorse SLEEPING-BOOK +EV SWEEP (PC port, 2026-11-06) — twin of
the Muse-side 08:30 early sweep; feeds the Positive EV Tracker.

An early-morning second +EV pass, timed to Spanky's stale-book
window — books are slowest to update overnight — ahead of the main
11:44 PC scan (dh_positive_ev.py). Same tracker, same rules:
  - EV >= 5% as displayed (parse shared with dh_positive_ev),
  - BAND RULE: price -500..+500 INCLUSIVE only — outside the band a
    play is NEVER logged, no matter the EV%,
  - event not started (the finder view), NJ-placeable book,
    deduped to the best-priced book, cap top 8 per run,
  - NO inverse pass (the inverse pass belongs to the 11:44 scan).

DEDUPE IS SHARED with dh_positive_ev and dh_derivative_ev through
data/positive-ev-state.json: a play logged here is skipped by the
11:44 run and vice versa — nothing double-logs.

OUTPUT: data/positive-ev-sweep-<date>.csv in the PC tracker columns
for manual import into the "Positive EV Tracker" sheet (the PC
writes no Google Sheets — no credentials on the PC, by design).
Notes read "Paper track only. Added by sleeping-book sweep."
Paper track only — never places a bet.
"""
import sys

import csv

from config import DATA_DIR
from dh_common import FINDER_URLS, open_session, signed_in
from dh_positive_ev import COLUMNS, MIN_EV, in_band, parse_ev_rows
from odds_common import bank, book_placeable, now_stamp, today_str
from state import load_state, save_state

SCAN = "positive-ev-sweep"
SHARED_STATE = "positive-ev"   # shared with dh_positive_ev + derivative
RUN_CAP = 8
NOTE = "Paper track only. Added by sleeping-book sweep."


def main():
    state = load_state(SHARED_STATE)
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
                  "sleeping-book sweep skipped, no prices invented.")
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
            dump_path = DATA_DIR / f"dump-positive-ev-sweep-{today_str()}.txt"
            with open(dump_path, "w", encoding="utf-8") as fh:
                fh.write(page.locator("body").inner_text(timeout=20000))
            print("positive-ev-sweep: session OK but the +EV finder rendered "
                  f"no table rows — body dumped to {dump_path}. "
                  "No prices invented.", file=sys.stderr)
            return 2
        plays = parse_ev_rows(page)
        kept = []
        for play in sorted(plays, key=lambda p: -(p["ev_pct"] or 0)):
            if len(kept) >= RUN_CAP:
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
        save_state(SHARED_STATE, state)
        out_rows = [[today_str(), p["event"], p["play"], p["book"],
                     p["price"], "", p["ev_pct"], "PENDING", NOTE]
                    for p in kept]
        csv_path = DATA_DIR / f"positive-ev-sweep-{today_str()}.csv"
        if out_rows:
            with open(csv_path, "w", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(COLUMNS)
                writer.writerows(out_rows)
        bank(SCAN, [f"## {now_stamp()} -- sleeping-book sweep: "
                    f"{len(kept)} new plays logged; CSV rows: "
                    f"{len(out_rows)} -> {csv_path}"]
             + [f"- {p['event']} | {p['play']} | {p['book']} "
                f"{p['price']} | EV {p['ev_pct']}%" for p in kept])
        print(f"positive-ev-sweep: {len(kept)} new plays "
              f"(shared +EV state dedupe vs the 11:44 scan).")
        return 0
    finally:
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
