#!/usr/bin/env python3
"""NCAAF first-score + lead-change tally (PC, 2026-11-11) — the
college-football sibling of nfl_first_score.py, built at Michael's
ask ("build both, NCAAF first-score + football pace tabs").

ESPN college-football summaries are the exception in the family:
they carry NO "plays" array — scoring lives in top-level
"scoringPlays" (verified on 2026-11-07 final 401858291, Notre Dame
vs Miami: first score Spencer Porath 44-yd FG). Each scoring play
carries team, type abbreviation (FG / TD), period number, clock
seconds remaining, and the post-play away/home score.

Scope: FBS only (ESPN scoreboard groups=80). Games vs FCS
opponents still appear on the FBS scoreboard, so they are in.
Quarters are 15:00, so first-score elapsed is measured against a
900-second quarter.

Same architecture as the other first-score scans:
  - 30-minute scheduler interval; tallies only games that are
    FINAL and not already in state — INCREMENTAL and IDEMPOTENT:
    state tracks ESPN event IDs already tallied, and every tally
    is RECOMPUTED from the stored sequences each run.
  - First-ever run (empty state) BACKFILLS the season from
    2026-08-22 (Week 0) so the trend tables cover the whole season.
  - Regular season only (ESPN season type 2); a game whose
    scoring sequence cannot be verified (summary fetch fails, no
    scoring plays, sequence does not end at the final score) is
    left OUT and retried on a later run — never guessed.

First-score kind: FG / TD / Safety from the play's type
abbreviation. Lead changes: walk the scoring sequence's margin
sign (away - home); a change counts each time the leading team
flips — passing through a tie counts once, when the other team
emerges ahead (the NFL scan's rule).

Outputs (all in data/, synced to pc-data/):
  - ncaaf-first-score-state.json (state.py shape; entries per game)
  - ncaaf-first-score-tally.json {"through", "games", "tally",
    "league", "lead_change_team_avgs"}
  - ncaaf-first-score-games.csv / ncaaf-first-score-sequences.csv
  - ncaaf-first-score-<date>.md banked lines when new games tally

Trends (added 2026-11-12 at Michael's ask): per team and
league-wide, won-by-exactly-one when scoring first — the both-win
rate for a moneyline + opponent +1.5 in-play middle when your
team scores first.
"""
import csv
import json
import statistics
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from config import DATA_DIR  # noqa: E402
from odds_common import bank, now_stamp  # noqa: E402
from state import load_state, save_state  # noqa: E402

ET = ZoneInfo("America/New_York")
SCAN = "ncaaf-first-score"
SEASON_START = "2026-08-22"
SB = ("https://site.api.espn.com/apis/site/v2/sports/football/college-football/"
      "scoreboard?dates={}&groups=80&limit=400")
SUMMARY = ("https://site.api.espn.com/apis/site/v2/sports/football/"
           "college-football/summary?event={}")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"}
KINDS = ("FG", "TD", "Safety")


def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_events(date_str):
    try:
        data = get_json(SB.format(date_str.replace("-", "")))
    except Exception:  # noqa: BLE001 - best-effort probe
        return []
    return data.get("events", [])


def event_finals(events):
    out = []
    for ev in events:
        try:
            comp = ev["competitions"][0]
            if comp["status"]["type"]["name"] != "STATUS_FINAL":
                continue
            season_type = (ev.get("season") or {}).get("type")
            if season_type is not None and season_type != 2:
                continue  # regular season only
            comps = {c["homeAway"]: c for c in comp["competitors"]}
            out.append({
                "id": str(ev["id"]),
                "date_utc": ev.get("date", ""),
                "away": comps["away"]["team"]["displayName"],
                "home": comps["home"]["team"]["displayName"],
                "away_score": int(comps["away"].get("score", -1)),
                "home_score": int(comps["home"].get("score", -1)),
            })
        except Exception:  # noqa: BLE001 - skip malformed events
            continue
    return out


def lead_changes_and_ties(seq_pairs):
    changes, ties, last = 0, 0, 0
    for a, h in seq_pairs:
        if a == h:
            ties += 1
            continue
        sign = 1 if a > h else -1
        if last != 0 and sign != last:
            changes += 1
        last = sign
    return changes, ties


def first_kind(play):
    abbr = ((play.get("type") or {}).get("abbreviation") or "").upper()
    text = ((play.get("type") or {}).get("text") or "").lower()
    if abbr == "FG" or "field goal" in text:
        return "FG"
    if "safety" in text or abbr in ("SAF", "SFTY"):
        return "Safety"
    return "TD"


def process_game(ev):
    """Verify + build the game record, or None (UNVERIFIED)."""
    try:
        summ = get_json(SUMMARY.format(ev["id"]))
    except Exception:  # noqa: BLE001
        return None
    plays = summ.get("scoringPlays") or []
    if not plays:
        return None
    pairs = []
    for p in plays:
        a, h = p.get("awayScore"), p.get("homeScore")
        if a is None or h is None:
            return None
        pairs.append((int(a), int(h)))
    if pairs[-1] != (ev["away_score"], ev["home_score"]):
        return None
    header = (summ.get("header") or {}).get("competitions") or []
    id_to_side = {}
    if header:
        for c in header[0].get("competitors", []):
            team = c.get("team") or {}
            id_to_side[str(team.get("id"))] = c.get("homeAway")
    first_play = plays[0]
    side = id_to_side.get(str((first_play.get("team") or {}).get("id")))
    a0, h0 = pairs[0]
    leader_side = "away" if a0 > h0 else ("home" if h0 > a0 else None)
    if side is not None and leader_side is not None and side != leader_side:
        return None  # cross-check failed — do not guess
    if side is None:
        side = leader_side
    if side is None:
        return None
    first = ev["away"] if side == "away" else ev["home"]
    # elapsed seconds at the first score (quarters are 15:00;
    # clock.value is seconds remaining in the quarter)
    elapsed = None
    period = (first_play.get("period") or {}).get("number")
    clk = (first_play.get("clock") or {}).get("value")
    if period is not None and 1 <= period <= 4 and clk is not None:
        elapsed = int(round((period - 1) * 900 + (900 - float(clk))))
    changes, ties = lead_changes_and_ties(pairs)
    if ev["away_score"] == ev["home_score"]:
        return None  # final tie is impossible post-OT; treat as bad data
    winner = ev["away"] if ev["away_score"] > ev["home_score"] else ev["home"]
    try:
        dt = datetime.fromisoformat(ev["date_utc"].replace("Z", "+00:00"))
        date_et = dt.astimezone(ET).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_et = ""
    return {"date": date_et, "away": ev["away"], "home": ev["home"],
            "first": first, "first_side": side,
            "first_kind": first_kind(first_play),
            "first_elapsed_sec": elapsed,
            "winner": winner,
            "seq": ">".join(f"{a}-{h}" for a, h in pairs),
            "lead_changes": changes, "ties": ties,
            "final": f"{ev['away_score']}-{ev['home_score']}"}


def margin_for(game, team):
    """Final-score margin for team (+N = team won by N), or None."""
    try:
        a, h = (int(x) for x in str(game.get("final", "")).split("-"))
    except (ValueError, TypeError):
        return None
    if team == game.get("away"):
        return a - h
    if team == game.get("home"):
        return h - a
    return None


def recompute(entries):
    games = sorted(entries.values(),
                   key=lambda g: (g["date"], g["away"]))
    tally = {}
    lc_avg = {}
    for g in games:
        for team in (g["away"], g["home"]):
            t = tally.setdefault(team, {
                "gp": 0, "scored_first": 0,
                "kinds": {k: 0 for k in KINDS},
                "won_when_first": 0, "won_when_not_first": 0,
                "won_by_one_when_first": 0})
            t["gp"] += 1
            la = lc_avg.setdefault(team, {"games": 0, "lead_changes": 0})
            la["games"] += 1
            la["lead_changes"] += g["lead_changes"]
            if g["first"] == team:
                if g["winner"] == team:
                    t["won_when_first"] += 1
                    # ML + opponent +1.5 middle: both legs win iff the
                    # first-score team wins by exactly one point.
                    if margin_for(g, team) == 1:
                        t["won_by_one_when_first"] += 1
            elif g["winner"] == team:
                t["won_when_not_first"] += 1
        if g["first"] in tally:
            t = tally[g["first"]]
            t["scored_first"] += 1
            if g["first_kind"] in t["kinds"]:
                t["kinds"][g["first_kind"]] += 1
    for t in tally.values():
        gp_first = t["scored_first"]
        gp_not = t["gp"] - gp_first
        t["scored_first_pct"] = round(100 * gp_first / t["gp"], 1) \
            if t["gp"] else 0.0
        t["win_pct_when_first"] = round(100 * t["won_when_first"] / gp_first, 1) \
            if gp_first else None
        t["win_pct_when_not_first"] = \
            round(100 * t["won_when_not_first"] / gp_not, 1) if gp_not else None
        t["won_by_one_when_first_pct"] = \
            round(100 * t["won_by_one_when_first"] / gp_first, 1) \
            if gp_first else None
    for v in lc_avg.values():
        v["avg_per_game"] = round(v["lead_changes"] / v["games"], 2) \
            if v["games"] else 0.0
    n = len(games)
    elapsed = [g["first_elapsed_sec"] for g in games
               if g.get("first_elapsed_sec") is not None]
    scored = [g for g in games if g.get("first")]
    first_by_one = sum(1 for g in scored
                       if g["first"] == g["winner"]
                       and margin_for(g, g["first"]) == 1)
    league = {
        "games": n,
        "home_scored_first": sum(1 for g in games if g["first_side"] == "home"),
        "first_team_won": sum(1 for g in games if g["first"] == g["winner"]),
        "first_team_won_by_one": first_by_one,
        "first_team_won_by_one_pct": round(100 * first_by_one / len(scored), 1)
        if scored else None,
        "kinds": {k: sum(1 for g in games if g["first_kind"] == k)
                  for k in KINDS},
        "avg_lead_changes": round(sum(g["lead_changes"] for g in games) / n, 2)
        if n else 0.0,
        "median_first_score_sec": int(statistics.median(elapsed))
        if elapsed else None,
    }
    if n:
        league["home_scored_first_pct"] = round(
            100 * league["home_scored_first"] / n, 1)
        league["first_team_win_pct"] = round(
            100 * league["first_team_won"] / n, 1)
    return games, tally, lc_avg, league


def write_outputs(state):
    games, tally, lc_avg, league = recompute(state["entries"])
    through = games[-1]["date"] if games else ""
    standing = {"through": through, "games": games, "tally": tally,
                "league": league, "lead_change_team_avgs": lc_avg}
    (Path(DATA_DIR) / "ncaaf-first-score-tally.json").write_text(
        json.dumps(standing, indent=1))
    with open(Path(DATA_DIR) / "ncaaf-first-score-games.csv", "w",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "away", "home", "first", "first_kind",
                    "first_elapsed_sec", "winner"])
        for g in games:
            w.writerow([g["date"], g["away"], g["home"], g["first"],
                        g["first_kind"], g["first_elapsed_sec"],
                        g["winner"]])
    with open(Path(DATA_DIR) / "ncaaf-first-score-sequences.csv", "w",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "away", "home", "seq", "lead_changes",
                    "ties", "final"])
        for g in games:
            w.writerow([g["date"], g["away"], g["home"], g["seq"],
                        g["lead_changes"], g["ties"], g["final"]])
    return through, games


def fmt_elapsed(sec):
    if sec is None:
        return "?"
    return f"{sec // 60}:{sec % 60:02d}"


def main():
    now = datetime.now(ET)
    today = now.date()
    state = load_state(SCAN)
    backfill = not state["entries"]
    if backfill:
        start = datetime.strptime(SEASON_START, "%Y-%m-%d").date()
        dates = [start + timedelta(days=i)
                 for i in range((today - start).days + 1)]
        print(f"ncaaf-first-score: empty state -- season backfill "
              f"from {SEASON_START} ({len(dates)} dates).")
    else:
        dates = [today - timedelta(days=d) for d in (2, 1, 0)]
    new_games = []
    unverified = 0
    for d in dates:
        for ev in event_finals(fetch_events(d.strftime("%Y-%m-%d"))):
            if ev["id"] in state["entries"]:
                continue
            rec = process_game(ev)
            if rec is None:
                unverified += 1
                continue
            state["entries"][ev["id"]] = rec
            new_games.append(rec)
    state["meta"]["last_run"] = now_stamp()
    if new_games:
        through, games = write_outputs(state)
        state["meta"]["through"] = through
        save_state(SCAN, state)
        lines = [f"## {now_stamp()} -- NCAAF FIRST SCORE (PC): "
                 f"{len(new_games)} new final(s) tallied; season "
                 f"{len(games)} games through {through}"]
        for g in new_games:
            lines.append(
                f"- {g['date']}: {g['away']} @ {g['home']} — first: "
                f"{g['first']} ({g['first_kind']}, "
                f"{fmt_elapsed(g['first_elapsed_sec'])} in); "
                f"lead changes {g['lead_changes']}; "
                f"final {g['final']}")
        if unverified:
            lines.append(f"- note: {unverified} final(s) UNVERIFIED "
                         f"this run — left out, retried next run.")
        bank(SCAN, lines)
    else:
        save_state(SCAN, state)
    print(f"ncaaf-first-score: {len(new_games)} new, {unverified} "
          f"unverified, {len(state['entries'])} total tallied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
