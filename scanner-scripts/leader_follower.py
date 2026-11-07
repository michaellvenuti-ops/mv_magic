#!/usr/bin/env python3
"""Leader-follower scan (PC port; Spanky batch 6, Ep 51).

Tracks which book moves FIRST. A book earns a LEAD when:
  - it moves >= 1.0 (spread) / >= 1.5 (total) from its OWN prior value,
  - while >= 3 other books still show the old number at that check, AND
  - >= 3 books follow to (or past) its new number by the NEXT check.
Lone moves nobody follows are recorded as no-follow, never credited.

Hard rules (added Nov 5, 2026, after a false PC graduation):
  - ONE lead per book per game per market, ever. Duplicate credits
    for the same (book, game, market) are suppressed, and the stored
    history is de-duplicated on every run.
  - PACK MOVES never credit: when >= 2 books move the same market in
    the same direction in the same check, no book in that pack earns
    a candidate (a lead needs the rest of the board still on the old
    number because ONE book moved first).
  - NO POST-KICKOFF CREDITS: games ESPN shows as already kicked off
    are skipped entirely; pending candidates on them are dropped,
    never resolved against live lines.

ALERTS ONLY after a book reaches 3 leads in the trailing 14 days.
Until then this is a logging phase: state + banked log only, no
alerts printed beyond a summary line. Every alert carries the honesty
footer: board display timing is not proof of who truly moved first.
The first run is a silent full-board baseline.

State: data/leader-follower-state.json
  {"entries": {game_key: {"books": {book: {"spread","total"}},
                          "pending": [candidate leads awaiting follow check]}},
   "meta": {"leads": {book: [{"ts","game","market"}]}, "baseline_done"}}
Banked log: data/leader-follower-<date>.md.
"""
import argparse
import datetime
import sys

from odds_common import (bank, game_started, now_stamp, started_matchups,
                         today_str)
from so_board import BoardUnreadable, load_board
from state import load_state, save_state

SCAN = "leader-follower"
SPREAD_MOVE, TOTAL_MOVE = 1.0, 1.5
MIN_STILL, MIN_FOLLOW = 3, 3
ALERT_LEADS, WINDOW_DAYS = 3, 14
FOOTER = ("Honesty note: board display timing is not proof of who "
          "truly moved first.")


def _moved(old, new, thresh):
    return old is not None and new is not None and abs(new - old) >= thresh


def _already_credited(leads, book, game_key, market):
    """One lead per (book, game, market) — the de-dupe rule."""
    return any(h.get("game") == game_key and h.get("market") == market
               for h in leads.get(book, []))


def _dedupe_leads(leads):
    """Collapse duplicate (game, market) credits per book, keeping
    the earliest. Repairs histories written before the de-dupe rule
    (e.g. the UCLA @ Minnesota spread lead credited twice on Nov 5).
    Returns how many duplicates were removed."""
    removed = 0
    for book, hist in leads.items():
        seen, kept = set(), []
        for h in sorted(hist, key=lambda x: x.get("ts", "")):
            sig = (h.get("game"), h.get("market"))
            if sig in seen:
                removed += 1
                continue
            seen.add(sig)
            kept.append(h)
        leads[book] = kept
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()
    state = load_state(SCAN)
    leads = state["meta"].setdefault("leads", {})
    dupes_removed = _dedupe_leads(leads)
    if dupes_removed:
        bank(SCAN, [f"## {now_stamp()} — de-dupe: removed "
                    f"{dupes_removed} duplicate lead credit(s)"])
    games = []
    for league in ("NFL", "NCAAF"):
        try:
            lg, _ = load_board(league)
            games += lg
        except BoardUnreadable as e:
            bank(SCAN, [f"## {now_stamp()} — {league} unreadable: {e}"])
    started = started_matchups(("NFL", "NCAAF"))
    baseline = not state["meta"].get("baseline_done")
    new_leads, alerts = [], []
    skipped_started = 0
    for game in games:
        key = f"{game['away']} @ {game['home']} | {today_str()}"
        entry = state["entries"].setdefault(key, {"books": {}, "pending": []})
        if game_started(game, started):
            # Post-kickoff: never resolve or credit against live
            # lines. Drop any pending candidates uncredited.
            skipped_started += 1
            if entry["pending"]:
                bank(SCAN, [f"## {now_stamp()} — kickoff passed, "
                            f"dropped {len(entry['pending'])} pending "
                            f"candidate(s) uncredited: {key}"])
                entry["pending"] = []
            continue
        prior_books = entry["books"]
        cur_books = {}
        for book, lines in game["books"].items():
            cur_books[book] = {"spread": lines.get("home_spread"),
                               "total": lines.get("total")}
        # Resolve pending candidates from the previous check: did >= 3
        # books follow to (or past) the candidate's new number?
        still_pending = []
        for cand in entry["pending"]:
            book, market, new_val = cand["book"], cand["market"], cand["new"]
            follows = 0
            for other, vals in cur_books.items():
                if other == book:
                    continue
                v = vals.get(market)
                if v is None or new_val is None:
                    continue
                if market == "spread":
                    followed = (v <= new_val) if cand["direction"] < 0 else (v >= new_val)
                else:
                    followed = (v >= new_val) if cand["direction"] > 0 else (v <= new_val)
                if followed:
                    follows += 1
            if follows >= MIN_FOLLOW:
                if _already_credited(leads, book, key, market):
                    bank(SCAN, [f"## {now_stamp()} — duplicate lead "
                                f"suppressed: {book} {key} {market} "
                                f"(already credited once)"])
                else:
                    leads.setdefault(book, []).append(
                        {"ts": now_stamp(), "game": key,
                         "market": market})
                    new_leads.append((book, key, market))
            else:
                bank(SCAN, [f"## {now_stamp()} — no-follow: {book} {key} "
                            f"{market} (only {follows} followed)"])
            # candidates resolve in one check either way
        entry["pending"] = still_pending
        # Detect fresh candidate leads vs this book's own prior value.
        if not baseline:
            moves = []
            for book, vals in cur_books.items():
                prior = prior_books.get(book)
                if not prior:
                    continue
                for market, thresh in (("spread", SPREAD_MOVE),
                                       ("total", TOTAL_MOVE)):
                    old, new = prior.get(market), vals.get(market)
                    if _moved(old, new, thresh):
                        moves.append({"book": book, "market": market,
                                      "old": old, "new": new,
                                      "direction": 1 if new > old else -1})
            # Pack rule: >= 2 books moving the same market the same
            # way in one check = a pack move; nobody in it leads.
            pack = {(m["market"], m["direction"]) for m in moves
                    if sum(1 for o in moves
                           if o["market"] == m["market"]
                           and o["direction"] == m["direction"]) >= 2}
            for market, direction in sorted(pack):
                books = [m["book"] for m in moves
                         if m["market"] == market
                         and m["direction"] == direction]
                bank(SCAN, [f"## {now_stamp()} — pack move, never "
                            f"credited: {key} {market} "
                            f"({', '.join(sorted(books))})"])
            for m in moves:
                if (m["market"], m["direction"]) in pack:
                    continue
                book, market = m["book"], m["market"]
                old, new = m["old"], m["new"]
                still = sum(1 for o, ov in prior_books.items()
                            if o != book and ov.get(market) == old)
                if still < MIN_STILL:
                    continue
                if _already_credited(leads, book, key, market):
                    continue
                if any(c["book"] == book and c["market"] == market
                       for c in entry["pending"]):
                    continue
                entry["pending"].append(
                    {"book": book, "market": market, "old": old,
                     "new": new, "direction": m["direction"],
                     "ts": now_stamp()})
        entry["books"] = cur_books
    state["meta"]["baseline_done"] = True
    # Alert check: 3+ leads in trailing 14 days.
    cutoff = (datetime.datetime.now().astimezone()
              - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    alerted = state["meta"].setdefault("alerted_books", [])
    # Self-heal: a book whose count falls back below the bar after
    # de-duping (the Nov 5 false graduation) loses its alerted flag,
    # so the false "graduated" state cannot persist or re-fire.
    alerted[:] = [b for b in alerted
                  if sum(1 for h in leads.get(b, [])
                         if h["ts"] >= cutoff) >= ALERT_LEADS]
    for book, hist in leads.items():
        recent = [h for h in hist if h["ts"] >= cutoff]
        if len(recent) >= ALERT_LEADS and book not in alerted:
            alerted.append(book)
            alerts.append((book, len(recent)))
    save_state(SCAN, state)
    lines = [f"## {now_stamp()} — {len(games)} games"
             + (" (BASELINE)" if baseline else "")
             + f", {len(new_leads)} leads credited"
             + (f", {skipped_started} started games skipped"
                if skipped_started else "")]
    for book, key, market in new_leads:
        lines.append(f"LEAD: {book} on {key} ({market})")
    bank(SCAN, lines)
    for book, count in alerts:
        print(f"LEADER ALERT: {book} reached {count} leads in "
              f"{WINDOW_DAYS} days. {FOOTER}")
    if not alerts:
        print(f"leader-follower: logging phase ({len(games)} games, "
              f"{len(new_leads)} new leads, no book at {ALERT_LEADS} yet).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
