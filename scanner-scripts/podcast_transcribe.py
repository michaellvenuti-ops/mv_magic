#!/usr/bin/env python3
"""podcast_transcribe.py - PC podcast transcription lane (Nov 6 2026).

WHY THIS EXISTS: the Muse-side podcast picks scan reads Spotify
transcripts. Episodes Spotify has no transcript for yield nothing
(Nov 5 scan: 10 episodes, 0 picks). This lane is the fallback: it
discovers the day's episodes for the 6 tracked shows from their public
RSS feeds, downloads the audio, and transcribes locally with the
already-proven transcribe.py (faster-whisper base, ~0.37x realtime).
Transcripts land in data/transcripts/ and ride the normal GitHub sync
back to pc-data/transcripts/, where the Muse side can read them.

This lane changes NO scan behavior. It only produces transcripts.

MODES
  --discover [--days N]
      Fetch the feeds, enqueue episodes for the tracked shows whose
      pubDate (ET) falls in the last N days (default 2: yesterday +
      today, so the 01:00 run covers the day that just ended and the
      12:30 run covers today-so-far). Idempotent: state dedupe on
      show|date|title. Prints a SUMMARY line. Episodes predating
      the lane can be parked with status "skipped" (never processed).
  --work [--limit N]
      Start a DETACHED worker (unless one is already running) and
      return immediately. The scheduler caps every job at 30 minutes
      and one episode takes ~2h, so the scheduler step itself must
      never transcribe inline. Default limit: no limit (the overnight
      entry passes --limit 5; the daytime entry passes --limit 1).
  --worker [--limit N]
      The detached worker: processes the queue sequentially, oldest
      first. Per episode: download audio (to podcast-audio/, OUTSIDE
      data/ so the sync never pushes 43MB mp3s), run transcribe.py at
      idle priority, mark done/failed, delete the audio. One bad
      episode never blocks the queue. Holds data/podcast-worker.lock
      with a heartbeat in the state file.
  --status
      Print queue counts by status.

SCHEDULER WIRING (Eastern): discover 01:00 + work 01:05 (overnight
batch, limit 5) and discover 12:30 + work 12:35 --limit 1 (daytime
single lane, lands before the ~17:00 ET Muse scan).
"""
import argparse
import json
import re
import subprocess
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as XMLT
from datetime import datetime, timedelta
from datetime import timezone as _tz
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import BASE_DIR, DATA_DIR

ET = ZoneInfo("America/New_York")
STATE_PATH = DATA_DIR / "podcast-transcribe-state.json"
LOCK_PATH = DATA_DIR / "podcast-worker.lock"
AUDIO_DIR = BASE_DIR / "podcast-audio"          # outside data/: never synced
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
INDEX_PATH = TRANSCRIPTS_DIR / "index.json"
UA = {"User-Agent": "Mozilla/5.0 (pc-scans podcast lane)"}

# feed url -> how to recognize tracked shows in its items.
# match = (title prefix, show name). Items matching no prefix are skipped.
FEEDS = [
    ("https://feeds.simplecast.com/iXBZFZCf", [   # VSiN Best Bets
        ("VSiN By The Books", "VSiN By The Books"),
        ("Follow the Money", "Follow the Money"),
        ("Prop Points", "Prop Points"),
        ("Circa Friday Football Invitational", "Circa Friday Football Invitational"),
    ]),
    ("https://feeds.simplecast.com/dQdfx569", [   # A Numbers Game
        ("A Numbers Game", "A Numbers Game"),
    ]),
    ("https://feeds.simplecast.com/bPyImcOV", [   # Prop Points
        ("Prop Points", "Prop Points"),
    ]),
    ("https://feeds.megaphone.fm/DFT4986441816", [  # Even Money
        ("", "Even Money"),
    ]),
]
SHOW_SLUG = {
    "VSiN By The Books": "vsin-by-the-books",
    "Follow the Money": "follow-the-money",
    "A Numbers Game": "a-numbers-game",
    "Prop Points": "prop-points",
    "Even Money": "even-money",
    "Circa Friday Football Invitational": "circa-friday-football-invitational",
}
IDLE_PRIORITY_CLASS = 0x00000040  # Windows: scans keep CPU priority


def now_et() -> datetime:
    return datetime.now(ET)


def slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-{2,}", "-", text)[:60].strip("-")


def episode_slug(show: str, title: str, date_str: str) -> str:
    """follow-the-money-2026-11-05-hour-1 style stem."""
    rest = title
    for prefix in (show, show.replace("VSiN ", "")):
        if rest.startswith(prefix):
            rest = rest[len(prefix):]
            break
    rest = rest.strip(" |")
    # drop the spelled-out date ("November 5, 2026") from the remainder
    rest = re.sub(r"(January|February|March|April|May|June|July|August|"
                  r"September|October|November|December)\s+\d{1,2},\s+\d{4}",
                  "", rest).strip(" |")
    tail = slugify(rest)
    if tail.startswith(date_str[:4] + "-"):
        tail = tail[5:]  # title already carries the year (Even Money)
    stem = f"{SHOW_SLUG[show]}-{date_str}"
    return f"{stem}-{tail}" if tail else stem


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"episodes": {}, "worker": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE_PATH)


def refresh_index(state: dict) -> None:
    """data/transcripts/index.json - done-only marker for the Muse side.
    The Muse podcast scan reads this (via pc-data/transcripts/index.json)
    to find Whisper transcripts for episodes Spotify has no transcript
    for. Rebuilt from state on every completion/discover; never carries
    queue internals (no URLs, no errors)."""
    done = []
    for e in state.get("episodes", {}).values():
        if e.get("status") != "done":
            continue
        done.append({
            "show": e["show"], "date": e["date"], "title": e["title"],
            "stem": e["stem"], "transcript": e.get("transcript", ""),
            "words": e.get("words", 0), "done_at": e.get("done_at", ""),
        })
    done.sort(key=lambda d: (d["date"], d["show"], d["stem"]))
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(
        {"generated_at": now_et().isoformat(), "count": len(done),
         "transcripts": done}, indent=1, sort_keys=True), encoding="utf-8")


def fetch_feed(url: str) -> list:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        root = XMLT.fromstring(r.read())
    out = []
    for it in root.findall(".//item"):
        enc = it.find("enclosure")
        if enc is None or not enc.get("url"):
            continue
        try:
            pub = parsedate_to_datetime((it.findtext("pubDate") or "").strip())
        except (TypeError, ValueError):
            continue
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=_tz.utc)  # "-0000" = unknown zone; feeds mean UTC
        out.append({
            "title": (it.findtext("title") or "").strip(),
            "pub": pub,
            "url": enc.get("url"),
            "length": enc.get("length") or "",
        })
    return out


def match_show(prefixes: list, title: str):
    for prefix, show in prefixes:
        if prefix == "" or title.startswith(prefix):
            return show
    return None


def discover(days: int) -> int:
    state = load_state()
    if not worker_alive(state):
        # No live worker: a crashed worker may have stranded an episode
        # in transcribing/downloading - requeue it so the next --work
        # picks it up instead of skipping it forever.
        for e in state.get("episodes", {}).values():
            if e.get("status") in ("transcribing", "downloading"):
                e["status"] = "queued"
                e["note"] = "requeued: worker not alive at discover"
                print(f"REQUEUED stale: {e['show']}|{e['date']}|{e['title']}")
    eps = state.setdefault("episodes", {})
    today = now_et().date()
    window = {today - timedelta(days=d) for d in range(days)}
    added = 0
    for url, prefixes in FEEDS:
        try:
            items = fetch_feed(url)
        except Exception as e:  # one bad feed never blocks the others
            print(f"FEED FAIL {url}: {e}")
            continue
        for it in items:
            show = match_show(prefixes, it["title"])
            if not show:
                continue
            ep_date = it["pub"].astimezone(ET).date()
            if ep_date not in window:
                continue
            date_str = ep_date.isoformat()
            key = f"{show}|{date_str}|{it['title']}"
            if key in eps:
                continue
            stem = episode_slug(show, it["title"], date_str)
            eps[key] = {
                "show": show, "title": it["title"], "date": date_str,
                "url": it["url"], "stem": stem, "status": "queued",
                "queued_at": now_et().isoformat(),
            }
            added += 1
            print(f"QUEUED: {key} -> {stem}")
    save_state(state)
    refresh_index(state)
    queued = sum(1 for e in eps.values() if e["status"] == "queued")
    print(f"SUMMARY: discover days={days} added={added} queued_total={queued}")
    return 0


def worker_alive(state: dict) -> bool:
    w = state.get("worker") or {}
    hb = w.get("heartbeat")
    if not hb or not LOCK_PATH.exists():
        return False
    try:
        age = (now_et() - datetime.fromisoformat(hb)).total_seconds()
    except ValueError:
        return False
    return age < 14400  # heartbeat within 4h = alive: one episode
    # transcribes ~2-3h with no heartbeat refresh mid-episode, so a
    # 30-min threshold falsely reports a live worker as dead and the
    # next --work spawns a duplicate worker on this 2-core box.


def work(limit) -> int:
    state = load_state()
    if worker_alive(state):
        print("SUMMARY: work skipped - worker already running")
        return 0
    queued = sum(1 for e in state.get("episodes", {}).values()
                 if e["status"] == "queued")
    if queued == 0:
        print("SUMMARY: work skipped - queue empty")
        return 0
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker"]
    if limit:
        cmd += ["--limit", str(limit)]
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen(cmd, cwd=str(BASE_DIR), creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)
    print(f"SUMMARY: worker spawned (queued={queued}, limit={limit})")
    return 0


def heartbeat(state: dict, **kw) -> None:
    state["worker"] = {"heartbeat": now_et().isoformat(), **kw}
    save_state(state)


def run_episode(state: dict, key: str, ep: dict) -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    audio = AUDIO_DIR / f"{ep['stem']}.mp3"
    ep["status"] = "downloading"
    heartbeat(state, current=key)
    try:
        req = urllib.request.Request(ep["url"], headers=UA)
        with urllib.request.urlopen(req, timeout=300) as r, \
                audio.open("wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        if audio.stat().st_size < 100_000:
            raise RuntimeError(f"download too small ({audio.stat().st_size} B)")
    except Exception as e:
        ep["status"] = "failed"
        ep["error"] = f"download: {e}"
        audio.unlink(missing_ok=True)
        heartbeat(state, current=None)
        print(f"FAILED download {key}: {e}", flush=True)
        return
    ep["status"] = "transcribing"
    heartbeat(state, current=key)
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "transcribe.py", str(audio)],
            cwd=str(BASE_DIR), capture_output=True, text=True,
            creationflags=IDLE_PRIORITY_CLASS)
        wall = time.time() - t0
        if proc.returncode != 0:
            raise RuntimeError(f"transcribe exit {proc.returncode}: "
                               f"{(proc.stdout or '')[-300:]}")
        txt = TRANSCRIPTS_DIR / f"{ep['stem']}.txt"
        if not txt.exists():
            raise RuntimeError("transcribe.py wrote no transcript")
        words = len(txt.read_text(encoding="utf-8", errors="replace").split())
        ep.update({"status": "done", "done_at": now_et().isoformat(),
                   "wall_s": round(wall), "words": words,
                   "transcript": f"transcripts/{ep['stem']}.txt"})
        refresh_index(state)
        print(f"DONE {key}: {words} words in {wall:.0f}s", flush=True)
    except Exception as e:
        ep["status"] = "failed"
        ep["error"] = f"transcribe: {e}"
        print(f"FAILED transcribe {key}: {e}", flush=True)
    finally:
        audio.unlink(missing_ok=True)
        heartbeat(state, current=None)


TOUCH_INTERVAL = 300  # seconds between lock touches


def _touch_lock(stop: threading.Event) -> None:
    """Daemon: keep the lock file fresh while the worker runs, so a
    live worker is never mistaken for dead mid-episode."""
    while not stop.is_set():
        try:
            LOCK_PATH.touch(exist_ok=True)
        except OSError:
            pass
        stop.wait(TOUCH_INTERVAL)


def worker(limit) -> int:
    state = load_state()
    LOCK_PATH.write_text(str(now_et()), encoding="utf-8")
    _stop = threading.Event()
    threading.Thread(target=_touch_lock, args=(_stop,), daemon=True).start()
    heartbeat(state, started=now_et().isoformat())
    done = 0
    try:
        while limit is None or done < limit:
            state = load_state()
            pending = sorted(
                ((k, e) for k, e in state["episodes"].items()
                 if e["status"] == "queued"),
                key=lambda kv: kv[1]["queued_at"])
            if not pending:
                break
            key, ep = pending[0]
            run_episode(state, key, ep)
            done += 1
    finally:
        _stop.set()
        state = load_state()
        state["worker"] = {"heartbeat": now_et().isoformat(), "finished": True}
        save_state(state)
        LOCK_PATH.unlink(missing_ok=True)
    print(f"SUMMARY: worker finished, {done} episode(s) processed")
    return 0


def status() -> int:
    state = load_state()
    counts = {}
    for e in state.get("episodes", {}).values():
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    print(f"SUMMARY: queue {counts} worker={state.get('worker')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--work", action="store_true")
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.discover:
        return discover(args.days)
    if args.work:
        return work(args.limit)
    if args.worker:
        return worker(args.limit)
    if args.status:
        return status()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
