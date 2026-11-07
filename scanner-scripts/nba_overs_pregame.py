#!/usr/bin/env python3
"""NBA Overs & Unders PRE-GAME REFRESH (PC port, 2026-11-06) — twin
of the Muse-side 30-minute refresh; companion to the PC daily
08:14 scan (nba_overs_daily.py).

HONEST SCOPE (same as the Muse twin): the three-filter ranks are
SEASON-TO-DATE team stats — they do not change with a night's
lineups. What this refresh updates is the MARKET total line and
juice, which is where who's in/out shows up. A pre-game run almost
never creates a brand-new qualifier; its job is to keep a delivered
qualifier's recorded line honest through tipoff, so grading uses a
near-tipoff number instead of the morning number.

GATE: acts only 12:00-23:30 ET and only when today's snapshot
exists (data/nba-overs-snapshot-<today>.json, written by the 08:10
fetch; missing snapshot = the morning scan owns the day -> exit).
Only games tipping within the next 90 minutes (and not yet tipped)
are refreshed, from the ScoresAndOdds public NBA board (so_bball)
— PUBLIC SOURCE ONLY, no DarkHorse, no sign-ins, never an invented
line or juice.

Delivery (alert-only, banked to data/nba-overs-pregame-<date>.md):
only when a qualifier already delivered today has its total move
>= 1.0 point or its juice change since the last messaged value —
the banked line leads with the FRANK CRITERIA HIT header like the
Muse twin. Refreshed games are recorded in
data/nba-overs-pregame-<date>.json (refreshed + messaged maps) so
a later run can tell a new move from one already surfaced. The PC
board is rebuilt only when a qualifier changed. Scheduler:
interval 30 min (self-gating, like the Muse twin).
"""
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from config import DATA_DIR  # noqa: E402
from odds_common import bank, norm_team, now_stamp  # noqa: E402
from so_bball import BoardUnreadable, current_total, load_odds_board  # noqa: E402

ET = ZoneInfo("America/New_York")
SCAN = "nba-overs-pregame"
TIP_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)", re.I)


def parse_tipoff(tip_str, today):
    m = TIP_RE.search(tip_str or "")
    if not m:
        return None
    hh, mm, ap = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if ap == "PM" and hh != 12:
        hh += 12
    if ap == "AM" and hh == 12:
        hh = 0
    return datetime(today.year, today.month, today.day, hh, mm,
                    tzinfo=ET)


def nick(name):
    parts = norm_team(name).split()
    return parts[-1] if parts else ""


def main():
    window_min = 90
    if "--window" in sys.argv:
        try:
            window_min = int(sys.argv[sys.argv.index("--window") + 1])
        except (ValueError, IndexError):
            pass
    now = datetime.now(ET)
    if not (now.hour >= 12 and (now.hour, now.minute) < (23, 30)):
        print("nba-overs-pregame: outside 12:00-23:30 ET gate -- exit.")
        return 0
    date = now.strftime("%Y-%m-%d")
    snap_path = Path(DATA_DIR) / f"nba-overs-snapshot-{date}.json"
    state_path = Path(DATA_DIR) / "nba-overs-state.json"
    if not snap_path.exists():
        print("nba-overs-pregame: no snapshot for today -- exit "
              "(the morning scan owns the first snapshot).")
        return 0
    if not state_path.exists():
        print("nba-overs-pregame: no nba-overs state yet -- exit.")
        return 0
    snap = json.loads(snap_path.read_text())
    state = json.loads(state_path.read_text())
    day = state.get("days", {}).get(date)
    if day is None:
        print("nba-overs-pregame: state has no day entry for today "
              "-- exit.")
        return 0

    qualifiers = {}
    for g in day.get("games", []):
        key = (norm_team(g["away"]), norm_team(g["home"]))
        if g.get("qualifies"):
            qualifiers[key] = "Over"
        elif g.get("under_qualifies"):
            qualifiers[key] = "Under"

    targets = []
    for g in snap.get("games", []):
        tip = parse_tipoff(g.get("tipoff_et", ""), now.date())
        if tip is None:
            continue
        mins = (tip - now).total_seconds() / 60.0
        if 0 < mins <= window_min:
            targets.append((g, tip))
    if not targets:
        print(f"nba-overs-pregame: no games tipping within "
              f"{window_min} min -- exit.")
        return 0

    try:
        board = load_odds_board("NBA")
    except BoardUnreadable as exc:
        print(f"nba-overs-pregame: {exc} -- silent retry next run.")
        return 0
    board_idx = {}
    for bg in board:
        board_idx[(nick(bg["away"]), nick(bg["home"]))] = bg
        board_idx[(nick(bg["home"]), nick(bg["away"]))] = bg

    rec_path = Path(DATA_DIR) / f"nba-overs-pregame-{date}.json"
    try:
        record = json.loads(rec_path.read_text())
    except (OSError, json.JSONDecodeError):
        record = {"refreshed": {}, "messaged": {}}
    record.setdefault("refreshed", {})
    record.setdefault("messaged", {})

    day_games = {(norm_team(g["away"]), norm_team(g["home"])): g
                 for g in day.get("games", [])}
    deliveries = []
    refreshed = 0
    for g, tip in targets:
        bg = board_idx.get((nick(g["away"]), nick(g["home"])))
        if bg is None:
            continue
        total, over_j, under_j = current_total(bg)
        if total is None:
            continue
        key = f"{g['away']} @ {g['home']}"
        old_total = g.get("total")
        old_oj, old_uj = g.get("over_juice"), g.get("under_juice")
        g["total"], g["over_juice"], g["under_juice"] = total, over_j, under_j
        g["total_source"] = "ScoresAndOdds (PC pregame refresh)"
        dg = day_games.get((norm_team(g["away"]), norm_team(g["home"])))
        if dg is not None:
            dg["total"], dg["over_juice"] = total, over_j
            dg["under_juice"] = under_j
            dg["total_source"] = "ScoresAndOdds (PC pregame refresh)"
        for coll in ("qualifiers", "under_qualifiers"):
            for q in day.get(coll, []):
                if (norm_team(q["away"]), norm_team(q["home"])) == \
                        (norm_team(g["away"]), norm_team(g["home"])):
                    q["total"] = total
                    if coll == "qualifiers":
                        q["over_juice"] = over_j
                    else:
                        q["under_juice"] = under_j
        record["refreshed"][key] = {
            "total": total, "over_juice": over_j,
            "under_juice": under_j, "time": now_stamp()}
        refreshed += 1
        side = qualifiers.get((norm_team(g["away"]),
                               norm_team(g["home"])))
        if side is None:
            continue
        ref = record["messaged"].get(key)
        ref_total = ref["total"] if ref else old_total
        ref_oj = ref["over_juice"] if ref else old_oj
        ref_uj = ref["under_juice"] if ref else old_uj
        moved = (ref_total is not None
                 and abs(total - ref_total) >= 1.0)
        juice_changed = (over_j != ref_oj or under_j != ref_uj)
        if moved or juice_changed:
            side_note = side if side == "Over" else \
                "Under -- Chet's mirror, NOT Frank's system"
            deliveries.append(
                f"- FRANK CRITERIA HIT — NBA Overs & Unders "
                f"(PC calibration): {key} ({g.get('tipoff_et', '')}) "
                f"{side_note} — total {ref_total} -> {total}, "
                f"over {over_j}, under {under_j} (ScoresAndOdds)")
            record["messaged"][key] = {
                "total": total, "over_juice": over_j,
                "under_juice": under_j, "time": now_stamp()}

    if refreshed:
        snap_path.write_text(json.dumps(snap, indent=1))
        state_path.write_text(json.dumps(state, indent=1))
        rec_path.write_text(json.dumps(record, indent=1))
    if deliveries:
        bank(SCAN, [f"## {now_stamp()} -- nba-overs pregame refresh "
                    f"(PC calibration): {len(deliveries)} changed "
                    f"qualifier(s)"] + deliveries)
        try:
            subprocess.run([sys.executable,
                            str(HERE / "build_nba_overs_board.py")],
                           cwd=HERE, timeout=300,
                           capture_output=True, text=True)
        except Exception as exc:  # noqa: BLE001 - board is a nicety
            print(f"nba-overs-pregame: board rebuild failed ({exc}).")
    print(f"nba-overs-pregame: {refreshed} game(s) refreshed, "
          f"{len(deliveries)} deliverable change(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
