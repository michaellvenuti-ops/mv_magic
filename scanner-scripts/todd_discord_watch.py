#!/usr/bin/env python3
"""Todd Discord fast-alert relay (added 2026-11-18 at Michael's tap).

Watches #todd-picks on the 'CBB Discord' server for Todd's in-play
college-basketball plays and relays each one instantly. NO auto-betting,
ever -- Michael places every bet himself. The relay only alerts + logs.

How it works:
  * Runs as a persistent loop (NOT a scheduler-rotation job) under its
    OWN Windows scheduled task (todd-discord-watch, logon trigger),
    polling the channel every 30 seconds.
  * Reads Discord in its OWN dedicated Playwright persistent profile
    (%LOCALAPPDATA%\\pc_scans\\discord_profile) -- never touches the
    DarkHorse Chromium profile, the Yahoo profile, or his regular Chrome.
  * He signs in ONCE himself (see --signin): the session then persists
    in that profile. No password is stored or handled here, and no
    captcha is ever solved automatically.
  * On each NEW Todd message it: parses the play best-effort (game,
    market, line, price, stake as stated -- raw text always kept),
    pushes it INSTANTLY to ntfy (Title + Click headers, so the phone
    notification itself is the one-tap card), appends it to the dated
    delivery file data/todd-picks-YYYY-MM-DD.md (synced to pc-data by
    the regular sync, and ALSO pushed immediately via github_push so
    the chat card lands in ~2 minutes instead of waiting for the
    ~10-minute sync), and appends it to data/todd-picks-log.json
    (the tracker log, graded next morning at $100 flat, Muse-side).

Usage:
    py -3.12 todd_discord_watch.py --signin   # headed: sign in
        yourself, click into CBB Discord > #todd-picks, wait for the
        script to verify the channel and save the config.
    py -3.12 todd_discord_watch.py --check    # headless: report
        whether the profile is signed in and the channel loads.
    py -3.12 todd_discord_watch.py            # the watch loop
        (started by run-todd-watch.cmd under the scheduled task).

Novig deep link (verified 2026-11-18, NOT invented): Novig links
resolve only to the EVENT page -- they do NOT pre-fill the bet
(SharpAPI production measurement: Novig deep link Yes, preselects
selection No). Novig publishes no per-event URL pattern and no public
API, so a Todd game cannot be resolved to a Novig event ID. The card
therefore links to the Novig app entry, https://app.novig.us/AppEntry
(the link Novig itself distributes), and Michael searches the game
there -- tap the card, confirm stake, place the bet. If Novig ever
ships event deep links, update NOVIG_LINK and the resolver here.

READ-ONLY on Discord: never sends, replies, reacts, or changes any
account/server setting. Not a self-bot: no user token is ever used --
only the logged-in browser session Michael created himself.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DATA_DIR, PAGE_TIMEOUT_MS  # noqa: E402
from github_push import put_file  # noqa: E402

ET = ZoneInfo("America/New_York")
POLL_SECONDS = 30
NOVIG_LINK = "https://app.novig.us/AppEntry"
DISCORD_APP_URL = "https://discord.com/app"

_lad = os.environ.get("LOCALAPPDATA")
if _lad:
    DISCORD_PROFILE_DIR = Path(_lad) / "pc_scans" / "discord_profile"
else:
    DISCORD_PROFILE_DIR = Path.home() / ".pc_scans" / "discord_profile"

CONFIG_PATH = DATA_DIR / "todd-discord-config.json"
STATE_PATH = DATA_DIR / "todd-discord-state.json"
LOG_PATH = DATA_DIR / "todd-picks-log.json"
WATCH_LOG = DATA_DIR / "todd-discord-watch.log"
SIGNIN_RESULT = DATA_DIR / "todd-discord-signin-result.txt"
CHANNEL_RE = re.compile(r"/channels/(\d+)/(\d+)")
PRICE_RE = re.compile(r"([+-])(\d{2,3})\b")
STAKE_DOLLAR_RE = re.compile(r"\$([\d,]+)")
STAKE_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:u|units?)\b", re.I)


def now_et():
    return datetime.now(ET)


def today_str():
    return now_et().strftime("%Y-%m-%d")


def log(msg):
    line = f"[{now_et():%Y-%m-%d %H:%M:%S} ET] {msg}"
    print(line, flush=True)
    try:
        WATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WATCH_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ state

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def load_config():
    return load_json(CONFIG_PATH, {})


def load_state():
    st = load_json(STATE_PATH, {})
    st.setdefault("seen", [])
    st.setdefault("last_poll", None)
    st.setdefault("last_session_lost_ntfy", None)
    st.setdefault("plays_logged", 0)
    return st


def save_state(st):
    st["seen"] = sorted(set(st.get("seen", [])))[-5000:]
    save_json(STATE_PATH, st)


# ------------------------------------------------------------------ ntfy

def ntfy_todd(title, body):
    """Instant phone alert. Title + Click headers make the notification
    itself the one-tap card: tap it and Novig opens. Returns True if
    the post went out. Never raises."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        log("ntfy: NTFY_TOPIC not set, cannot push alert")
        return False
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
            method="POST",
            headers={"Title": title, "Click": NOVIG_LINK,
                     "Priority": "high", "Tags": "basketball",
                     "Content-Type": "text/plain"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return True
    except Exception as exc:  # noqa: BLE001 - alert path must not die
        log(f"ntfy: push failed ({exc})")
        return False


# ------------------------------------------------------------------ parse

_MARKET_WORDS_RE = re.compile(
    r"\s+(live|over|under|ml|moneyline|spread|total|1h|2h|1st\s+half|"
    r"2nd\s+half)$", re.I)
_LEADING_MARKET_WORDS_RE = re.compile(
    r"^(live|over|under|ml|moneyline|spread|total|1h|2h|1st\s+half|"
    r"2nd\s+half)\s+", re.I)


def _clean_team(s):
    if not s:
        return s
    s = _MARKET_WORDS_RE.sub("", s.strip()).strip()
    return _LEADING_MARKET_WORDS_RE.sub("", s).strip()


def parse_play(text):
    """Best-effort parse of a Todd message. Never drops the raw text;
    unparsed fields stay None and the alert still goes out."""
    t = " ".join(text.split())
    out = {"game": None, "market": None, "line": None, "price": None,
           "stake": None, "stake_text": None, "raw_text": t, "side": None}
    low = t.lower()
    half = None
    if "1st half" in low or re.search(r"\b1h\b", low):
        half = "1st Half"
    elif "2nd half" in low or re.search(r"\b2h\b", low):
        half = "2nd Half"
    is_total = bool(re.search(r"\b(over|under|total)\b", low))
    is_ml = bool(re.search(r"\b(ml|moneyline)\b", low))
    if is_total:
        out["market"] = (half + " Total") if half else "Total"
    elif is_ml:
        out["market"] = "Moneyline"
    elif half:
        out["market"] = half + " Spread"
    elif "live" in low or "spread" in low:
        out["market"] = "Spread"
    # teams: "A vs B" / "A @ B" / "A at B"
    m = re.search(r"([A-Za-z][A-Za-z .&'-]{2,30}?)\s+(?:vs\.?|@|at)\s+"
                  r"([A-Za-z][A-Za-z .&'-]{2,30})", t)
    if m:
        out["game"] = (f"{_clean_team(m.group(1))} vs "
                       f"{_clean_team(m.group(2))}")
    # numbers: signed decimals are lines, integers 100-500 are prices
    line = None
    price = None
    for tok in re.findall(r"[+-]\d+(?:\.\d+)?", t):
        if "." in tok:
            if line is None:
                line = tok
        else:
            try:
                v = int(tok)
            except ValueError:
                continue
            if 100 <= abs(v) <= 500:
                price = v
    # bare total: "Over 145.5" / "Under 78.5" (no sign on the number)
    if line is None and is_total:
        m = re.search(r"\b(?:over|under)\s+(\d+(?:\.\d+)?)", t, re.I)
        if m:
            line = m.group(1)
    # a signed line with no other market word is a spread
    if line is not None and out["market"] is None:
        out["market"] = "Spread"
    out["line"] = line
    out["price"] = price
    # side: the team the line/price attaches to (the grader needs it).
    # Totals: Over/Under. Spread/live: the team named right before the
    # line. Moneyline: the team named before "ML".
    side = None
    if out["market"] and "Total" in out["market"]:
        if re.search(r"\bover\b", low):
            side = "Over"
        elif re.search(r"\bunder\b", low):
            side = "Under"
    elif out["market"] == "Moneyline":
        m = re.search(r"([A-Za-z][A-Za-z .&'-]{2,30}?)\s+"
                      r"(?:ML|moneyline)\b", t, re.I)
        if m:
            side = _clean_team(m.group(1))
    else:
        m = re.search(r"([A-Za-z][A-Za-z .&'-]{2,30}?)\s+"
                      r"(?:(?:1H|2H|1st\s+half|2nd\s+half|live)\s+)?"
                      r"[+-]\d+(?:\.\d+)?", t)
        if m:
            side = _clean_team(m.group(1))
    out["side"] = side
    # stake: $ first, else units
    m = STAKE_DOLLAR_RE.search(t)
    if m:
        try:
            out["stake"] = int(m.group(1).replace(",", ""))
            out["stake_text"] = f"${m.group(1)}"
        except ValueError:
            pass
    else:
        m = STAKE_UNIT_RE.search(t)
        if m:
            out["stake_text"] = f"{m.group(1)}u"
    return out


def play_title(parsed):
    bits = []
    if parsed.get("game"):
        bits.append(parsed["game"])
    mk = parsed.get("market") or ""
    ln = parsed.get("line") or ""
    if mk or ln:
        bits.append(f"{mk} {ln}".strip())
    return " — ".join(bits) if bits else "Todd play (in-play CBB)"


# ------------------------------------------------------------------ banking

def next_play_id(log_obj):
    n = len(log_obj.get("plays", [])) + 1
    return f"TD-{n:03d}"


def bank_play(parsed, message_id, posted):
    """Append to the tracker log + the dated delivery file. Returns
    the play dict."""
    log_obj = load_json(LOG_PATH, {"plays": []})
    log_obj.setdefault("plays", [])
    play = {
        "id": next_play_id(log_obj),
        "date": today_str(),
        "posted_et": posted or now_et().strftime("%Y-%m-%d %H:%M ET"),
        "game": parsed.get("game"),
        "market": parsed.get("market"),
        "side": parsed.get("side"),
        "line": parsed.get("line"),
        "price": parsed.get("price"),
        "stake": parsed.get("stake"),
        "stake_text": parsed.get("stake_text"),
        "raw_text": parsed.get("raw_text"),
        "message_id": message_id,
        "channel": "#todd-picks",
        "source": "Todd (Discord #todd-picks)",
        "status": "pending",
        "result": None,
        "pl": None,
        "final": None,
        "final_value": None,
        "novig_link": NOVIG_LINK,
    }
    log_obj["plays"].append(play)
    save_json(LOG_PATH, log_obj)

    md_path = DATA_DIR / f"todd-picks-{today_str()}.md"
    header = (f"# Todd picks — {today_str()} "
              f"(PC Discord watch, #todd-picks, CBB Discord)\n")
    section = (f"## {now_et():%Y-%m-%d %H:%M:%S} ET — 1 NEW Todd play\n"
               f"NEW TODD PLAY: {play_title(parsed)}"
               f"{' ' + str(play['price']) if play['price'] is not None else ''}"
               f"{' (stake ' + play['stake_text'] + ')' if play['stake_text'] else ''}"
               f" :: raw: \"{play['raw_text']}\""
               f" :: msg {message_id} :: posted {play['posted_et']}"
               f" :: Novig: {NOVIG_LINK}\n")
    if not md_path.exists():
        md_path.write_text(header + section, encoding="utf-8")
    else:
        with open(md_path, "a", encoding="utf-8") as fh:
            fh.write(section)
    return play, md_path


def push_delivery_file(md_path):
    """Immediate GitHub push so the chat card lands in ~2 minutes
    instead of waiting for the ~10-minute sync. One retry after 15s
    (409 race with the sync flood); failure is quiet -- the regular
    sync remains the backstop."""
    repo_path = f"pc-data/{md_path.name}"
    ok, detail = put_file(repo_path, md_path, f"Todd play {today_str()}")
    if ok:
        log(f"github: pushed {repo_path} ({detail})")
        return True
    log(f"github: push failed ({detail}), retrying once in 15s")
    time.sleep(15)
    ok, detail = put_file(repo_path, md_path, f"Todd play {today_str()}")
    log(f"github: retry {'ok (' + detail + ')' if ok else 'failed (' + detail + ')'}")
    return ok


# ------------------------------------------------------------------ discord

READ_MESSAGES_JS = """
() => {
  const out = [];
  document.querySelectorAll('li[id^="chat-messages-"]').forEach(li => {
    const idAttr = li.id.replace('chat-messages-', '');
    const parts = idAttr.split('-');
    const messageId = parts.length ? parts[parts.length - 1] : idAttr;
    let author = '';
    const userEl = li.querySelector('[class*="username"]');
    if (userEl) author = userEl.textContent.trim();
    let posted = '';
    const timeEl = li.querySelector('time');
    if (timeEl) posted = timeEl.getAttribute('datetime') || timeEl.textContent.trim();
    let text = '';
    const contentEl = li.querySelector('[id^="message-content-"]');
    if (contentEl) text = contentEl.innerText.trim();
    else text = (li.innerText || '').trim();
    if (messageId && text) out.push({message_id: messageId, author: author, posted: posted, text: text});
  });
  return out;
}
"""

SCROLL_BOTTOM_JS = """
() => {
  const list = document.querySelector('ol[data-list-id="chat-messages"]');
  if (!list) return false;
  let el = list.parentElement;
  while (el) {
    if (el.scrollHeight > el.clientHeight + 4) { el.scrollTop = el.scrollHeight; return true; }
    el = el.parentElement;
  }
  return false;
}
"""

AUTHORS_JS = """
() => {
  const counts = {};
  document.querySelectorAll('li[id^="chat-messages-"] [class*="username"]').forEach(el => {
    const a = (el.textContent || '').trim();
    if (a) counts[a] = (counts[a] || 0) + 1;
  });
  return counts;
}
"""


def is_login_wall(page):
    try:
        url = page.url or ""
        if "/login" in url or "discord.com/login" in url:
            return True
        body = page.locator("body").inner_text(timeout=5000)
        return "Log in to Discord" in body and "chat-messages-" not in page.content()
    except Exception:
        return False


def channel_loaded(page):
    try:
        page.wait_for_selector('ol[data-list-id="chat-messages"]',
                               timeout=30000)
        return True
    except Exception:
        return False


def open_channel(page, url):
    page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
    # dismiss a first-run modal if one blocks the list
    try:
        close = page.locator('[aria-label="Close"]').first
        if close.is_visible(timeout=3000):
            close.click(timeout=3000)
    except Exception:
        pass
    return channel_loaded(page)


def is_todd_message(msg, todd_author):
    author = (msg.get("author") or "").strip()
    if todd_author and author:
        return author == todd_author or "todd" in author.lower()
    if author:
        return "todd" in author.lower()
    return True  # no author readable: channel is Todd's picks channel


# ------------------------------------------------------------------ modes

def do_signin():
    """Headed sign-in window. Michael logs in himself (password,
    captcha, 2FA -- all his steps at the keyboard), then clicks into
    CBB Discord > #todd-picks. The script verifies the channel from
    the tab title + URL and saves the config. Nothing is stored but
    the channel location and the author name."""
    from playwright.sync_api import sync_playwright

    DISCORD_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(DISCORD_PROFILE_DIR), headless=False,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.new_page()
        page.goto(DISCORD_APP_URL, timeout=PAGE_TIMEOUT_MS,
                  wait_until="domcontentloaded")
        print("A Discord window is open. Sign in YOURSELF in that "
              "window (password, captcha, 2FA -- all yours), then click "
              "into the CBB Discord server and open #todd-picks.")
        print("Waiting up to 10 minutes for you to land on #todd-picks...")
        cfg = {}
        ok = False
        for _ in range(40):
            time.sleep(15)
            try:
                url = page.url or ""
                title = page.title() or ""
                m = CHANNEL_RE.search(url)
                if m and "todd-picks" in title.lower():
                    authors = page.evaluate(AUTHORS_JS)
                    top = sorted(authors.items(), key=lambda kv: -kv[1])
                    cfg = {
                        "guild_id": m.group(1),
                        "channel_id": m.group(2),
                        "channel_url": url.split("?")[0],
                        "channel_name": "todd-picks",
                        "server_name": "CBB Discord",
                        "todd_author": top[0][0] if top else None,
                        "authors_seen": authors,
                        "signed_in_at": now_et().isoformat(),
                    }
                    ok = True
                    break
                if m:
                    print("You are on a Discord channel, but not "
                          "#todd-picks yet -- click #todd-picks in the "
                          "left sidebar (Picks category, CBB Discord).")
            except Exception:
                pass
        verdict = ("signed in - #todd-picks verified"
                   if ok else "not signed in / #todd-picks not reached "
                              "after 10 minutes")
        print(f"Discord sign-in result: {verdict}")
        if ok:
            save_json(CONFIG_PATH, cfg)
            print(f"Config saved: {cfg['channel_url']} "
                  f"(Todd author: {cfg.get('todd_author')})")
        try:
            SIGNIN_RESULT.write_text(
                f"{now_et().isoformat()} {verdict}", encoding="utf-8")
        except OSError:
            pass
        ctx.close()
        return 0 if ok else 1


def do_check():
    """Headless check: is the profile signed in and does #todd-picks
    load? Exit 0 signed in, 1 otherwise."""
    from playwright.sync_api import sync_playwright

    cfg = load_config()
    url = cfg.get("channel_url") or DISCORD_APP_URL
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(DISCORD_PROFILE_DIR), headless=True,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.new_page()
        try:
            # 120s: first load of Discord's SPA in a cold profile can
            # exceed the 60s page timeout.
            page.goto(url, timeout=120000,
                      wait_until="domcontentloaded")
            # Settle: wait up to 30s for EITHER the channel message
            # list (signed in) OR the login form (not signed in).
            # Checking after a fixed 5s raced the SPA render and
            # wrongly reported "signed in" on a fresh profile.
            state = None
            for _ in range(15):
                time.sleep(2)
                try:
                    if page.locator(
                            'ol[data-list-id="chat-messages"]').count() > 0:
                        state = True
                        break
                except Exception:
                    pass
                if "/login" in (page.url or ""):
                    state = False
                    break
                try:
                    body = page.locator("body").inner_text(timeout=3000)
                    if "Log in to Discord" in body:
                        state = False
                        break
                except Exception:
                    pass
            if state is None:
                print("Discord session: undecided -- page did not "
                      "render in 30s")
                ctx.close()
                return 1
            if not state:
                print("Discord session: NOT signed in")
                ctx.close()
                return 1
            title = page.title() or ""
            on_channel = (("todd-picks" in title.lower())
                          if cfg.get("channel_url") else True)
            print(f"Discord session: signed in, "
                  f"#todd-picks loaded: {on_channel}")
            ctx.close()
            return 0 if on_channel else 1
        except Exception as exc:  # noqa: BLE001
            print(f"Discord session: check failed: {exc}")
            ctx.close()
            return 1


def watch_loop():
    """Persistent poll loop, every POLL_SECONDS. Never exits on a
    single failure -- it logs, backs off, and keeps going. The
    run-todd-watch.cmd wrapper relaunches it if the process dies."""
    from playwright.sync_api import sync_playwright

    cfg = load_config()
    if not cfg.get("channel_url"):
        log("FATAL: no channel configured -- run "
            "`py -3.12 todd_discord_watch.py --signin` at the PC first.")
        return 2
    state = load_state()
    todd_author = cfg.get("todd_author")
    log(f"watch starting: {cfg['channel_url']} "
        f"(Todd author: {todd_author or 'any author in channel'}) "
        f"poll {POLL_SECONDS}s, seen {len(state['seen'])} messages")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(DISCORD_PROFILE_DIR), headless=True,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.new_page()
        baselined = False
        while True:
            try:
                if not baselined or page.url != cfg["channel_url"]:
                    if not open_channel(page, cfg["channel_url"]):
                        if is_login_wall(page):
                            handle_session_lost(state)
                            time.sleep(300)
                            continue
                        log("channel did not load, retrying in 30s")
                        time.sleep(30)
                        continue
                page.evaluate(SCROLL_BOTTOM_JS)
                time.sleep(1.5)
                msgs = page.evaluate(READ_MESSAGES_JS)
                state["last_poll"] = now_et().isoformat()
                if not baselined:
                    for m in msgs:
                        state["seen"].append(m["message_id"])
                    save_state(state)
                    log(f"baselined {len(msgs)} messages already in "
                        f"channel (no alerts for these)")
                    baselined = True
                    time.sleep(POLL_SECONDS)
                    continue
                fresh = [m for m in msgs
                         if m["message_id"] not in set(state["seen"])]
                for m in fresh:
                    state["seen"].append(m["message_id"])
                    if not is_todd_message(m, todd_author):
                        log(f"skipped non-Todd message from "
                            f"{m.get('author') or '?'}: "
                            f"{m['text'][:80]}")
                        continue
                    handle_new_play(m, state)
                if fresh:
                    save_state(state)
                else:
                    save_state(state)
            except Exception as exc:  # noqa: BLE001 - loop must survive
                log(f"poll error ({exc}), continuing in 30s")
                try:
                    if page.is_closed():
                        page = ctx.new_page()
                        baselined = False
                except Exception:
                    pass
            time.sleep(POLL_SECONDS)


def handle_session_lost(state):
    """Session died (login wall). One ntfy an hour at most, then the
    loop backs off. Michael re-runs --signin at the keyboard."""
    log("SESSION LOST: Discord shows a login wall -- watcher paused. "
        "Re-run --signin at the PC to restore it.")
    last = state.get("last_session_lost_ntfy")
    now = now_et()
    send = True
    if last:
        try:
            prev = datetime.fromisoformat(last)
            send = (now - prev).total_seconds() > 3600
        except ValueError:
            send = True
    if send:
        ntfy_todd("Discord session lost — Todd watch paused",
                  "Your PC lost its Discord sign-in, so the Todd "
                  "watch is paused. Re-run the Discord sign-in at "
                  "the PC to restore it. No plays are being missed "
                  "silently -- the watcher will resume after sign-in.")
        state["last_session_lost_ntfy"] = now.isoformat()
        save_state(state)


def handle_new_play(msg, state):
    parsed = parse_play(msg.get("text", ""))
    play, md_path = bank_play(parsed, msg["message_id"],
                              msg.get("posted"))
    state["plays_logged"] = state.get("plays_logged", 0) + 1
    save_state(state)
    title = f"Todd play — {play_title(parsed)}"
    body_lines = [parsed.get("raw_text", "")]
    if parsed.get("price") is not None:
        body_lines.append(f"Price {parsed['price']}")
    if parsed.get("stake_text"):
        body_lines.append(f"Stake {parsed['stake_text']} (as stated)")
    body_lines.append(f"Posted {play['posted_et']} ET")
    body_lines.append("Tap to open Novig, search the game, confirm "
                      "stake, place it.")
    body = "\n".join(body_lines)
    ok = ntfy_todd(title, body)
    log(f"NEW PLAY {play['id']}: {play_title(parsed)} "
        f"price={parsed.get('price')} stake={parsed.get('stake_text')} "
        f"ntfy={'sent' if ok else 'FAILED'} msg={msg['message_id']}")
    push_delivery_file(md_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signin", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    DATA_DIR.mkdir(exist_ok=True)
    if args.signin:
        return do_signin()
    if args.check:
        return do_check()
    return watch_loop()


if __name__ == "__main__":
    sys.exit(main())
