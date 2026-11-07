#!/usr/bin/env python3
"""Promo converter WATCH (PC port, 2026-11-06) — twin of the
Muse-side daily promo-converter watch.

Design (identical to the Muse watch):
  (a) BASELINE-SILENT: the first run records everything currently
      on the DarkHorse Promo Dashboard as the baseline and delivers
      NOTHING — Michael has already run through most standing
      promos himself.
  (b) After the baseline, a run delivers only NEW convertible
      promos at his books: Profit Boost, Bonus Bet(s), Site Credit,
      Second Chance / No Sweat, Qualifying Bet (bet X get Y). For
      each (cap 3 per run) it runs the matching on-demand converter
      (dh_promo_convert.py) with the book + amount/boost EXACTLY as
      the dashboard states them. If the dashboard states no amount
      (or no boost % for a profit boost), the promo notice is banked
      alone with a "converter needs the amount/boost from his
      account offer" note — an amount is NEVER invented.
  Plain deposit matches, odds boosts with no stated %, and
  new-user-only offers are banked silently, never converted.

His books: FanDuel, DraftKings, BetMGM, Caesars, Hard Rock,
Fanatics, Bally Bet, BetRivers / BetParx, Kalshi, Novig,
Polymarket US, ProphetX. theScore legs carry a licensing-unverified
flag. Hedge pairs exclude Polymarket International (the converter
enforces NJ-placeability). Never places a bet, never claims a
promo.

CALIBRATION SURFACE: the Promo Dashboard URL + DOM are not visible
from the Muse side. The watch probes candidate paths, requires
promo markers + a known book in the page text, and dumps the raw
dashboard text to data/promo-dashboard-dump-<date>.txt every run so
the parser can be patched from real output. Promo extraction is
line-block best-effort: a block starts at a line naming one of his
books and gathers the following lines as title/terms.

Output: data/promo-converter-<date>.md (full dashboard list every
run + NEW PROMO deliverable lines), state
data/promo-converter-state.json. Scheduler: daily 09:54 ET.
"""
import re
import subprocess
import sys
from pathlib import Path

from config import DATA_DIR
from dh_common import KNOWN_BOOKS, open_session, signed_in
from odds_common import bank, now_stamp, today_str
from state import load_state, save_state

HERE = Path(__file__).resolve().parent
SCAN = "promo-converter"
CONVERT_CAP = 3

DASHBOARD_CANDIDATES = [
    "https://darkhorseodds.com/promos",
    "https://darkhorseodds.com/promo-dashboard",
    "https://darkhorseodds.com/promotions",
    "https://darkhorseodds.com/dashboard",
]

HIS_BOOKS = ["FanDuel", "DraftKings", "BetMGM", "Caesars", "Hard Rock",
             "HardRock", "Fanatics", "Bally Bet", "BetRivers",
             "BetParx", "Kalshi", "Novig", "Polymarket US", "ProphetX",
             "theScore"]
PROMO_MARKERS = ["boost", "bonus", "credit", "second chance",
                 "no sweat", "qualifying", "promo"]
AMOUNT_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)")
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
NEW_USER_RE = re.compile(r"new (user|customer|member)|sign[ -]?up", re.I)
DEPOSIT_RE = re.compile(r"deposit match", re.I)


def classify(blob):
    """-> converter type or None (bank-only / not convertible)."""
    low = blob.lower()
    if NEW_USER_RE.search(blob) or DEPOSIT_RE.search(blob):
        return None
    if "second chance" in low or "no sweat" in low:
        return "second-chance"
    if "site credit" in low:
        return "site-credit"
    if "bonus bet" in low:
        return "bonus-bet"
    if "profit boost" in low or "odds boost" in low:
        return "profit-boost"
    if "qualifying" in low or re.search(r"bet\s+\$?[\d,]+\s*(and\s+)?get",
                                        low):
        return "qualifying-bet"
    return None


def extract_promos(text):
    """Line-block best-effort promo extraction (see module doc)."""
    lines = [ln.strip() for ln in text.splitlines()]
    promos = []
    i = 0
    while i < len(lines):
        line = lines[i]
        book = next((b for b in HIS_BOOKS
                     if b.lower() in line.lower()), None)
        if book and any(m in line.lower() for m in PROMO_MARKERS):
            blob_lines = [line]
            j = i + 1
            while j < len(lines) and len(blob_lines) < 5:
                nxt = lines[j]
                if not nxt:
                    j += 1
                    break
                if any(b.lower() in nxt.lower() for b in HIS_BOOKS) \
                        and any(m in nxt.lower() for m in PROMO_MARKERS):
                    break
                blob_lines.append(nxt)
                j += 1
            blob = " ".join(blob_lines)
            key = f"{book} | {' '.join(blob.split())[:120]}"
            promos.append({"key": key, "book": book, "title": blob})
            i = j
        else:
            i += 1
    seen = {}
    for p in promos:
        seen[p["key"]] = p
    return list(seen.values())


def run_converter(ctype, book, amount, boost):
    cmd = [sys.executable, str(HERE / "dh_promo_convert.py"),
           "--type", ctype, "--book", book, "--amount", str(amount)]
    if boost is not None:
        cmd += ["--boost", str(boost)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=600, cwd=HERE)
        return (proc.stdout or proc.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        return f"converter run failed: {exc}"


def main():
    state = load_state(SCAN)
    baseline_done = bool(state["meta"].get("baseline_done"))
    pw, ctx = open_session()
    dashboard_text = ""
    dashboard_url = None
    try:
        page = ctx.new_page()
        for url in DASHBOARD_CANDIDATES:
            try:
                if not signed_in(page, url):
                    continue
                text = page.locator("body").inner_text(timeout=20000)
            except Exception:  # noqa: BLE001 - try the next candidate
                continue
            if "promo" in text.lower() and \
                    any(b.lower() in text.lower() for b in KNOWN_BOOKS):
                dashboard_text, dashboard_url = text, url
                break
            if not dashboard_text:
                dashboard_text = text  # keep for the dump
    finally:
        ctx.close()
        pw.stop()
    if not dashboard_url:
        print("DarkHorse check unavailable (signed out) or Promo "
              "Dashboard not found -- promo watch skipped, nothing "
              "invented.")
        if dashboard_text:
            dump = DATA_DIR / f"promo-dashboard-dump-{today_str()}.txt"
            dump.write_text(dashboard_text, encoding="utf-8")
        return 0
    dump = DATA_DIR / f"promo-dashboard-dump-{today_str()}.txt"
    dump.write_text(dashboard_text, encoding="utf-8")

    promos = extract_promos(dashboard_text)
    new_promos = []
    for p in promos:
        if p["key"] not in state["entries"]:
            state["entries"][p["key"]] = {
                "book": p["book"], "promo": p["title"],
                "first_seen": now_stamp()}
            new_promos.append(p)
    state["meta"]["last_check"] = now_stamp()
    lines = [f"## {now_stamp()} -- promo dashboard ({dashboard_url}): "
             f"{len(promos)} promos listed, {len(new_promos)} new"
             + ("" if baseline_done else " (BASELINE run — silent)")]
    for p in promos:
        lines.append(f"  listed: {p['key']}")

    if not baseline_done:
        state["meta"]["baseline_done"] = True
        save_state(SCAN, state)
        bank(SCAN, lines)
        print(f"promo-watch: baseline recorded ({len(promos)} promos) "
              f"-- silent by design.")
        return 0

    conversions = 0
    for p in new_promos:
        ctype = classify(p["title"])
        if ctype is None:
            continue
        if conversions >= CONVERT_CAP:
            lines.append(f"- NEW PROMO (PC calibration): {p['key']} -- "
                         f"converter cap reached this run; notice only.")
            continue
        amt_m = AMOUNT_RE.search(p["title"])
        pct_m = PCT_RE.search(p["title"])
        amount = (float(amt_m.group(1).replace(",", ""))
                  if amt_m else None)
        boost = float(pct_m.group(1)) if pct_m else None
        flag = (" [theScore leg: licensing unverified]"
                if p["book"] == "theScore" else "")
        if amount is None or (ctype == "profit-boost" and boost is None):
            lines.append(
                f"- NEW PROMO (PC calibration): {p['key']} -- "
                f"convertible ({ctype}) but the dashboard states no "
                f"{'amount' if amount is None else 'boost %'}; "
                f"converter needs it from his account offer.{flag}")
            conversions += 1
            continue
        result = run_converter(ctype, p["book"], amount,
                               boost if ctype == "profit-boost" else None)
        lines.append(f"- NEW PROMO (PC calibration): {p['key']} -- "
                     f"{ctype} converter: {result}{flag}")
        conversions += 1
    save_state(SCAN, state)
    bank(SCAN, lines)
    print(f"promo-watch: {len(promos)} listed, {len(new_promos)} new, "
          f"{conversions} converter attempt(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
