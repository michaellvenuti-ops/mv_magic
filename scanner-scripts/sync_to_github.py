#!/usr/bin/env python3
"""Sync PC scan results back to Chet via GitHub (DATA RETURN, Route 1).

Pushes everything in this package's data/ folder (state JSONs, banked
<scan>-<date>.md lists, CSVs, snapshots) to the mv_magic repo under a
NEW folder: pc-data/. ADD-ONLY guarantee: this script only ever
creates/updates paths that start with "pc-data/" — it cannot touch
any existing site file (the repo is add-only by Michael's rule).

Boards keep the v2 calibration convention: any board HTML in boards/
is pushed at repo ROOT under its calibration name (*-pc.html), exactly
like v2's build_nba_overs_board.py flow.

Token: GH_PC_SCANS_TOKEN environment variable ONLY (see config.py).
Never hardcoded, never printed.

Usage: python sync_to_github.py [--dry-run]
Prints a summary of what was pushed.
"""
import argparse
import sys
from pathlib import Path

from config import BOARDS_DIR, DATA_DIR
from github_push import put_file

PREFIX = "pc-data/"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be pushed, push nothing")
    args = ap.parse_args()
    plan = []
    for path in sorted(Path(DATA_DIR).rglob("*")):
        if path.is_file() and "__pycache__" not in str(path):
            rel = path.relative_to(DATA_DIR).as_posix()
            plan.append((f"{PREFIX}{rel}", path))
    for path in sorted(Path(BOARDS_DIR).glob("*.html")):
        plan.append((path.name, path))  # repo root, calibration name
    pushed, failed = [], []
    for repo_path, local in plan:
        assert repo_path.startswith(PREFIX) or repo_path.endswith("-pc.html"), \
            f"refusing non-pc-data path: {repo_path}"
        if args.dry_run:
            pushed.append(f"(dry-run) {repo_path}")
            continue
        ok, detail = put_file(repo_path, local,
                              f"pc-scans sync: {repo_path}")
        (pushed if ok else failed).append(
            f"{repo_path} ({detail})" if not ok else repo_path)
    print(f"sync_to_github: {len(pushed)} pushed, {len(failed)} failed "
          f"of {len(plan)} files.")
    for p in pushed:
        print(f"  ok: {p}")
    for f in failed:
        print(f"  FAILED: {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
