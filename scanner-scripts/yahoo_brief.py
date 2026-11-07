"""Yahoo Mail daily brief extractor (PC port, added 2026-11-06 at
Michael's tap — "Yes, build the PC Yahoo brief").

Runs on the scan PC under scheduler.py in Michael's interactive session,
using a DEDICATED Playwright persistent profile for Yahoo
(%LOCALAPPDATA%\\pc_scans\\yahoo_profile) — separate from the DarkHorse
and Costco profiles and from his regular Chrome. He signs in ONCE
himself:

    python yahoo_brief.py --signin    # headed: sign in yourself, window polls
    python yahoo_brief.py --check     # report whether the profile is signed in
    python yahoo_brief.py             # the daily extraction (scheduler entry)

Daily run: opens Yahoo Mail, detects the signed-in state, and — when
signed in — scans the last 7 days of inbox mail, classifies messages
into the brief's lanes (urgent / Miles Earn and Burn / Doctor of Credit /
gift cards / sportsbook), opens the matched messages, and banks sender,
subject, date and plain-text body (truncated) to:
    data/yahoo-brief/yahoo-brief-YYYY-MM-DD.json   (full digest)
    data/yahoo-brief/yahoo-brief-YYYY-MM-DD.md     (one-screen summary)
    data/yahoo-brief/dump-YYYY-MM-DD.txt           (raw list rows,
                                                    calibration surface)
which sync_to_github.py pushes to pc-data/yahoo-brief/. This script only
EXTRACTS — the summarizing/brief happens Muse-side when the digest
lands, exactly like the other PC scans.

Signed OUT is a normal outcome, not a failure: the report is written
with signed_in=false and the run exits 0, so the scheduler logs OK and
Chet can tell Michael a sign-in is needed. If the inbox DOM cannot be
parsed, the report still lands with signed_in plus an error note.

READ-ONLY: never sends, replies, deletes, clicks out to claim or buy,
and never changes account settings. No password is stored in this
script or handled by it — the session lives in the local profile only,
created by Michael signing in himself in the --signin window.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from config import DATA_DIR, PAGE_TIMEOUT_MS

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")

_lad = os.environ.get("LOCALAPPDATA")
if _lad:
    YAHOO_PROFILE_DIR = Path(_lad) / "pc_scans" / "yahoo_profile"
else:
    YAHOO_PROFILE_DIR = Path.home() / ".pc_scans" / "yahoo_profile"

ACCOUNT_HINT = "venutiml@yahoo.com (expected)"
MAIL_URL = "https://mail.yahoo.com/"
WINDOW_DAYS = 7
BODY_MAX_CHARS = 6000
MAX_OPEN = 40          # cap on messages opened per run
MAX_SCROLLS = 14        # cap on inbox list scrolls per run
OUT_DIR = DATA_DIR / "yahoo-brief"

MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
DATE_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\b")
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\s*(AM|PM)\b", re.I)

# ---------------------------------------------------------------- lanes
# Matching is on sender + subject (and, for urgent, the body once opened).
# A message can land in several lanes; urgent is computed across all.

def _has(text: str, *words: str) -> bool:
    t = text.lower()
    return any(w in t for w in words)


def is_miles(sender: str, subject: str) -> bool:
    return _has(sender + " " + subject,
                "milesearnandburn", "miles earn and burn",
                "matt@milesearnandburn")


def is_doc(sender: str, subject: str) -> bool:
    return _has(sender + " " + subject,
                "doctor of credit", "doctorofcredit")


GIFT_WORDS = ("gift card", "giftcard", "giftcards.com", "runa", "qcgc",
              "aligned incentives", "cardcash", "raise marketplace",
              "egift", "e-gift")
SPORTS_WORDS = ("fanduel", "draftkings", "betmgm", "caesars sportsbook",
                "fanatics sportsbook", "espn bet", "betrivers",
                "bally bet", "betparx", "bet365", "hard rock bet",
                "novig", "prophetx", "sportsbook", "bonus bet",
                "odds boost", "free play")
URGENT_SUBJECT_WORDS = (
    "security alert", "sign-in attempt", "sign in attempt",
    "new sign-in", "suspicious", "verify your", "action required",
    "payment due", "payment failed", "payment declined", "past due",
    "autopay", "expires", "expiring", "last day", "ends today",
    "ending soon", "delivery failed", "delivery problem",
    "shipping delay", "your order has been delayed", "unusual activity",
    "password was changed", "account alert", "fraud alert")
URGENT_BODY_WORDS = (
    "expires on", "expires by", "expiration date", "respond by",
    "action required", "payment failed", "payment declined",
    "past due", "will be charged", "renewal price")


def classify(sender: str, subject: str, body: str = "") -> list:
    lanes = []
    if is_miles(sender, subject):
        lanes.append("miles_earn_and_burn")
    if is_doc(sender, subject):
        lanes.append("doctor_of_credit")
    if _has(sender + " " + subject, *GIFT_WORDS):
        lanes.append("gift_cards")
    if _has(sender + " " + subject, *SPORTS_WORDS):
        lanes.append("sportsbook")
    if _has(subject, *URGENT_SUBJECT_WORDS) or (
            body and _has(body[:2500], *URGENT_BODY_WORDS)):
        lanes.append("urgent")
    return lanes


LANE_ORDER = ["urgent", "miles_earn_and_burn", "doctor_of_credit",
              "gift_cards", "sportsbook"]
LANE_TITLES = {
    "urgent": "Urgent / deadlines / security",
    "miles_earn_and_burn": "Miles Earn and Burn",
    "doctor_of_credit": "Doctor of Credit",
    "gift_cards": "Gift cards",
    "sportsbook": "Sportsbook offers",
}

# ---------------------------------------------------------------- probe

PROBE_JS = """() => {
    const url = location.href;
    const body = document.body ? document.body.innerText : '';
    const has = (sel) => !!document.querySelector(sel);
    const signedIn = has('[data-test-id="compose-button"]')
        || has('[data-test-id="message-list"]')
        || has('[data-test-id="folder-list"]')
        || (body.includes('Compose') && body.includes('Inbox'));
    const loginForm = has('input[name="username"]')
        || has('#login-username') || url.includes('login.yahoo.com');
    return { url, signedIn, loginForm, title: document.title,
             bodyHead: body.slice(0, 400) };
}"""


def probe(page) -> dict:
    """Best-effort signed-in verdict for the current Yahoo page."""
    try:
        info = page.evaluate(PROBE_JS)
    except Exception as e:  # noqa: BLE001
        return {"signed_in": None, "url": page.url, "error": str(e)}
    if info.get("loginForm") and not info.get("signedIn"):
        return {"signed_in": False, "url": info.get("url", page.url)}
    if info.get("signedIn"):
        return {"signed_in": True, "url": info.get("url", page.url)}
    if "login.yahoo.com" in (info.get("url") or ""):
        return {"signed_in": False, "url": info.get("url")}
    # Fresh/logged-out profiles land on a Yahoo Mail promo page whose
    # only mail affordance is a "Sign in" CTA — that is signed OUT.
    head = (info.get("bodyHead") or "")
    if "Sign in" in head and "Compose" not in head:
        return {"signed_in": False, "url": info.get("url", page.url),
                "note": "promo/landing page with Sign in CTA, no inbox"}
    return {"signed_in": None, "url": info.get("url", page.url),
            "note": "no inbox or login markers found",
            "body_head": info.get("bodyHead", "")}


def open_mail(page) -> dict:
    page.goto(MAIL_URL, timeout=PAGE_TIMEOUT_MS,
              wait_until="domcontentloaded")
    page.wait_for_timeout(7000)
    verdict = probe(page)
    # Yahoo sometimes lands on a marketing/consent interstitial first.
    if verdict["signed_in"] is None:
        page.wait_for_timeout(5000)
        verdict = probe(page)
    return verdict

# ------------------------------------------------------------ list scan

ROWS_JS = """() => {
    const sels = ['[data-test-id="message-list-item"]',
                  '[data-test-id*="message-list"] li',
                  'ul[data-test-id] li', '[role="listbox"] [role="option"]',
                  '[role="list"] [role="listitem"]', 'li[data-test-id]'];
    let items = [];
    for (const s of sels) {
        items = Array.from(document.querySelectorAll(s));
        if (items.length >= 3) break;
    }
    if (items.length < 3) {
        // fallback: any list items under the main region with mail-ish text
        items = Array.from(document.querySelectorAll('main li, [role="main"] li'));
    }
    return items.map(el => (el.innerText || '').trim()).filter(t => t.length > 10);
}"""

SCROLL_JS = """() => {
    const cands = document.querySelectorAll(
        '[data-test-id="message-list"], [data-test-id*="scroll"], main, [role="main"]');
    let best = null, bestH = 0;
    document.querySelectorAll('div, ul').forEach(el => {
        if (el.scrollHeight > el.clientHeight + 200 && el.clientHeight > 200
                && el.scrollHeight > bestH) { best = el; bestH = el.scrollHeight; }
    });
    const el = best || document.scrollingElement;
    if (!el) return false;
    el.scrollTop = el.scrollTop + Math.max(400, el.clientHeight * 0.8);
    return true;
}"""


def parse_row(text: str, today: datetime) -> dict | None:
    """Split one list row's text into sender / subject / date fields.

    Yahoo rows render as stacked lines: sender, subject, snippet, date
    (time for today, 'Yesterday', or 'Mon D'). Parsing is best-effort;
    the raw text is banked in the dump either way.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    date_text, msg_date = "", None
    for ln in lines:
        m = DATE_RE.search(ln)
        if m:
            date_text = m.group(0)
            year = today.year
            msg_date = datetime(year, MONTHS[m.group(1)],
                                int(m.group(2)), tzinfo=ET)
            if msg_date > today + timedelta(days=2):  # Dec row in January
                msg_date = msg_date.replace(year=year - 1)
            break
        if TIME_RE.search(ln) and len(ln) < 12:
            date_text = ln
            msg_date = today
            break
        if ln.lower() == "yesterday":
            date_text = ln
            msg_date = today - timedelta(days=1)
            break
    content = [ln for ln in lines if ln != date_text]
    sender = content[0] if content else ""
    subject = content[1] if len(content) > 1 else ""
    if not sender or not subject:
        return None
    return {"sender": sender[:120], "subject": subject[:300],
            "date_text": date_text,
            "date": msg_date.strftime("%Y-%m-%d") if msg_date else "",
            "_msg_date": msg_date, "raw": text}


def collect_rows(page, today: datetime) -> tuple[list, list]:
    """Scroll the inbox list, collecting rows from the last WINDOW_DAYS."""
    cutoff = today - timedelta(days=WINDOW_DAYS)
    seen: dict[str, dict] = {}
    dump: list[str] = []
    old_streak = 0
    for _ in range(MAX_SCROLLS):
        try:
            texts = page.evaluate(ROWS_JS)
        except Exception as e:  # noqa: BLE001
            dump.append(f"ROWS_JS failed: {e}")
            break
        new_here = 0
        batch_old = True
        for t in texts:
            row = parse_row(t, today)
            if not row:
                continue
            key = f"{row['sender']}|{row['subject']}|{row['date_text']}"
            if key not in seen:
                seen[key] = row
                new_here += 1
                dump.append("----- row -----\n" + t[:600])
            if row["_msg_date"] is None or row["_msg_date"] >= cutoff:
                batch_old = False
        if batch_old and texts:
            old_streak += 1
            if old_streak >= 2:
                break
        else:
            old_streak = 0
        if new_here == 0 and _ > 2:
            # virtualized list may need a beat; one extra scroll, then stop
            try:
                page.evaluate(SCROLL_JS)
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(1200)
            break
        try:
            page.evaluate(SCROLL_JS)
        except Exception:  # noqa: BLE001
            break
        page.wait_for_timeout(1400)
    rows = [r for r in seen.values()
            if r["_msg_date"] is None or r["_msg_date"] >= cutoff]
    rows.sort(key=lambda r: r["_msg_date"] or today, reverse=True)
    return rows, dump

# ------------------------------------------------------------ body read

BODY_JS = """() => {
    const sels = ['[data-test-id="message-view-body"]',
                  '[data-test-id="message-body"]', '.mail-message-body',
                  '[data-test-id="message-view"]'];
    for (const s of sels) {
        const el = document.querySelector(s);
        if (el && (el.innerText || '').trim().length > 40)
            return el.innerText.trim();
    }
    // fallback: longest text block in the main region
    let best = '';
    document.querySelectorAll('main div, [role="main"] div').forEach(el => {
        const t = (el.innerText || '').trim();
        if (t.length > best.length && t.length < 60000) best = t;
    });
    return best;
}"""


def read_body(page) -> str:
    try:
        text = page.evaluate(BODY_JS) or ""
    except Exception:  # noqa: BLE001
        text = ""
    if len(text) < 40:  # some messages render the body in a child frame
        for frame in page.frames[1:]:
            try:
                t = frame.evaluate(
                    "() => document.body ? document.body.innerText : ''")
            except Exception:  # noqa: BLE001
                continue
            if t and len(t) > len(text):
                text = t
    return " ".join(text.split())[:BODY_MAX_CHARS]


def open_row(page, row: dict) -> bool:
    """Click the list row matching this message; True if a view opened."""
    subject = row["subject"]
    handle = page.evaluate_handle("""(subj) => {
        const sels = ['[data-test-id="message-list-item"]',
                      '[data-test-id*="message-list"] li', 'main li',
                      '[role="main"] li', 'li[data-test-id]'];
        for (const s of sels) {
            for (const el of document.querySelectorAll(s)) {
                if ((el.innerText || '').includes(subj)) return el;
            }
        }
        return null;
    }""", subject[:60])
    try:
        el = handle.as_element()
        if el is None:
            return False
        el.scroll_into_view_if_needed(timeout=5000)
        el.click(timeout=8000)
        page.wait_for_timeout(2500)
        return True
    except Exception:  # noqa: BLE001
        return False


def go_back(page) -> None:
    for sel in ('[data-test-id="back-button"]', '[aria-label="Back"]',
                'button[title="Back"]'):
        try:
            page.click(sel, timeout=2500)
            page.wait_for_timeout(1500)
            return
        except Exception:  # noqa: BLE001
            continue
    try:
        page.go_back(timeout=10000)
        page.wait_for_timeout(1500)
    except Exception:  # noqa: BLE001
        pass

# ------------------------------------------------------------------ run

def write_report(result: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = result["date"]
    (OUT_DIR / f"yahoo-brief-{today}.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    lanes = result["lanes"]
    md = [f"# Yahoo brief (PC) — {today}",
          f"Run: {result['run_time_pt']} | account: {ACCOUNT_HINT} | "
          f"signed_in: {result['signed_in']}",
          f"Scanned rows (last {WINDOW_DAYS} days): "
          f"{result['scanned_rows']} | matched messages: "
          f"{len(result['messages'])}", ""]
    if result["signed_in"] is False:
        md.append("**Signed OUT** — the PC Yahoo profile is not signed "
                  "in. Michael: run the yahoo-signin-once task (or "
                  "`yahoo_brief.py --signin`) at the PC and sign in "
                  "yourself; the brief then runs on its own.")
    for lane in LANE_ORDER:
        items = lanes.get(lane) or []
        if not items:
            continue
        md.append(f"## {LANE_TITLES[lane]} ({len(items)})")
        for m in items:
            md.append(f"- [{m['date_text'] or m['date']}] {m['sender']} — "
                      f"{m['subject']}")
            if m.get("body"):
                md.append(f"  {m['body'][:500]}")
        md.append("")
    if result.get("errors"):
        md.append("Errors: " + "; ".join(result["errors"]))
    if result.get("parse_note"):
        md.append(f"Note: {result['parse_note']}")
    (OUT_DIR / f"yahoo-brief-{today}.md").write_text(
        "\n".join(md), encoding="utf-8")
    if result.get("_dump"):
        (OUT_DIR / f"dump-{today}.txt").write_text(
            "\n".join(result["_dump"]), encoding="utf-8")


def run_daily() -> int:
    today_dt = datetime.now(ET)
    today = today_dt.strftime("%Y-%m-%d")
    result = {"date": today,
              "run_time_et": today_dt.strftime("%Y-%m-%d %H:%M ET"),
              "run_time_pt": datetime.now(PT).strftime("%Y-%m-%d %H:%M PT"),
              "account": ACCOUNT_HINT, "signed_in": None,
              "window_days": WINDOW_DAYS, "scanned_rows": 0,
              "lanes": {lane: [] for lane in LANE_ORDER},
              "messages": [], "errors": [], "parse_note": "",
              "_dump": []}
    YAHOO_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(YAHOO_PROFILE_DIR), headless=False,
                args=["--disable-blink-features=AutomationControlled"])
            page = ctx.new_page()
            verdict = open_mail(page)
            result["signed_in"] = verdict["signed_in"]
            if verdict["signed_in"] is not True:
                if verdict["signed_in"] is None:
                    result["parse_note"] = (
                        "inbox state unclear (no inbox/login markers); "
                        f"url={verdict.get('url')} "
                        f"note={verdict.get('note', '')} "
                        f"body_head={verdict.get('body_head', '')[:200]}")
                write_report(result)
                print(f"SUMMARY: signed_in={result['signed_in']} — "
                      "report written, no extraction", flush=True)
                ctx.close()
                return 0
            rows, dump = collect_rows(page, today_dt)
            result["_dump"] = dump
            result["scanned_rows"] = len(rows)
            matched = []
            for r in rows:
                lanes = classify(r["sender"], r["subject"])
                if lanes:
                    r["lanes"] = lanes
                    matched.append(r)
            # Open matched messages (urgent first) for body + reclassify.
            matched.sort(key=lambda r: 0 if "urgent" in r["lanes"] else 1)
            opened = 0
            for r in matched:
                if opened >= MAX_OPEN:
                    r["opened"] = False
                    continue
                if open_row(page, r):
                    body = read_body(page)
                    r["body"] = body
                    r["opened"] = True
                    opened += 1
                    for lane in classify(r["sender"], r["subject"], body):
                        if lane not in r["lanes"]:
                            r["lanes"].append(lane)
                    go_back(page)
                else:
                    r["opened"] = False
                    result["errors"].append(
                        f"could not open: {r['subject'][:60]}")
            for r in matched:
                msg = {"sender": r["sender"], "subject": r["subject"],
                       "date": r["date"], "date_text": r["date_text"],
                       "lanes": r["lanes"], "opened": r.get("opened", False),
                       "body": r.get("body", "")}
                result["messages"].append(msg)
                for lane in r["lanes"]:
                    result["lanes"][lane].append(msg)
            if not rows:
                result["parse_note"] = (
                    "signed in, but no inbox rows could be parsed — "
                    "DOM calibration needed (see dump file)")
            ctx.close()
    except Exception as e:  # noqa: BLE001 - report, never crash the run
        result["errors"].append(f"run failed: {e}")
        write_report(result)
        print(f"SUMMARY: FAILED - {e}", flush=True)
        return 2
    write_report(result)
    counts = {k: len(v) for k, v in result["lanes"].items() if v}
    print(f"SUMMARY: signed_in=True, rows={result['scanned_rows']}, "
          f"matched={len(result['messages'])}, lanes={counts}, "
          f"errors={len(result['errors'])}", flush=True)
    return 0


def signed_in_probe(page) -> bool:
    return open_mail(page)["signed_in"] is True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signin", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    YAHOO_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    if args.signin:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(YAHOO_PROFILE_DIR), headless=False,
                args=["--disable-blink-features=AutomationControlled"])
            page = ctx.new_page()
            page.goto(MAIL_URL, timeout=PAGE_TIMEOUT_MS,
                      wait_until="domcontentloaded")
            print("A browser window is open on Yahoo Mail. Sign in "
                  "YOURSELF in that window, then leave the window open.")
            print("Waiting up to 10 minutes for a signed-in session...")
            ok = False
            for _ in range(40):
                time.sleep(15)
                try:
                    ok = probe(page)["signed_in"] is True
                except Exception:  # noqa: BLE001 - keep waiting
                    ok = False
                if ok:
                    break
            verdict = ("signed in - inbox markers found"
                       if ok else "not signed in after 10 minutes")
            print(f"Yahoo sign-in result: {verdict}")
            try:
                DATA_DIR.mkdir(exist_ok=True)
                (DATA_DIR / "yahoo-signin-result.txt").write_text(
                    f"{datetime.now(ET).isoformat()} {verdict}",
                    encoding="utf-8")
            except OSError:
                pass
            ctx.close()
            return 0 if ok else 1
    if args.check:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(YAHOO_PROFILE_DIR), headless=True,
                args=["--disable-blink-features=AutomationControlled"])
            page = ctx.new_page()
            try:
                ok = signed_in_probe(page)
            except Exception as e:  # noqa: BLE001
                print(f"Yahoo session: check failed: {e}")
                ctx.close()
                return 1
            print(f"Yahoo session: {'signed in' if ok else 'NOT signed in'}")
            ctx.close()
            return 0 if ok else 1
    return run_daily()


if __name__ == "__main__":
    sys.exit(main())
