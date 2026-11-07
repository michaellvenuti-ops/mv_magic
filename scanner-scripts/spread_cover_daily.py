#!/usr/bin/env python3
"""Spread Cover DAILY RUNNER (PC build, 2026-11-06).

Wires the two PC halves together for the scheduler:
  1. fetch_spread_cover_snapshot.build_snapshot() — public
     ScoresAndOdds odds boards + consensus splits -> today's
     snapshot in data/.
  2. spread_cover_scan.py <snapshot> — the compute half ported
     2026-11-05 (stores openers, evaluates Chet's flag rule:
     spread moved >= 1.0 toward a side vs its stored opener AND
     money% - bet% >= 10 on that side; thresholds are Chet's
     display choices — Frank states none and makes no win-rate
     claim), updates data/spread-cover-state.json and writes
     data/spread-cover-<date>.md.
  3. This runner appends the delivery summary to that .md: one
     banked line per NEW or MOVED flag, each leading with the
     FRANK CRITERIA HIT header for its sport (PC calibration),
     so the pc-data watcher can surface them during testing.
     Baselines and non-qualifying games stay silent.

Scheduler: daily 08:14 ET. The Muse-side twin stays ON during
testing; nothing here touches it. Exit codes: fetch failure
(both boards unreadable) banks a note and exits 0 (public-scan
convention); a scan failure propagates (fail loud).
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from config import DATA_DIR  # noqa: E402
from fetch_spread_cover_snapshot import build_snapshot  # noqa: E402
from odds_common import bank, now_stamp  # noqa: E402

ET = ZoneInfo("America/New_York")
SCAN = "spread-cover"


def flag_line(flag):
    sport = flag["sport"]
    ats = ""
    if sport == "NBA" and flag.get("ats_last10"):
        ats = f", last-10 ATS {flag['ats_last10']}"
        if flag.get("three_signal"):
            ats += " [3-signal]"
    return (f"- FRANK CRITERIA HIT — Spread Cover ({sport}) "
            f"(PC calibration): {flag['away']} @ {flag['home']} — "
            f"{flag['team']} {flag['spread']:+g} "
            f"({flag.get('spread_juice')}), opener (home) "
            f"{flag['open_home_spread']:+g} -> current "
            f"{flag['current_home_spread']:+g}, move toward side "
            f"{flag['move_toward_side']} pt, bet% {flag['bet_pct']} "
            f"vs money% {flag['money_pct']} (divergence "
            f"{flag['divergence']}){ats}. Thresholds are Chet's "
            f"display choices; Frank states none and makes no "
            f"win-rate claim.")


def main():
    date_str = datetime.now(ET).strftime("%Y-%m-%d")
    snap, errors, _splits = build_snapshot(date_str)
    if snap is None:
        bank(SCAN, [f"## {now_stamp()} -- spread-cover (PC): fetch "
                    f"failed, both boards unreadable: {errors}"])
        print(f"spread-cover: fetch failed -- {errors}")
        return 0
    snap_path = Path(DATA_DIR) / f"spread-cover-snapshot-{date_str}.json"
    snap_path.write_text(json.dumps(snap, indent=1))
    proc = subprocess.run(
        [sys.executable, str(HERE / "spread_cover_scan.py"),
         str(snap_path)], cwd=HERE, capture_output=True, text=True,
        timeout=600)
    sys.stdout.write(proc.stdout or "")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or "")
        return proc.returncode
    summary = None
    try:
        summary = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        start = (proc.stdout or "").find("{")
        if start >= 0:
            try:
                summary = json.loads(proc.stdout[start:])
            except json.JSONDecodeError:
                summary = None
    if summary is not None:
        lines = []
        for flag in summary.get("new_flags", []):
            lines.append(flag_line(flag))
        for flag in summary.get("moved_flags", []):
            lines.append(flag_line(flag) + " [MOVED further]")
        if lines:
            bank(SCAN, [f"## {now_stamp()} -- spread-cover (PC): "
                        f"{len(lines)} deliverable flag(s)"]
                 + lines)
        print(f"spread-cover: games {summary.get('games')}, "
              f"new {len(summary.get('new_flags', []))}, "
              f"moved {len(summary.get('moved_flags', []))}, "
              f"baselines {len(summary.get('baselines_stored', []))}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
