#!/usr/bin/env python3
"""scheduler.py — runs the scans on the same cadence Chet uses, while it stays open.

    python3 scheduler.py            # run forever (Ctrl+C to stop)
    python3 scheduler.py --once     # fire everything that is due right now, then exit
    python3 scheduler.py --list     # show the schedule table and exit

Design notes:
- One long-running process; leave the window open and the computer awake.
- Every scan still runs as its own subprocess (30-min cap, failure-isolated).
- Intervals mirror Chet's live cadence (Eastern times for the daily ones).
- Results sync back to GitHub (pc-data/) at most once per 10 minutes, and
  only when at least one scan ran since the last sync. Needs
  GH_PC_SCANS_TOKEN in the environment; without it, syncing is skipped.
- upset_watch is NOT in this queue: since 2026-11-06 it runs in its
  own lane (Windows task pc-upset-watch-lane -> upset_watch_lane.py,
  every 2 min) so this serial queue's backlog can never delay it.
  Everything here is 5-minute or slower.
- State lives in data/scheduler-state.json so a restart does not re-fire
  scans that just ran.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")
STATE = HERE / "data" / "scheduler-state.json"
LOG = HERE / "data" / "scheduler.log"
PY = sys.executable or "python3"
CAP_SECONDS = 30 * 60

# (script, args, kind, spec)
#   kind "interval": spec = minutes between runs
#   kind "daily":    spec = "HH:MM" Eastern, fires once per day at/after that time
#   kind "weekly":   spec = ("Mon".."Sun", "HH:MM") Eastern
SCHEDULE = [
    # upset_watch.py: REMOVED 2026-11-06 — it runs in its own 2-minute
    # lane now (pc-upset-watch-lane task + upset_watch_lane.py); keeping
    # it here too would run it twice and race the shared state file.
    ("odds_recorder.py", [], "interval", 5),
    # hourly family
    ("dh_arbitrage.py", [], "interval", 60),
    ("dh_kalshi_poly_arb.py", [], "interval", 60),
    ("dh_low_holds.py", [], "interval", 60),
    ("dh_game_middles.py", [], "interval", 30),
    ("dh_key_number_middles.py", [], "interval", 30),
    ("dh_basketball_key_middles.py", [], "interval", 30),
    ("hung_number.py", [], "interval", 60),
    ("rlm.py", [], "interval", 60),
    ("leader_follower.py", [], "interval", 60),
    ("steam_side.py", [], "interval", 30),
    # every 2 hours
    ("dh_prop_middles.py", [], "interval", 30),
    ("dh_sog_middles.py", [], "interval", 30),
    ("dh_team_total_middles.py", [], "interval", 30),
    ("nfl_first_score.py", [], "interval", 30),
    # daily, Eastern
    ("fetch_nba_snapshot.py", [], "daily", "08:10"),
    ("dh_sleeping_book_sweep.py", [], "daily", "08:30"),
    # nba_overs_scan.py needs the dated snapshot path as an argument;
    # the wrapper computes it (bare entry failed daily, exit 2).
    ("nba_overs_daily.py", [], "daily", "08:14"),
    ("nba_overs_pregame.py", [], "interval", 30),
    ("nhl_totals_scan.py", [], "daily", "08:14"),
    ("total_projectors_scan.py", [], "daily", "08:14"),
    ("spread_cover_daily.py", [], "daily", "08:14"),
    # the grade scripts need --date/--finals; the *_grade_run wrappers
    # compute yesterday (ET) and fetch finals from ESPN (bare entries
    # failed daily, exit 2 — fixed 2026-11-05).
    ("nba_overs_grade_run.py", [], "daily", "09:14"),
    ("nhl_totals_grade_run.py", [], "daily", "09:14"),
    ("total_projectors_grade_run.py", [], "daily", "09:14"),
    ("futures_board.py", [], "daily", "08:44"),
    # Yahoo Mail brief extractor (added 2026-11-06; PC twin of the
    # Muse-side daily Yahoo brief — the Muse-side cron STAYS ON
    # until a signed-in PC run is proven, per the flip rule).
    ("yahoo_brief.py", [], "daily", "08:45"),
    # Podcast transcription lane (added 2026-11-06): RSS discovery +
    # detached local-Whisper worker for episodes with no Spotify
    # transcript. --work spawns the worker detached and returns at
    # once (episodes take ~2h; the scheduler caps jobs at 30 min).
    # Overnight batch first (limit 5), then a daytime single lane
    # (limit 1) so one transcript can land before the 17:00 ET scan.
    ("podcast_transcribe.py", ["--discover"], "daily", "01:00"),
    ("podcast_transcribe.py", ["--work", "--limit", "5"], "daily", "01:05"),
    ("podcast_transcribe.py", ["--discover"], "daily", "12:30"),
    ("podcast_transcribe.py", ["--work", "--limit", "1"], "daily", "12:35"),
    # Half Percent Club one-stock pick (added 2026-11-06; weekdays
    # only — the script itself no-ops Sat/Sun; grades prior pick first).
    ("half_percent.py", [], "daily", "09:00"),
    # Half Percent Club v3 layer 1 (added 2026-11-06): 9:28 AM ET
    # pre-bell re-check — cancels today's pick before the bell if
    # the gap left the +3%..+12% band or price broke the plan stop.
    ("half_percent.py", ["--recheck"], "daily", "09:28"),
    ("costco_gold.py", [], "daily", "11:04"),  # 08:04 PT, ahead of the 08:44 PT brief
    ("futures_pairs.py", [], "daily", "09:24"),
    ("promo_watch.py", [], "daily", "09:54"),
    ("totals_scan.py", [], "daily", "10:44"),
    ("correlated_parlay.py", [], "daily", "10:44"),
    ("dh_derivative_ev.py", [], "daily", "10:44"),
    ("public_fade.py", [], "daily", "11:44"),
    ("dh_positive_ev.py", [], "daily", "11:44"),
    # weekly, Eastern
    ("qb_props.py", [], "weekly", ("Thu", "10:44")),
    ("rb_rushing.py", [], "weekly", ("Thu", "11:24")),
    ("score_pred.py", [], "weekly", ("Sun", "08:44")),
    ("first_half.py", [], "weekly", ("Sun", "09:44")),
    ("wong_teasers.py", [], "weekly", ("Sat", "11:44")),
]
# totals grading is handled inside totals_scan's companion run when finals exist;
# kicker.py data is produced by qb_props.py's weekly run.


def log(msg: str) -> None:
    line = f"[{datetime.now(ET):%Y-%m-%d %H:%M:%S} ET] {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def key_of(script: str, args: list) -> str:
    return script + (" " + " ".join(args) if args else "")


def due(entry, state: dict, now: datetime) -> bool:
    script, args, kind, spec = entry
    last = state.get(key_of(script, args))
    last_dt = datetime.fromisoformat(last) if last else None
    if kind == "interval":
        return last_dt is None or (now - last_dt).total_seconds() >= spec * 60 - 5
    if kind == "daily":
        hh, mm = (int(x) for x in spec.split(":"))
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return now >= target and (last_dt is None or last_dt.date() < now.date() or last_dt < target)
    if kind == "weekly":
        day, hm = spec
        if now.strftime("%a") != day:
            return False
        hh, mm = (int(x) for x in hm.split(":"))
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return now >= target and (last_dt is None or (now - last_dt).total_seconds() > 6 * 24 * 3600)
    return False


def run_one(script: str, args: list) -> bool:
    started = time.time()
    try:
        proc = subprocess.run(
            [PY, script, *args], cwd=HERE, timeout=CAP_SECONDS,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        ok = proc.returncode == 0
        tail = (proc.stdout or "").strip().splitlines()
        log(f"{script}: {'OK' if ok else 'FAILED (exit ' + str(proc.returncode) + ')'} in {time.time()-started:.0f}s"
            + (f" — {tail[-1][:160]}" if tail else ""))
        return ok
    except subprocess.TimeoutExpired:
        log(f"{script}: TIMEOUT after {CAP_SECONDS//60} min — skipped, others continue")
        return False
    except OSError as e:
        log(f"{script}: could not start ({e})")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the scans on Chet's cadence while this stays open.")
    ap.add_argument("--once", action="store_true", help="fire everything currently due, then exit")
    ap.add_argument("--list", action="store_true", help="print the schedule table and exit")
    args = ap.parse_args()

    if args.list:
        for script, sargs, kind, spec in SCHEDULE:
            print(f"{script:<28} {kind:<9} {spec} {' '.join(sargs)}")
        return 0

    state = load_state()
    last_sync = 0.0
    dirty = False
    log("scheduler started — mirroring Chet's cadence. Ctrl+C to stop.")
    try:
        while True:
            now = datetime.now(ET)
            for entry in SCHEDULE:
                if due(entry, state, now):
                    script, sargs, _kind, _spec = entry
                    run_one(script, sargs)
                    state[key_of(script, sargs)] = datetime.now(ET).isoformat()
                    save_state(state)
                    dirty = True
            if dirty and time.time() - last_sync >= 10 * 60:
                if os.environ.get("GH_PC_SCANS_TOKEN"):
                    if run_one("sync_to_github.py", []):
                        last_sync = time.time()
                        dirty = False
                        # Phone ping (no-op unless a notify
                        # channel is configured — see notify.py)
                        run_one("notify.py", ["--digest"])
                else:
                    last_sync = time.time()  # don't nag every loop
                    log("sync skipped — GH_PC_SCANS_TOKEN not set (results stay in data/ until you send them)")
            if args.once:
                break
            time.sleep(30)
    except KeyboardInterrupt:
        log("scheduler stopped by user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
