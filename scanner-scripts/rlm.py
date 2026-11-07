#!/usr/bin/env python3
"""Reverse-line-move (RLM) scan (PC port; Spanky batch 6, Eps 51+39).

Flags today's NFL/FBS games where >= 70% of TICKETS sit on one side of
the spread/total AND the median line moved >= 1.0 (spread) / >= 1.5
(total) TOWARD the minority side vs the stored line. The minority side
is the value side. RLM is a signal, not proof of sharp money.

Rules carried from the live scan:
- First sightings record silently (no baseline = no flag).
- News-explained moves are NOT flagged: a best-effort headline check
  (ESPN RSS via requests) runs on flagged candidates and its result is
  noted in the banked list; a candidate with a clear news explanation
  (QB out, suspension, weather postponement) is banked, not flagged.
- Depends on the same public splits source as public_fade.py; same
  gate behavior (one gate message + gate flag file, then silent).
- Deliverable = NEW flags, or a known flag whose move widened 0.5+
  further toward the minority side.

State: data/rlm-state.json ({"entries", "meta"} with meta.lines holding
stored medians). Banked: data/rlm-<date>.md.
"""
import argparse
import re
import sys
from pathlib import Path

import requests

from config import DATA_DIR
from odds_common import bank, now_stamp, today_str
from so_board import BoardUnreadable, load_board, medians, parse_splits
from state import load_state, save_state

SCAN = "rlm"
GATE_FILE = Path(DATA_DIR) / "rlm-gate-notified"
TICKETS_MIN = 70.0
SPREAD_MOVE, TOTAL_MOVE = 1.0, 1.5
WIDEN = 0.5
NEWS_WORDS = ("out for", "ruled out", "suspended", "injury", "injured",
              "will not play", "season-ending", "postponed")


def news_explains(away, home):
    """Best-effort headline check; returns (explained, note)."""
    try:
        resp = requests.get(
            "https://www.espn.com/espn/rss/news",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        text = resp.text.lower()
    except requests.RequestException as e:
        return False, f"headline check unavailable ({e})"
    for team in (away.lower(), home.lower()):
        for w in NEWS_WORDS:
            if team in text and w in text:
                return True, f"headline mentions {team} + '{w}'"
    return False, "no explaining headline found"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()
    state = load_state(SCAN)
    stored = state["meta"].setdefault("lines", {})
    games, htmls = [], []
    for league in ("NFL", "NCAAF"):
        try:
            lg, html_text = load_board(league)
            games += lg
            htmls.append(html_text)
        except BoardUnreadable as e:
            bank(SCAN, [f"## {now_stamp()} — {league} unreadable: {e}"])
    if not any(parse_splits(h) for h in htmls):
        if not GATE_FILE.exists():
            GATE_FILE.write_text(now_stamp())
            print("RLM GATE: no public splits source validated; RLM "
                  "needs ticket % to run. One-time message; later runs "
                  "stay silent until data/rlm-gate-notified is removed.")
        bank(SCAN, [f"## {now_stamp()} — GATE: no splits source."])
        return 0
    new_flags = []
    for game in games:
        key = f"{game['away']} @ {game['home']} | {today_str()}"
        med_spread, med_total = medians(game)
        sp = game.get("splits") or {}
        prev = stored.get(key)
        if prev is not None:
            checks = (
                ("spread", prev.get("spread"), med_spread, SPREAD_MOVE,
                 sp.get("bets_spread_home_pct")),
                ("total", prev.get("total"), med_total, TOTAL_MOVE,
                 sp.get("bets_total_over_pct")))
            for market, old, new, thresh, home_pct in checks:
                if old is None or new is None or home_pct is None:
                    continue
                move = new - old
                if abs(move) < thresh:
                    continue
                # Majority side: home_pct >= 70 -> public on home/Over.
                if home_pct >= TICKETS_MIN:
                    minority_home = False
                    toward_minority = move > 0  # line moved toward away
                elif (100 - home_pct) >= TICKETS_MIN:
                    minority_home = True
                    toward_minority = move < 0
                else:
                    continue
                if not toward_minority:
                    continue
                explained, note = news_explains(game["away"], game["home"])
                fkey = f"{key} | {market}"
                prior = state["entries"].get(fkey)
                widened = (prior is not None and
                           abs(move) - abs(prior.get("move", 0)) >= WIDEN)
                if explained:
                    bank(SCAN, [f"## {now_stamp()} — NOT flagged "
                                f"(news-explained): {fkey} move {move:+.1f}; "
                                f"news check: {note}"])
                    continue
                if prior is None or widened:
                    flag = {"game": key, "market": market, "move": move,
                            "minority_side": (game["home"] if minority_home
                                              else game["away"]),
                            "tickets_majority_pct": max(home_pct,
                                                        100 - home_pct),
                            "news_check": note,
                            "first_flagged": now_stamp()}
                    state["entries"][fkey] = flag
                    new_flags.append(flag)
        stored[key] = {"spread": med_spread, "total": med_total,
                       "seen": now_stamp()}
    save_state(SCAN, state)
    lines = [f"## {now_stamp()} — {len(games)} games, "
             f"{len(new_flags)} NEW/widened RLM flags",
             "RLM is a signal, not proof of sharp money."]
    for f in new_flags:
        lines.append(f"FLAG: {f['game']} {f['market']} moved {f['move']:+.1f} "
                     f"toward minority side {f['minority_side']} against "
                     f"{f['tickets_majority_pct']:.0f}% tickets; "
                     f"news check: {f['news_check']}")
    bank(SCAN, lines)
    for f in new_flags:
        print(f"RLM FLAG: {f['game']} | {f['market']} {f['move']:+.1f} toward "
              f"{f['minority_side']} (public {f['tickets_majority_pct']:.0f}% "
              f"the other way) | news: {f['news_check']}")
    if not new_flags:
        print("rlm: no new flags (silent).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
