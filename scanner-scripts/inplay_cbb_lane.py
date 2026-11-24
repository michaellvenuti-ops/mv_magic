#!/usr/bin/env python3
"""inplay_cbb_lane.py -- fast lane for the in-play CBB watch (added
2026-11-24, same pattern as upset_watch_lane.py).

Why this exists: the serial scheduler's queue backs up behind slow
jobs, so a nominal 2-minute cadence can slip -- and a halftime flag
has only a ~15-minute window. This wrapper runs every 2 minutes
under its OWN Windows scheduled task (pc-inplay-cbb-lane),
independent of scheduler.py's serial queue:
  1. Snapshot the alerted set (game id -> alerted_at) from
     data/inplay-cbb-watch-state.json.
  2. Run inplay_cbb_watch.py unchanged -- detection rules,
     thresholds and banking all live there; this wrapper changes
     NONE of them.
  3. If (and only if) that run stamped a NEW alert, push ONLY
     pc-data/inplay-cbb-watch-state.json to GitHub immediately via
     github_push.put_file (one retry, then give up quietly -- the
     regular 10-minute sync remains the backstop). No new alert =
     no push. (The watcher itself already pushes the dated delivery
     .md + the ntfy phone alert inline, like todd_discord_watch.py.)

Every run appends a line to data/inplay-cbb-lane.log (start time,
runtime, new-alert count, push result) -- that log is the cadence
evidence.

Token: GH_PC_SCANS_TOKEN from the environment only (via config.py /
github_push.py). Never printed, never logged.

--selftest: pushes a harmless scratch file
(pc-data/inplay-cbb-lane-selftest.txt) through the exact same
put_file path and reports the result; never touches the watch state
and never fabricates an alert.
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DATA_DIR  # noqa: E402
from github_push import put_file  # noqa: E402

HERE = Path(__file__).resolve().parent
STATE_PATH = DATA_DIR / "inplay-cbb-watch-state.json"
REPO_STATE = "pc-data/inplay-cbb-watch-state.json"
LANE_LOG = DATA_DIR / "inplay-cbb-lane.log"
WATCH_TIMEOUT_S = 110  # inplay_cbb_watch normally finishes in 2-10s


def now():
    """PC local time == ET (the PC lives in Cherry Hill, Eastern)."""
    return datetime.now()


def lane_log(msg: str) -> None:
    line = f"[{now():%Y-%m-%d %H:%M:%S} ET] {msg}"
    print(line, flush=True)
    try:
        LANE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with LANE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def alerted_map() -> dict:
    """game id -> alerted_at for every record currently alerted."""
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for gid, rec in (state.get("games") or {}).items():
        if isinstance(rec, dict) and rec.get("alerted"):
            out[gid] = rec.get("alerted_at")
    return out


def push_state(matchups: list) -> bool:
    """Push ONLY the in-play CBB watch state file; one retry, then give up."""
    msg = "in-play CBB fast lane: " + ("; ".join(matchups) or "new alert")
    for attempt in (1, 2):
        ok, detail = put_file(REPO_STATE, STATE_PATH, msg)
        if ok:
            lane_log(f"fast push OK (attempt {attempt}, commit {detail}) "
                     f"for {len(matchups)} new alert(s)")
            return True
        lane_log(f"fast push FAILED (attempt {attempt}): {detail}")
        if attempt == 1:
            time.sleep(3)
    lane_log("fast push gave up -- the regular 10-min sync is the backstop")
    return False


def main() -> int:
    if "--selftest" in sys.argv:
        scratch = DATA_DIR / "inplay-cbb-lane-selftest.txt"
        scratch.write_text(
            "inplay-cbb-lane selftest "
            f"{now():%Y-%m-%d %H:%M:%S} ET -- push-path proof, not an alert.\n",
            encoding="utf-8")
        ok, detail = put_file("pc-data/inplay-cbb-lane-selftest.txt", scratch,
                              "in-play CBB fast lane selftest")
        lane_log(f"SELFTEST push {'OK commit ' + detail if ok else 'FAILED: ' + detail}")
        return 0 if ok else 1

    started = time.time()
    before = alerted_map()
    try:
        proc = subprocess.run(
            [sys.executable, "inplay_cbb_watch.py"], cwd=HERE,
            timeout=WATCH_TIMEOUT_S, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
        out = (proc.stdout or "").strip()
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        lane_log(f"lane run: inplay_cbb_watch TIMEOUT after {WATCH_TIMEOUT_S}s -- "
                 f"no state diff taken, no push")
        return 0
    except OSError as exc:
        lane_log(f"lane run: inplay_cbb_watch could not start ({exc})")
        return 0
    for line in out.splitlines():
        print(line, flush=True)
    after = alerted_map()
    new_ids = [gid for gid, ts in after.items() if before.get(gid) != ts]
    matchups = []
    if new_ids:
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            games = state.get("games") or {}
            matchups = [str((games.get(gid) or {}).get("matchup") or gid)
                        for gid in new_ids]
        except (OSError, json.JSONDecodeError):
            matchups = list(new_ids)
    lane_log(f"lane run: inplay_cbb_watch exit {rc} in {time.time()-started:.0f}s -- "
             f"{len(new_ids)} new alert(s)"
             + (f" ({'; '.join(matchups)})" if matchups else ""))
    if new_ids:
        push_state(matchups)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
