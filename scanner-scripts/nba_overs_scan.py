#!/usr/bin/env python3
"""NBA Overs scan (Civittolo / @linemakersports reel, Oct 26, 2026),
plus the Chet-built UNDER mirror (added 2026-10-27 at Michael's tap).

OVER method, EXACTLY as stated in the reel ("Easiest Way To Predict
an NBA Over!"): to predict an NBA Over, BOTH teams in the game should
be
  1) Top 10 in OFFENSIVE efficiency,
  2) BOTTOM 10 in DEFENSIVE efficiency (ranks 21-30 of 30),
  3) Top 10 in POSSESSIONS per game (pace).
A game qualifies ONLY when BOTH teams meet ALL THREE criteria on
season-to-date stats. No threshold beyond these ranks, no win-rate
claim — the reel states neither, and this script invents neither.

UNDER mirror (NOT Frank's claim — label it as such everywhere):
Chet's inversion, approved by Michael 2026-10-27. A game is an Under
lean ONLY when BOTH teams meet ALL THREE mirrored criteria:
  1) BOTTOM 10 in OFFENSIVE efficiency (ranks 21-30 of 30),
  2) TOP 10 in DEFENSIVE efficiency (ranks 1-10),
  3) BOTTOM 10 in POSSESSIONS per game (ranks 21-30).
Same strict both-teams rule, same early-season noise caveat, same
grading. The Under side starts with no track record.

Ranks use competition ranking ("1224"): rank = 1 + teams strictly
better. Ties share a rank, so a tied 10th can put 11 teams at rank
<= 10; that is shown, not hidden. DRtg bottom-10 = rank >= 21
(rank 1 = best defense, i.e. lowest DRtg).

Input: a snapshot JSON with {"date", "source", "teams": {name:
{"gp", "ortg", "drtg", "pace"}}, "games": [{"away", "home",
"tipoff_et", "total", "total_source", "over_juice", "notes"}]}.
The snapshot is written by the run's fetch step from public sources
only (CraftedNBA standings for ORtg/DRtg/Pace; ScoresAndOdds public
board preferred for totals, consensus board as fallback — never
DarkHorse, never a sign-in). This script never fetches anything
itself and never invents a stat, line, or rank.

Output: updates nba-overs-state.json (days history), appends
nba-overs-<date>.md, prints a JSON summary (qualifiers, near-miss,
full team table) for the delivery step.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR as HF


def comp_ranks(values, higher_better):
    ranks = {}
    for team, val in values.items():
        better = sum(1 for other in values.values()
                     if (other > val if higher_better else other < val))
        ranks[team] = better + 1
    return ranks


def main():
    if len(sys.argv) < 2:
        print("usage: nba_overs_scan.py <snapshot.json>", file=sys.stderr)
        return 2
    snap = json.load(open(sys.argv[1]))
    date = snap["date"]
    teams = snap["teams"]
    ortg = comp_ranks({t: v["ortg"] for t, v in teams.items()}, True)
    drtg = comp_ranks({t: v["drtg"] for t, v in teams.items()}, False)
    pace = comp_ranks({t: v["pace"] for t, v in teams.items()}, True)

    def criteria(team):
        return {
            "ortg_rank": ortg[team],
            "drtg_rank": drtg[team],
            "pace_rank": pace[team],
            "top10_off": ortg[team] <= 10,
            "bottom10_def": drtg[team] >= 21,
            "top10_pace": pace[team] <= 10,
            # Under mirror (Chet's inversion, NOT Frank's claim)
            "bottom10_off": ortg[team] >= 21,
            "top10_def": drtg[team] <= 10,
            "bottom10_pace": pace[team] >= 21,
        }

    def met(team):
        c = criteria(team)
        return (c["top10_off"] + c["bottom10_def"] + c["top10_pace"])

    def under_met(team):
        c = criteria(team)
        return (c["bottom10_off"] + c["top10_def"] + c["bottom10_pace"])

    table = {}
    for team, vals in teams.items():
        c = criteria(team)
        table[team] = {**vals, **c, "criteria_met": met(team),
                       "under_criteria_met": under_met(team)}

    games_out = []
    qualifiers = []
    under_qualifiers = []
    for g in snap["games"]:
        away, home = g["away"], g["home"]
        a_met, h_met = met(away), met(home)
        a_umet, h_umet = under_met(away), under_met(home)
        entry = {
            **g,
            "away_criteria": criteria(away),
            "home_criteria": criteria(home),
            "away_met": a_met,
            "home_met": h_met,
            "combined_met": a_met + h_met,
            "qualifies": (a_met == 3 and h_met == 3),
            "away_under_met": a_umet,
            "home_under_met": h_umet,
            "under_combined_met": a_umet + h_umet,
            "under_qualifies": (a_umet == 3 and h_umet == 3),
        }
        games_out.append(entry)
        if entry["qualifies"]:
            qualifiers.append(entry)
        if entry["under_qualifies"]:
            under_qualifiers.append(entry)

    near_miss = None
    if games_out:
        near_miss = max(games_out, key=lambda e: e["combined_met"])
    under_near_miss = None
    if games_out:
        under_near_miss = max(games_out, key=lambda e: e["under_combined_met"])

    state_path = os.path.join(HF, "nba-overs-state.json")
    try:
        state = json.load(open(state_path))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"meta": {}, "days": {}}
    state["meta"] = {
        "source": snap.get("source", ""),
        "last_scan": date,
        "method": "Over: both teams top-10 ORtg + bottom-10 DRtg + top-10 pace; "
                  "Under (Chet's mirror, NOT Frank's claim): both teams "
                  "bottom-10 ORtg + top-10 DRtg + bottom-10 pace",
    }
    state["days"][date] = {
        "teams": table,
        "games": games_out,
        "qualifiers": [
            {"away": q["away"], "home": q["home"],
             "tipoff_et": q.get("tipoff_et", ""),
             "total": q.get("total"), "total_source": q.get("total_source", ""),
             "over_juice": q.get("over_juice")}
            for q in qualifiers
        ],
        "under_qualifiers": [
            {"away": q["away"], "home": q["home"],
             "tipoff_et": q.get("tipoff_et", ""),
             "total": q.get("total"), "total_source": q.get("total_source", ""),
             "under_juice": q.get("under_juice")}
            for q in under_qualifiers
        ],
    }
    json.dump(state, open(state_path, "w"), indent=1)

    log_path = os.path.join(HF, f"nba-overs-{date}.md")
    lines = [f"# NBA Overs scan — {date}",
             f"Stats source: {snap.get('source', '')}",
             "", "## Team table (season-to-date)",
             "| Team | GP | ORtg | ORtg# | DRtg | DRtg# | Pace | Pace# | Over | Under |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for team in sorted(table, key=lambda t: table[t]["ortg_rank"]):
        r = table[team]
        lines.append(f"| {team} | {r['gp']} | {r['ortg']} | {r['ortg_rank']} | "
                     f"{r['drtg']} | {r['drtg_rank']} | {r['pace']} | "
                     f"{r['pace_rank']} | {r['criteria_met']}/3 | "
                     f"{r['under_criteria_met']}/3 |")
    lines += ["", "## Games (Over)",
              "| Matchup | Tipoff ET | Total | Away | Home | Combined | Qualifies |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for g in games_out:
        lines.append(f"| {g['away']} @ {g['home']} | {g.get('tipoff_et', '')} | "
                     f"{g.get('total', '')} | {g['away_met']}/3 | {g['home_met']}/3 | "
                     f"{g['combined_met']}/6 | {'YES' if g['qualifies'] else 'no'} |")
    lines += ["", "## Games (Under mirror — Chet's inversion, NOT Frank's claim)",
              "| Matchup | Tipoff ET | Total | Away | Home | Combined | Qualifies |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for g in games_out:
        lines.append(f"| {g['away']} @ {g['home']} | {g.get('tipoff_et', '')} | "
                     f"{g.get('total', '')} | {g['away_under_met']}/3 | "
                     f"{g['home_under_met']}/3 | "
                     f"{g['under_combined_met']}/6 | "
                     f"{'YES' if g['under_qualifies'] else 'no'} |")
    lines.append("")
    with open(log_path, "w") as fh:
        fh.write("\n".join(lines))

    summary = {
        "date": date,
        "qualifiers": state["days"][date]["qualifiers"],
        "under_qualifiers": state["days"][date]["under_qualifiers"],
        "near_miss": ({k: near_miss[k] for k in
                       ("away", "home", "tipoff_et", "total", "away_met",
                        "home_met", "combined_met")} if near_miss else None),
        "under_near_miss": ({k: under_near_miss[k] for k in
                             ("away", "home", "tipoff_et", "total",
                              "away_under_met", "home_under_met",
                              "under_combined_met")} if under_near_miss else None),
        "games": len(games_out),
    }
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
