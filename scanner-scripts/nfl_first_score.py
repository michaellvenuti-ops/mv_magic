#!/usr/bin/env python3
"""NFL first-score + lead-change tally (PC build, 2026-11-06) —
Michael's upgrade of the Muse-side weekly Tuesday scan.

Muse cadence: one Tuesday run tallies the whole week. PC cadence
(Michael's ask): refresh AFTER EVERY NFL GAME — this script sits on
a 30-minute scheduler interval and SELF-GATES:
  - In a game window (Thu 19:00 ET -> Tue 06:00 ET, covering Thu
    night, Friday specials, Saturday NFL dates, Sunday, Monday
    night) it checks ESPN for finals.
  - Outside the window it still makes ONE cheap scoreboard probe
    for today; a special-date game (e.g. a Wednesday Christmas
    game) proceeds, otherwise it exits in seconds.
  - It tallies only games that are FINAL and not already in state
    — INCREMENTAL and IDEMPOTENT: state tracks ESPN event IDs
    already tallied, and every tally is RECOMPUTED from the stored
    sequences each run (never hand-incremented), so re-running can
    never double-count.
  - First-ever run (empty state) BACKFILLS the season from
    2026-09-01 so the tallies are comparable to the Muse run.
  - Regular season only (ESPN season type 2); a game whose scoring
    sequence cannot be verified (summary fetch fails, empty
    scoringPlays, sequence does not end at the final score) is
    left OUT and retried on a later run — never guessed.

Data: ESPN public API only (scoreboard + summary scoringPlays —
each play carries the post-play away/home score). The first-score
team derives from the sequence's first pair (the leader after it),
cross-checked against the play's team. Lead changes: walk the
sequence's margin sign (away - home); a change counts each time
the leading team flips — passing through a tie counts once, when
the other team emerges ahead. Recomputed from the sequence,
never hand-counted (the Muse rule).

Outputs (all in data/, synced to pc-data/ for testing):
  - nfl-first-score-state.json (state.py shape; entries per game)
  - nfl-first-score-tally.json {"through", "games", "tally",
    "league", "lead_changes", "lead_change_team_avgs"}
  - nfl-first-score-games.csv / nfl-first-score-sequences.csv
  - nfl-first-score-<date>.md banked lines when new games tally
The Muse-side Tuesday scan stays ON untouched during testing.

Trends (added 2026-11-12 at Michael's ask): per team and
league-wide, win % when scoring first vs when not, plus
won-by-exactly-one when scoring first — the both-win rate for a
moneyline + opponent +1.5 in-play middle when your team scores
first. Winner and margin derive from the final score each run.
"""
import csv
import json
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
SCAN = "nfl-first-score"
SEASON_START = "2026-09-01"
SB = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
      "scoreboard?dates={}")
SUMMARY = ("https://site.api.espn.com/apis/site/v2/sports/football/"
           "nfl/summary?event={}")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"}


def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def in_window(now):
    """Thu 19:00 ET -> Tue 06:00 ET game window."""
    wd = now.weekday()  # Mon=0 ... Sun=6
    if wd == 3:  # Thu
        return now.hour >= 19
    if wd in (4, 5, 6, 0):  # Fri Sat Sun Mon
        return True
    if wd == 1:  # Tue
        return now.hour < 6
    return False  # Wed, Tue daytime


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
    first_team_id = str((plays[0].get("team") or {}).get("id"))
    side = id_to_side.get(first_team_id)
    a0, h0 = pairs[0]
    leader_side = "away" if a0 > h0 else ("home" if h0 > a0 else None)
    if side is not None and leader_side is not None and side != leader_side:
        return None  # cross-check failed — do not guess
    if side is None:
        side = leader_side
    if side is None:
        return None
    first = ev["away"] if side == "away" else ev["home"]
    changes, ties = lead_changes_and_ties(pairs)
    try:
        dt = datetime.fromisoformat(ev["date_utc"].replace("Z", "+00:00"))
        date_et = dt.astimezone(ET).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_et = ""
    return {"date": date_et, "away": ev["away"], "home": ev["home"],
            "first": first,
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


def winner_of(game):
    """Winner team name from the final score, or None on a tie."""
    try:
        a, h = (int(x) for x in str(game.get("final", "")).split("-"))
    except (ValueError, TypeError):
        return None
    if a > h:
        return game.get("away")
    if h > a:
        return game.get("home")
    return None


def recompute(entries):
    games = sorted(entries.values(),
                   key=lambda g: (g["date"], g["away"]))
    tally = {}
    lc_avg = {}
    for g in games:
        winner = winner_of(g)
        for team in (g["away"], g["home"]):
            t = tally.setdefault(team, {
                "gp": 0, "scored_first": 0,
                "won_when_first": 0, "won_when_not_first": 0,
                "won_by_one_when_first": 0})
            t["gp"] += 1
            la = lc_avg.setdefault(team, {"games": 0, "lead_changes": 0})
            la["games"] += 1
            la["lead_changes"] += g["lead_changes"]
            if g["first"] == team:
                if winner == team:
                    t["won_when_first"] += 1
                    # ML + opponent +1.5 middle: both legs win iff the
                    # first-score team wins by exactly one point.
                    if margin_for(g, team) == 1:
                        t["won_by_one_when_first"] += 1
            elif winner == team:
                t["won_when_not_first"] += 1
        if g["first"] in tally:
            tally[g["first"]]["scored_first"] += 1
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
    scored = [g for g in games if g.get("first")]
    first_by_one = sum(1 for g in scored
                       if winner_of(g) == g["first"]
                       and margin_for(g, g["first"]) == 1)
    league = {
        "games": n,
        "games_with_goal": len(scored),
        "home_scored_first": sum(1 for g in scored
                                 if g["first"] == g["home"]),
        "first_team_won": sum(1 for g in scored
                              if winner_of(g) == g["first"]),
        "first_team_won_by_one": first_by_one,
        "first_team_won_by_one_pct": round(100 * first_by_one / len(scored), 1)
        if scored else None,
        "avg_lead_changes": round(
            sum(g["lead_changes"] for g in games) / n, 2) if n else 0.0,
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
                "league": league,
                "lead_changes": games, "lead_change_team_avgs": lc_avg}
    (Path(DATA_DIR) / "nfl-first-score-tally.json").write_text(
        json.dumps(standing, indent=1))
    with open(Path(DATA_DIR) / "nfl-first-score-games.csv", "w",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "away", "home", "first"])
        for g in games:
            w.writerow([g["date"], g["away"], g["home"], g["first"]])
    with open(Path(DATA_DIR) / "nfl-first-score-sequences.csv", "w",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "away", "home", "seq", "lead_changes",
                    "ties", "final"])
        for g in games:
            w.writerow([g["date"], g["away"], g["home"], g["seq"],
                        g["lead_changes"], g["ties"], g["final"]])
    return through, games


def main():
    now = datetime.now(ET)
    today = now.date()
    state = load_state(SCAN)
    backfill = not state["entries"]
    if not in_window(now) and not backfill:
        probe = fetch_events(today.strftime("%Y-%m-%d"))
        if not probe:
            print("nfl-first-score: outside game window, no NFL "
                  "events today -- gated exit.")
            return 0
    if backfill:
        start = datetime.strptime(SEASON_START, "%Y-%m-%d").date()
        dates = [start + timedelta(days=i)
                 for i in range((today - start).days + 1)]
        print(f"nfl-first-score: empty state -- season backfill "
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
        lines = [f"## {now_stamp()} -- NFL FIRST SCORE (PC): "
                 f"{len(new_games)} new final(s) tallied; season "
                 f"{len(games)} games through {through}"]
        for g in new_games:
            lines.append(
                f"- {g['date']}: {g['away']} @ {g['home']} — first: "
                f"{g['first']}; lead changes {g['lead_changes']}; "
                f"final {g['final']}")
        if unverified:
            lines.append(f"- note: {unverified} final(s) UNVERIFIED "
                         f"this run — left out, retried next run.")
        bank(SCAN, lines)
    else:
        save_state(SCAN, state)
    print(f"nfl-first-score: {len(new_games)} new, {unverified} "
          f"unverified, {len(state['entries'])} total tallied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
