#!/usr/bin/env python3
"""upset_watch_lane.py — fast lane for the upset watch (added 2026-11-06).

Why this exists: the serial scheduler's queue backs up behind ~4-minute
sync runs and slow DarkHorse jobs, so the nominal 2-minute upset_watch
cadence actually ran 5-48 minutes apart on 2026-11-06 (worst gap 37 min
before the ETSU at NC State trigger). A triggered alert then rode the
10-minute GitHub sync + 2-minute watcher poll and reached chat ~10
minutes after the scan read it.

This wrapper runs every 2 minutes under its OWN Windows scheduled task
(pc-upset-watch-lane), independent of scheduler.py's serial queue:
  1. Snapshot the alerted set (game id -> alerted_at) from
     data/upset-watch-state.json.
  2. Run upset_watch.py unchanged — detection rules, thresholds, stamps,
     sanity hold and re-verification all live there; this wrapper
     changes NONE of them.
  3. If (and only if) that run stamped a NEW alert, push ONLY
     pc-data/upset-watch-state.json to GitHub immediately via
     github_push.put_file (one retry, then give up quietly — the
     regular 10-minute sync remains the backstop). No new alert = no push.

Every run appends a line to data/upset-lane.log (start time, runtime,
new-alert count, push result) — that log is the cadence evidence.

Token: GH_PC_SCANS_TOKEN from the environment only (via config.py /
github_push.py). Never printed, never logged.

--selftest: pushes a harmless scratch file (pc-data/upset-lane-selftest.txt)
through the exact same put_file path and reports the result; never
touches the upset-watch state and never fabricates an alert.
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
STATE_PATH = DATA_DIR / "upset-watch-state.json"
REPO_STATE = "pc-data/upset-watch-state.json"
LANE_LOG = DATA_DIR / "upset-lane.log"
WATCH_TIMEOUT_S = 110  # upset_watch normally finishes in 1-5s


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
    """Push ONLY the upset-watch state file; one retry, then give up."""
    msg = "upset-watch fast lane: " + ("; ".join(matchups) or "new alert")
    for attempt in (1, 2):
        ok, detail = put_file(REPO_STATE, STATE_PATH, msg)
        if ok:
            lane_log(f"fast push OK (attempt {attempt}, commit {detail}) "
                     f"for {len(matchups)} new alert(s)")
            return True
        lane_log(f"fast push FAILED (attempt {attempt}): {detail}")
        if attempt == 1:
            time.sleep(3)
    lane_log("fast push gave up — the regular 10-min sync is the backstop")
    return False


def main() -> int:
    if "--selftest" in sys.argv:
        scratch = DATA_DIR / "upset-lane-selftest.txt"
        scratch.write_text(
            "upset-lane selftest "
            f"{now():%Y-%m-%d %H:%M:%S} ET — push-path proof, not an alert.\n",
            encoding="utf-8")
        ok, detail = put_file("pc-data/upset-lane-selftest.txt", scratch,
                              "upset-watch fast lane selftest")
        lane_log(f"SELFTEST push {'OK commit ' + detail if ok else 'FAILED: ' + detail}")
        return 0 if ok else 1

    started = time.time()
    before = alerted_map()
    try:
        proc = subprocess.run(
            [sys.executable, "upset_watch.py"], cwd=HERE,
            timeout=WATCH_TIMEOUT_S, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
        out = (proc.stdout or "").strip()
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        lane_log(f"lane run: upset_watch TIMEOUT after {WATCH_TIMEOUT_S}s — "
                 f"no state diff taken, no push")
        return 0
    except OSError as exc:
        lane_log(f"lane run: upset_watch could not start ({exc})")
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
    lane_log(f"lane run: upset_watch exit {rc} in {time.time()-started:.0f}s — "
             f"{len(new_ids)} new alert(s)"
             + (f" ({'; '.join(matchups)})" if matchups else ""))
    if new_ids:
        push_state(matchups)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
