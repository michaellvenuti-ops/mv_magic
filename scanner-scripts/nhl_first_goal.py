#!/usr/bin/env python3
"""NHL first-goal + lead-change tally (PC, 2026-11-11) — the NHL
sibling of nfl_first_score.py, built at Michael's ask ("same for
NBA and NHL — I want to see if there are any trends").

Same architecture as the NFL scan:
  - 30-minute scheduler interval; tallies only games that are
    FINAL and not already in state — INCREMENTAL and IDEMPOTENT:
    state tracks ESPN event IDs already tallied, and every tally
    is RECOMPUTED from the stored sequences each run.
  - First-ever run (empty state) BACKFILLS the season from
    2026-10-01 so the trend tables cover the whole season.
  - Regular season only (ESPN season type 2); a game whose
    scoring sequence cannot be verified is left OUT and retried
    on a later run — never guessed.

Data: ESPN public API only (scoreboard + summary plays — every
play carries the post-play away/home score; goals are flagged
scoringPlay and carry a strength field). Shootout handling:
shootout attempts live in period 5 and do NOT move the recorded
score, so the sequence uses periods 1-4 only (regulation + OT);
a game is verified when the sequence ends at the final score,
OR ends tied with the final exactly one goal higher on one side
(the shootout decider). The first goal also records its strength
(EV / PP / SH) and scorer. Lead changes: walk the sequence's
margin sign (away - home); a change counts each time the leading
team flips — passing through a tie counts once (the NFL rule).

Trends computed per team and league-wide: games, first-goal
count + %, strength mix of first goals, win % when scoring
first vs when not, avg lead changes, and the time of the first
goal (seconds into the game). Plus, per team and league-wide,
won-by-exactly-one when scoring first — the both-win rate for a
moneyline + opponent +1.5 in-play middle when your team scores
first (added 2026-11-12 at Michael's ask).

Outputs (all in data/, synced to pc-data/):
  - nhl-first-goal-state.json (state.py shape; entries per game)
  - nhl-first-goal-tally.json {"through", "games", "tally",
    "league", "lead_change_team_avgs"}
  - nhl-first-goal-games.csv / nhl-first-goal-sequences.csv
  - nhl-first-goal-<date>.md banked lines when new games tally
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
SCAN = "nhl-first-goal"
SEASON_START = "2026-10-01"
SB = ("https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/"
      "scoreboard?dates={}")
SUMMARY = ("https://site.api.espn.com/apis/site/v2/sports/hockey/"
           "nhl/summary?event={}")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"}


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


def clock_to_seconds(display):
    try:
        parts = str(display).split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, TypeError):
        return None


def strength_of(play):
    s = play.get("strength")
    text = ""
    if isinstance(s, dict):
        text = str(s.get("text") or s.get("abbreviation") or "").lower()
    elif s:
        text = str(s).lower()
    if "power" in text or text == "pp":
        return "PP"
    if "short" in text or text == "sh":
        return "SH"
    return "EV"


def scorer_of(play):
    parts = play.get("participants") or []
    if parts:
        name = ((parts[0].get("athlete") or {}).get("displayName"))
        if name:
            return name
    text = play.get("text") or ""
    return text.split(" Goal")[0].strip() or None


def process_game(ev):
    """Verify + build the game record, or None (UNVERIFIED)."""
    try:
        summ = get_json(SUMMARY.format(ev["id"]))
    except Exception:  # noqa: BLE001
        return None
    plays = [p for p in (summ.get("plays") or [])
             if p.get("scoringPlay")
             and ((p.get("period") or {}).get("number") or 0) <= 4]
    # A 0-0 game decided in the shootout has NO regulation/OT goal:
    # verified separately below (no first goal to record).
    pairs = []
    for p in plays:
        a, h = p.get("awayScore"), p.get("homeScore")
        if a is None or h is None:
            return None
        pairs.append((int(a), int(h)))
    final = (ev["away_score"], ev["home_score"])
    shootout = False
    if pairs and pairs[-1] == final:
        pass
    elif not pairs and final in ((1, 0), (0, 1)):
        shootout = True  # 0-0 through OT, decided in the shootout
    elif pairs and pairs[-1][0] == pairs[-1][1]:
        da = final[0] - pairs[-1][0]
        dh = final[1] - pairs[-1][1]
        if (da, dh) in ((1, 0), (0, 1)):
            shootout = True  # tied through OT; decider in the shootout
        else:
            return None
    else:
        return None
    if ev["away_score"] == ev["home_score"]:
        return None
    winner = ev["away"] if ev["away_score"] > ev["home_score"] else ev["home"]
    try:
        dt = datetime.fromisoformat(ev["date_utc"].replace("Z", "+00:00"))
        date_et = dt.astimezone(ET).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_et = ""
    if not plays:
        return {"date": date_et, "away": ev["away"], "home": ev["home"],
                "first": None, "first_side": None, "first_strength": None,
                "first_scorer": None, "first_elapsed_sec": None,
                "winner": winner, "shootout": True,
                "seq": "", "lead_changes": 0, "ties": 0,
                "final": f"{ev['away_score']}-{ev['home_score']}"}
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
    elapsed = None
    period = (first_play.get("period") or {}).get("number")
    clk = clock_to_seconds((first_play.get("clock") or {}).get("displayValue"))
    if period and clk is not None:
        elapsed = int(round((period - 1) * 1200 + (1200 - clk)))
    changes, ties = lead_changes_and_ties(pairs)
    return {"date": date_et, "away": ev["away"], "home": ev["home"],
            "first": first, "first_side": side,
            "first_strength": strength_of(first_play),
            "first_scorer": scorer_of(first_play),
            "first_elapsed_sec": elapsed,
            "winner": winner, "shootout": shootout,
            "seq": ">".join(f"{a}-{h}" for a, h in pairs),
            "lead_changes": changes, "ties": ties,
            "final": f"{ev['away_score']}-{ev['home_score']}"}


def recompute(entries):
    games = sorted(entries.values(),
                   key=lambda g: (g["date"], g["away"]))
    tally = {}
    lc_avg = {}
    for g in games:
        for team in (g["away"], g["home"]):
            t = tally.setdefault(team, {
                "gp": 0, "scored_first": 0,
                "strengths": {"EV": 0, "PP": 0, "SH": 0},
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
                    # first-goal team wins by exactly one (final-score
                    # margin 1 — shootout wins count, they pay both).
                    if margin_for(g, team) == 1:
                        t["won_by_one_when_first"] += 1
            elif g["winner"] == team:
                t["won_when_not_first"] += 1
        if g["first"] and g["first"] in tally:
            t = tally[g["first"]]
            t["scored_first"] += 1
            if g["first_strength"] in t["strengths"]:
                t["strengths"][g["first_strength"]] += 1
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
    scored = [g for g in games if g["first"]]
    n = len(games)
    elapsed = [g["first_elapsed_sec"] for g in scored
               if g.get("first_elapsed_sec") is not None]
    first_by_one = sum(1 for g in scored
                       if g["first"] == g["winner"]
                       and margin_for(g, g["first"]) == 1)
    league = {
        "games": n,
        "games_with_goal": len(scored),
        "shootouts": sum(1 for g in games if g.get("shootout")),
        "home_scored_first": sum(1 for g in scored
                                 if g["first_side"] == "home"),
        "first_team_won": sum(1 for g in scored if g["first"] == g["winner"]),
        "first_team_won_by_one": first_by_one,
        "first_team_won_by_one_pct": round(100 * first_by_one / len(scored), 1)
        if scored else None,
        "strengths": {k: sum(1 for g in scored if g["first_strength"] == k)
                      for k in ("EV", "PP", "SH")},
        "avg_lead_changes": round(
            sum(g["lead_changes"] for g in games) / n, 2) if n else 0.0,
        "median_first_goal_sec": int(statistics.median(elapsed))
        if elapsed else None,
    }
    if scored:
        league["home_scored_first_pct"] = round(
            100 * league["home_scored_first"] / len(scored), 1)
        league["first_team_win_pct"] = round(
            100 * league["first_team_won"] / len(scored), 1)
    return games, tally, lc_avg, league


def write_outputs(state):
    games, tally, lc_avg, league = recompute(state["entries"])
    through = games[-1]["date"] if games else ""
    standing = {"through": through, "games": games, "tally": tally,
                "league": league, "lead_change_team_avgs": lc_avg}
    (Path(DATA_DIR) / "nhl-first-goal-tally.json").write_text(
        json.dumps(standing, indent=1))
    with open(Path(DATA_DIR) / "nhl-first-goal-games.csv", "w",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "away", "home", "first", "first_strength",
                    "first_scorer", "first_elapsed_sec", "winner",
                    "shootout"])
        for g in games:
            w.writerow([g["date"], g["away"], g["home"], g["first"],
                        g["first_strength"], g["first_scorer"],
                        g["first_elapsed_sec"], g["winner"],
                        g["shootout"]])
    with open(Path(DATA_DIR) / "nhl-first-goal-sequences.csv", "w",
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
        print(f"nhl-first-goal: empty state -- season backfill "
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
        lines = [f"## {now_stamp()} -- NHL FIRST GOAL (PC): "
                 f"{len(new_games)} new final(s) tallied; season "
                 f"{len(games)} games through {through}"]
        for g in new_games:
            if g["first"]:
                lines.append(
                    f"- {g['date']}: {g['away']} @ {g['home']} — first "
                    f"goal: {g['first']} ({g['first_strength']}, "
                    f"{g['first_scorer'] or '?'}, "
                    f"{fmt_elapsed(g['first_elapsed_sec'])} in); "
                    f"lead changes {g['lead_changes']}; "
                    f"final {g['final']}"
                    f"{' (SO)' if g['shootout'] else ''}")
            else:
                lines.append(
                    f"- {g['date']}: {g['away']} @ {g['home']} — no "
                    f"goal through OT; shootout final {g['final']}")
        if unverified:
            lines.append(f"- note: {unverified} final(s) UNVERIFIED "
                         f"this run — left out, retried next run.")
        bank(SCAN, lines)
    else:
        save_state(SCAN, state)
    print(f"nhl-first-goal: {len(new_games)} new, {unverified} "
          f"unverified, {len(state['entries'])} total tallied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
