#!/usr/bin/env python3
"""In-play NCAA basketball watcher -- Todd mimic (added 2026-11-24
at Michael's tap: "Build the full Todd mimic (incl. 1H)").

Watches live NCAAB games and flags the in-play total plays Todd
himself takes, using the pace model back-derived from his #todd-picks
history (todd-model-report-2026-11-23.md) PLUS the rules from his
own Nov 24 recap. Paper only -- Michael places every bet himself.
This script only alerts + logs; it never bets.

PROBE RESULT (2026-11-24 09:47 ET, probe_sao_live_fields.py on the
PC): ScoresAndOdds' live NCAAB board carries a LIVE FULL-GAME TOTAL
(data-field="live-total", over/under + price) but NO live 1st-half
total and NO live team total anywhere on the page (0 hits for
"1st Half", "Team Total", "live-1h", "live-team-total"). There is no
free public source for a live 1H total line, so the 1H rules below
are DOCUMENTED BUT NOT ACTIVE -- the watcher fires halftime plays
only, which is also where Todd's money was (full-game 7-2 +$413.13,
at-halftime 4-1 +$246.84; 1H picks 9-12 -$421.06, the leak).

RULES (all fire at halftime, once per game, first match wins):
  1. TRAILING FAVE (override): pregame favorite trailing at half
     -> bet OVER anyway, even if pace says Under. (Boise State, a
     12.5-pt favorite trailing at half, H1 63 -> proj 126 vs line
     151.5 -- Todd bet OVER and won; BYU at the half Nov 24, "exactly
     how u want the comeback fave team to play out", road favorite
     still counts.)
  2. 2ND BITE: the 1H total cleared its implied line (pregame total
     / 2) by 5+ points AND the pace projection does not contradict
     the Over (H1 x 2 >= live total) -> bet the full-game OVER again
     at the half. (Arkansas State: he regrets NOT taking it; Santa
     Clara: he took it "for a 2nd bite and another eaaaasy W".)
  3. PACE: projection = H1 total x 2 vs the live full-game total.
     Alert Over when proj - live >= 5, Under when live - proj >= 5.
     (His at-halftime unders were on 8.5-18.5 pt edges; 1H overs he
     took on ~2-pt edges, but 1H is not active -- see above.)
  OVER TEAM tag/lift (BartTorvik proxy for KenPom -- KenPom is
  paywalled; the Total Projectors scan already uses BartTorvik):
  a team with adjO rank <= 75 AND adjD rank >= 90 is an over team
  (Santa Clara: KenPom offense 51, defense 133 of 365; Torvik on
  2026-11-24 gives adjO 70 / adjD 96, so the proxy defense bar is 90
  rather than the spec's ~100 so the anchor team actually tags).
  When either team in the game fits, the alert carries the OVER TEAM
  tag and the PACE Over threshold drops 5 -> 2.5.
  Elite-offense note (Illinois 1H team-total Over -- "this team
  isn't a real team its a video game"): team totals are NOT on the
  SAO board, so no TT flag is possible; a top-30 adjO team still
  qualifies for the OVER TEAM tag when its adjD is 90+ (Torvik proxy).

NOT IMPLEMENTED (and why):
  - 1H game totals: no live 1H total line on any free public source
    (SAO probe above). NO-CHASE (Arizona: liked it at the under-8
    timeout, passed, bet late and barely lost -- "u missed it just
    let it go") and WAIT-FOR-PACE (Siena: no bets before the under-16
    timeout) are 1H rules and do not apply to halftime plays.
  - 1H/2H team totals (Illinois TT): no live TT line on SAO.
  - "Undisciplined teams" (UNLV: "deserved to lose betting on an
    undisciplined bunch"): no reliable data source -- no proxy invented.
  - Pace can deceive (NC: a very fast 1H is not an automatic open
    gym): no booster beyond the projection itself -- the projection
    IS the rule, nothing is added on top of it.

DATA:
  - ESPN NCAAB scoreboard API (groups=50): live score/clock,
    halftime H1 linescores, pregame spread/total snapshot while
    scheduled. A game whose pregame line was never captured is NEVER
    judged (no invented lines, same fail-safe as upset_watch).
  - ScoresAndOdds NCAAB board, fetched ONLY on runs where a game is
    at halftime: live full-game total + price (data-field=
    "live-total"). A halftime game with no readable live total is
    skipped that cycle -- never alerted without a line.
  - BartTorvik T-Rank (cached daily to data/torvik-ranks-YYYY-MM-DD
    .json; fetch_torvik copied from total_projectors_scan.py): adjOE/
    adjDE -> computed ranks. A failed Torvik fetch never blocks
    alerts; the game simply carries no profile tag that run.

DELIVERY (mirrors todd_discord_watch.py): each new flag is banked to
data/inplay-cbb-log.json (same shape as the Todd log, ids INP-001...,
$100-flat paper, graded next morning by grade_inplay_cbb.py), written
to the dated delivery file data/inplay-cbb-YYYY-MM-DD.md (pushed to
GitHub immediately, one retry), and pushed to ntfy instantly (Title
+ Click headers; _latin1_safe on the Title -- the em-dash bug that
killed every Todd push until 2026-11-24). The LOG is also pushed to
GitHub immediately on every new play (push_log_file) -- the regular
sync cannot be relied on for it: it pushes ~1,146 files per run at
~1.5s each (~29 min) and the scheduler kills it at 30 min, so
tier-3 tail files starve; pc-data/todd-picks-log.json 404s on GitHub
for exactly this reason (found 2026-11-24). Runs as a single poll per
invocation under its own 2-minute lane task (pc-inplay-cbb-lane ->
inplay_cbb_lane.py), like the upset watch lane.

--probe: print what WOULD fire this run without banking, alerting,
or pushing (safe live test).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DATA_DIR  # noqa: E402
from github_push import put_file  # noqa: E402

NOVIG_LINK = "https://app.novig.us/AppEntry"
ESPN_NCAAB = ("https://site.api.espn.com/apis/site/v2/sports/basketball/"
              "mens-college-basketball/scoreboard?groups=50&dates={date}")
# NOTE: the bare scoreboard URL (no dates=) returns YESTERDAY's finals
# in the morning (probe 2026-11-24 ~10:00 ET: 46 events, ALL final).
# The pregame snapshot must land before tip, so always fetch with an
# explicit dates=YYYYMMDD for today.
SAO_NCAAB = "https://www.scoresandodds.com/ncaab"
TORVIK = "https://barttorvik.com/trank.php?year={year}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

STATE_PATH = DATA_DIR / "inplay-cbb-watch-state.json"
LOG_PATH = DATA_DIR / "inplay-cbb-log.json"

PACE_THRESHOLD = 5.0        # halftime pace gap (points) to flag
PACE_THRESHOLD_OVER_TEAM = 2.5  # PACE Over bar when OVER TEAM tag applies
SECOND_BITE_CLEAR = 5.0     # H1 points over implied 1H line (pregame/2)
OVER_TEAM_ADJO_MAX = 75     # adjO rank <= 75 ...
OVER_TEAM_ADJD_MIN = 90     # ... AND adjD rank >= 90 on the TORVIK proxy.
# (Spec said ~100 from KenPom; the anchor team Santa Clara is KenPom
# def 133 but Torvik def 96 on 2026-11-24, so the proxy bar is 90 so
# the team the rule was written about actually tags. Illinois adjO #1
# / adjD #21 correctly does NOT tag -- elite O but good D.)


def now_et():
    """PC local time == ET (the PC lives in Cherry Hill, Eastern)."""
    return datetime.now()


def today_str():
    return now_et().strftime("%Y-%m-%d")


def log(msg):
    print(msg, flush=True)


# ------------------------------------------------------------------ fetch

def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


# ------------------------------------------------------------------ names

def norm_team(name):
    n = (name or "").lower().strip()
    n = n.replace("&", "and").replace(".", "").replace("'", "")
    n = re.sub(r"\s+", " ", n)
    if n.endswith(" st"):
        n = n[:-3] + " state"
    return {"uconn": "connecticut"}.get(n, n)


def norm_torvik(name):
    toks = re.sub(r"[^a-z0-9]", " ", (name or "").lower()).split()
    return " ".join("st" if t == "state" else t for t in toks)


def parse_price(text):
    t = (text or "").strip().lower()
    if t == "even":
        return 100
    try:
        return int(t.replace("+", ""))
    except ValueError:
        return None


def parse_total(text):
    """'o155.5 -105' / 'u155.5 -115' -> (155.5, price) or (None, None)."""
    m = re.search(r"[ou]\s*(\d+(?:\.\d+)?)\s*([+-]?\d+)?",
                  (text or "").strip().lower())
    if not m:
        return None, None
    try:
        total = float(m.group(1))
    except ValueError:
        return None, None
    price = parse_price(m.group(2)) if m.group(2) else None
    return total, price


def _first_data_value(td_html):
    m = re.search(r'<span class="data-value">\s*([^<]+?)\s*</span>', td_html)
    return m.group(1) if m else None


def sao_live_totals(html):
    """Map normalized team name -> {"total": float, "over": price|None,
    "under": price|None} from the ScoresAndOdds live board, read from
    the SAME page fetch. Rows without a readable live total are absent.
    Each team's row carries the same game total, so a game is found
    through either team."""
    out = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        td = re.search(r'<td[^>]*data-field="live-total"[^>]*>(.*?)</td>',
                       row, re.S)
        if not td:
            continue
        name_m = re.search(r'aria-label="([^"]+)"', row)
        if not name_m:
            continue
        vals = re.findall(r'<span class="data-value">\s*([^<]+?)\s*</span>',
                          td.group(1))
        total, over_p, under_p = None, None, None
        for v in vals:
            t, p = parse_total(v)
            if t is None:
                continue
            total = t
            if v.strip().lower().startswith("o"):
                over_p = p
            elif v.strip().lower().startswith("u"):
                under_p = p
        if total is None:
            continue
        out[norm_team(name_m.group(1))] = {
            "total": total, "over": over_p, "under": under_p}
    return out


# ------------------------------------------------------------------ torvik

class _TableParser(HTMLParser):
    """Copied from total_projectors_scan.py (same BartTorvik parse)."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.cur = None
        self.cell = None
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cur = []
        elif tag in ("td", "th") and self.cur is not None:
            self.cell = ""
        elif tag == "span" and self.cell is not None:
            cls = dict(attrs).get("class", "")
            if "lowrow" in cls:
                self.skip += 1

    def handle_data(self, data):
        if self.cell is not None and not self.skip:
            self.cell += data

    def handle_endtag(self, tag):
        if tag == "span" and self.skip:
            self.skip -= 1
        elif tag in ("td", "th") and self.cur is not None and self.cell is not None:
            self.cur.append(re.sub(r"\s+", " ", self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.cur is not None:
            if self.cur:
                self.rows.append(self.cur)
            self.cur = None


def _parse_tables(html_text):
    p = _TableParser()
    p.feed(html_text)
    return p.rows


def torvik_season_year(date_str):
    d = datetime.fromisoformat(date_str).date()
    return d.year + 1 if d.month >= 10 else d.year


def fetch_torvik(year):
    """BartTorvik sits behind a JS-check form post; satisfy it with a
    cookie jar (GET -> POST js_test_submitted=1 -> GET). Copied from
    total_projectors_scan.py."""
    import http.cookiejar
    import urllib.parse
    url = TORVIK.format(year=year)
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj))

    def go(req_url, data=None):
        req = urllib.request.Request(req_url, data=data,
                                     headers={"User-Agent": UA})
        with opener.open(req, timeout=45) as resp:
            return resp.read().decode("utf-8", "replace")

    go(url)
    try:
        go(url, urllib.parse.urlencode(
            {"js_test_submitted": "1"}).encode())
    except Exception:
        pass
    html_text = go(url)
    rows = _parse_tables(html_text)
    hdr = None
    for i, r in enumerate(rows):
        if len(r) > 10 and r[0] == "Rk" and r[1] == "Team":
            hdr = i
            break
    if hdr is None:
        raise ValueError("BartTorvik table header not found")
    header = rows[hdr]
    idx = {name: header.index(name)
           for name in ("Team", "G", "AdjOE", "AdjDE", "Adj T.")}
    teams = {}
    for r in rows[hdr + 1:]:
        if len(r) <= max(idx.values()):
            continue
        try:
            teams[r[idx["Team"]]] = {
                "games": int(r[idx["G"]]),
                "adj_oe": float(r[idx["AdjOE"]]),
                "adj_de": float(r[idx["AdjDE"]]),
                "adj_t": float(r[idx["Adj T."]]),
            }
        except (TypeError, ValueError):
            continue
    if len(teams) < 300:
        raise ValueError(f"BartTorvik parse found only {len(teams)} teams")
    return teams


def load_torvik_ranks():
    """Return {norm_name: {"adjo_rank": int, "adjd_rank": int}} from
    today's cache, fetching + caching on a miss. Never raises -- a
    failed fetch returns {} (games simply carry no profile tag)."""
    cache = DATA_DIR / f"torvik-ranks-{today_str()}.json"
    try:
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        teams = fetch_torvik(torvik_season_year(today_str()))
    except Exception as exc:  # noqa: BLE001 - tags are optional
        log(f"TORVIK FAIL: {exc} -- continuing without profile tags")
        return {}
    by_oe = sorted(teams.items(), key=lambda kv: -kv[1]["adj_oe"])
    by_de = sorted(teams.items(), key=lambda kv: kv[1]["adj_de"])
    ranks = {}
    for i, (name, _) in enumerate(by_oe, start=1):
        ranks.setdefault(norm_torvik(name), {})["adjo_rank"] = i
    for i, (name, _) in enumerate(by_de, start=1):
        ranks.setdefault(norm_torvik(name), {})["adjd_rank"] = i
    try:
        cache.write_text(json.dumps(ranks), encoding="utf-8")
    except OSError:
        pass
    log(f"TORVIK: {len(ranks)} teams ranked and cached")
    return ranks


def over_team_tag(ranks, home_name, away_name):
    """True if either team fits the over-team profile (adjO rank
    <= 75 AND adjD rank >= 90, Torvik proxy). Returns (tag_bool, detail_str)."""
    details = []
    for nm in (home_name, away_name):
        r = ranks.get(norm_torvik(nm)) or {}
        ao, ad = r.get("adjo_rank"), r.get("adjd_rank")
        if ao is not None and ad is not None:
            details.append(f"{nm} adjO#{ao}/adjD#{ad}")
            if ao <= OVER_TEAM_ADJO_MAX and ad >= OVER_TEAM_ADJD_MIN:
                return True, "; ".join(details)
    return False, "; ".join(details)


# ------------------------------------------------------------------ ntfy

def _latin1_safe(s):
    """HTTP headers must be latin-1; play titles use an em dash
    (U+2014), which crashed every ntfy push with a latin-1 codec
    error (found 2026-11-24: no Todd phone alert had ever gone out).
    Same fix as todd_discord_watch.py -- reuse exactly."""
    return (s.replace("\u2014", "-").replace("\u2013", "-")
             .encode("latin-1", "replace").decode("latin-1"))


def ntfy_push(title, body):
    """Instant phone alert. Never raises. Returns True if sent."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        log("ntfy: NTFY_TOPIC not set, cannot push alert")
        return False
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
            method="POST",
            headers={"Title": _latin1_safe(title), "Click": NOVIG_LINK,
                     "Priority": "high", "Tags": "basketball",
                     "Content-Type": "text/plain"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return True
    except Exception as exc:  # noqa: BLE001 - alert path must not die
        log(f"ntfy: push failed ({exc})")
        return False


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


def next_play_id(log_obj):
    n = len(log_obj.get("plays", [])) + 1
    return f"INP-{n:03d}"


def bank_play(play):
    """Append to the tracker log + the dated delivery file. Returns
    the play dict (with id) and the md path."""
    log_obj = load_json(LOG_PATH, {"plays": []})
    log_obj.setdefault("plays", [])
    play["id"] = next_play_id(log_obj)
    log_obj["plays"].append(play)
    save_json(LOG_PATH, log_obj)
    md_path = DATA_DIR / f"inplay-cbb-{today_str()}.md"
    header = (f"# In-play CBB (Todd mimic) — {today_str()} "
              f"(PC watch, live NCAAB)\n")
    section = (f"## {now_et():%Y-%m-%d %H:%M:%S} ET — 1 NEW in-play flag\n"
               f"NEW IN-PLAY CBB: {play['raw_text']}\n")
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
    ok, detail = put_file(repo_path, md_path, f"In-play CBB {today_str()}")
    if ok:
        log(f"github: pushed {repo_path} ({detail})")
        return True
    log(f"github: push failed ({detail}), retrying once in 15s")
    time.sleep(15)
    ok, detail = put_file(repo_path, md_path, f"In-play CBB {today_str()}")
    log(f"github: retry {'ok (' + detail + ')' if ok else 'failed (' + detail + ')'}")
    return ok


def push_log_file():
    """Push the tracker log to GitHub IMMEDIATELY on every new play.
    The regular sync CANNOT be relied on for this file (found
    2026-11-24: pc-data/todd-picks-log.json 404s on GitHub -- the
    sync pushes ~1,146 files per run at ~1.5s each, ~29 min, and the
    scheduler kills it at 30 min, so tier-3 tail files like
    todd-picks-log.json starve whenever a run is slow or dies early;
    the Todd log was born Nov 24 09:15 and no sync run since has
    lived long enough to reach it). The morning grader reads the log
    from GitHub, so without this push it would see nothing. One
    retry after 15s; failure is quiet -- the sync remains the
    backstop."""
    ok, detail = put_file("pc-data/inplay-cbb-log.json", LOG_PATH,
                          f"In-play CBB log {today_str()}")
    if ok:
        log(f"github: pushed pc-data/inplay-cbb-log.json ({detail})")
        return True
    log(f"github: log push failed ({detail}), retrying once in 15s")
    time.sleep(15)
    ok, detail = put_file("pc-data/inplay-cbb-log.json", LOG_PATH,
                          f"In-play CBB log {today_str()}")
    log(f"github: log retry {'ok (' + detail + ')' if ok else 'failed (' + detail + ')'}")
    return ok


# ------------------------------------------------------------------ espn

def parse_game(event):
    comp = event["competitions"][0]
    status = comp["status"]["type"]["name"]
    detail = comp["status"]["type"].get("detail", "")
    competitors = {c["homeAway"]: c for c in comp["competitors"]}
    home, away = competitors["home"], competitors["away"]
    game = {
        "id": event["id"],
        "matchup": (f"{away['team']['shortDisplayName']} at "
                    f"{home['team']['shortDisplayName']}"),
        "home": home["team"]["shortDisplayName"],
        "away": away["team"]["shortDisplayName"],
        "home_score": home.get("score"),
        "away_score": away.get("score"),
        "status": status,
        "detail": detail,
        "date": event.get("date", "")[:10],
    }
    # halftime H1 points from linescores; at halftime the score
    # fields equal the H1 totals, used as fallback.
    for side, comp_side in (("home", home), ("away", away)):
        lines = comp_side.get("linescores") or []
        h1 = None
        if lines:
            try:
                h1 = int(lines[0].get("value"))
            except (TypeError, ValueError, IndexError):
                h1 = None
        game[f"{side}_h1"] = h1
    odds = comp.get("odds") or []
    if odds:
        o = odds[0]
        fav = None
        if o.get("awayTeamOdds", {}).get("favorite"):
            fav = "away"
        elif o.get("homeTeamOdds", {}).get("favorite"):
            fav = "home"
        spread = o.get("spread")
        if fav is None and isinstance(spread, (int, float)):
            fav = "home" if spread < 0 else "away"
        if fav and isinstance(spread, (int, float)):
            game["fav"] = fav
            game["spread"] = abs(float(spread))
        total = o.get("overUnder")
        if isinstance(total, (int, float)):
            game["pregame_total"] = float(total)
    return game


def is_halftime(game):
    detail = (game.get("detail") or "").lower()
    return ("halftime" in detail
            or game.get("status") == "STATUS_HALFTIME")


def h1_total(game):
    """Combined H1 points, or None if either half-score is missing."""
    hh, ah = game.get("home_h1"), game.get("away_h1")
    if hh is not None and ah is not None:
        return hh + ah
    try:
        return int(game["home_score"]) + int(game["away_score"])
    except (TypeError, ValueError, KeyError):
        return None


# ------------------------------------------------------------------ rules

def evaluate_halftime(rec, game, live, ranks):
    """Return the flag dict for a game at halftime, or None. First
    matching rule wins (TRAILING FAVE > 2ND BITE > PACE)."""
    h1 = h1_total(game)
    if h1 is None:
        return None
    live_total = live["total"]
    proj = h1 * 2.0
    gap = proj - live_total  # >0: pace points Over
    is_over_team, profile = over_team_tag(ranks, game["home"], game["away"])
    fav_team = rec.get("fav_team")
    dog_team = rec.get("dog_team")
    fav_trailing = False
    if fav_team and dog_team:
        try:
            fav_score = int(game["home_score"] if rec["fav"] == "home"
                            else game["away_score"])
            dog_score = int(game["away_score"] if rec["fav"] == "home"
                            else game["home_score"])
            fav_trailing = fav_score < dog_score
        except (TypeError, ValueError):
            fav_trailing = False
    base = {
        "game": game["matchup"],
        "market": "Full Total (at half)",
        "line": live_total,
        "h1_total": h1,
        "pace_proj": proj,
        "pregame_total": rec.get("pregame_total"),
        "fav_team": fav_team,
        "dog_team": dog_team,
        "fav_trailing": fav_trailing,
        "over_team": is_over_team,
        "profile": profile,
        "detail": game["detail"],
    }
    # Rule 1: TRAILING FAVE -- pregame favorite trailing at half
    # bets OVER anyway, even if pace says Under.
    if fav_trailing:
        base.update({
            "rule": "TRAILING FAVE",
            "side": "Over",
            "price": live.get("over"),
            "reason": (f"pregame favorite {fav_team} trails at half "
                       f"({dog_team} leads); pace proj {proj:.1f} vs live "
                       f"total {live_total:g} (gap {gap:+.1f}) -- "
                       f"comeback-fave Over anyway"),
        })
        return base
    # Rule 2: 2ND BITE -- 1H flew over its implied line by 5+ and
    # pace does not contradict the Over (proj >= live total).
    pre = rec.get("pregame_total")
    if pre is not None and h1 - pre / 2.0 >= SECOND_BITE_CLEAR and gap >= 0:
        base.update({
            "rule": "2ND BITE",
            "side": "Over",
            "price": live.get("over"),
            "reason": (f"1H total {h1} cleared implied 1H line "
                       f"{pre / 2.0:g} by {h1 - pre / 2.0:+.1f}; pace proj "
                       f"{proj:.1f} vs live total {live_total:g} -- "
                       f"take the Over again at the half"),
        })
        return base
    # Rule 3: PACE -- proj vs live total; Over bar drops 5 -> 2.5
    # when the OVER TEAM tag applies.
    over_bar = (PACE_THRESHOLD_OVER_TEAM if is_over_team
                else PACE_THRESHOLD)
    if gap >= over_bar:
        base.update({
            "rule": "PACE" + (" + OVER TEAM" if is_over_team else ""),
            "side": "Over",
            "price": live.get("over"),
            "reason": (f"pace proj {proj:.1f} (H1 {h1} x 2) vs live total "
                       f"{live_total:g} (gap {gap:+.1f}, bar {over_bar:g})"
                       + (f" -- OVER TEAM: {profile}" if is_over_team else "")),
        })
        return base
    if -gap >= PACE_THRESHOLD:
        base.update({
            "rule": "PACE",
            "side": "Under",
            "price": live.get("under"),
            "reason": (f"pace proj {proj:.1f} (H1 {h1} x 2) vs live total "
                       f"{live_total:g} (gap {gap:+.1f}, bar "
                       f"{PACE_THRESHOLD:g})"),
        })
        return base
    return None


# ------------------------------------------------------------------ main

def main():
    probe_only = "--probe" in sys.argv
    state = load_json(STATE_PATH, {"games": {}})
    games = state.setdefault("games", {})
    try:
        data = fetch_json(ESPN_NCAAB.format(
            date=now_et().strftime("%Y%m%d")))
    except Exception as exc:
        print(f"FEED FAIL ESPN NCAAB: {exc}", file=sys.stderr)
        return 0
    board_at = now_et()
    seen = 0
    halftime_games = []
    for event in data.get("events", []):
        g = parse_game(event)
        seen += 1
        rec = games.setdefault(g["id"], {
            "matchup": g["matchup"], "date": g["date"],
            "alerted": False,
        })
        if g["status"] == "STATUS_SCHEDULED":
            if g.get("fav") and g.get("spread"):
                rec["fav"] = g["fav"]
                rec["spread"] = g["spread"]
                rec["fav_team"] = (g["home"] if g["fav"] == "home"
                                   else g["away"])
                rec["dog_team"] = (g["away"] if g["fav"] == "home"
                                   else g["home"])
            if g.get("pregame_total") is not None:
                rec["pregame_total"] = g["pregame_total"]
        elif is_halftime(g):
            if not rec.get("alerted") and rec.get("fav"):
                halftime_games.append((rec, g))
            elif not rec.get("fav"):
                log(f"SKIP {g['matchup']} | at half but no pregame "
                    f"snapshot (never judged -- no invented lines)")
        elif g["status"] in ("STATUS_FINAL", "STATUS_CANCELED"):
            rec["final"] = True
    triggers = []
    if halftime_games:
        # Torvik ranks only matter when a game is at halftime.
        ranks = load_torvik_ranks()
        try:
            board = sao_live_totals(fetch_text(SAO_NCAAB))
        except Exception as exc:
            print(f"SAO FAIL: {exc} -- halftime games skipped this run",
                  file=sys.stderr)
            board = None
        sao_at = now_et()
        if board is not None:
            for rec, g in halftime_games:
                live = (board.get(norm_team(g["home"]))
                        or board.get(norm_team(g["away"])))
                if live is None:
                    log(f"SKIP {g['matchup']} | at half but no live "
                        f"total on SAO (never alerted without a line)")
                    continue
                flag = evaluate_halftime(rec, g, live, ranks)
                if flag is None:
                    h1v = h1_total(g)
                    log(f"NO FLAG {g['matchup']} | H1 {h1v} "
                        f"proj {(h1v * 2.0 if h1v is not None else float('nan')):.1f} vs live "
                        f"{live['total']:g} (inside bars / no H1)")
                    continue
                flag["date"] = today_str()
                flag["posted_et"] = board_at.strftime("%Y-%m-%d %H:%M ET")
                flag["board_read_at"] = board_at.isoformat(timespec="seconds")
                flag["sao_read_at"] = sao_at.isoformat(timespec="seconds")
                flag["status"] = "pending"
                flag["result"] = None
                flag["pl"] = None
                flag["final"] = None
                flag["final_value"] = None
                flag["source"] = "In-play CBB watch (Todd mimic)"
                price_txt = (f" {flag['price']:+d}"
                             if flag.get("price") is not None else "")
                flag["raw_text"] = (
                    f"{flag['rule']}: {g['matchup']} | Full Total (at half) "
                    f"{flag['side']} {flag['line']:g}{price_txt} | "
                    f"{flag['reason']} | H1 {flag['h1_total']} | "
                    f"as of {board_at:%H:%M:%S} ET")
                if probe_only:
                    triggers.append(f"PROBE: {flag['raw_text']}")
                    continue
                play, md_path = bank_play(flag)
                rec["alerted"] = True
                rec["alerted_at"] = now_et().isoformat(timespec="seconds")
                rec["alert_rule"] = flag["rule"]
                rec["alert_detail"] = flag["raw_text"]
                title = (f"In-play CBB — {g['matchup']} "
                         f"{flag['side']} {flag['line']:g}")
                body = (flag["raw_text"]
                        + "\nTap to open Novig, search the game, "
                          "confirm stake, place it.")
                ok = ntfy_push(title, body)
                log(f"NEW FLAG {play['id']}: {flag['raw_text']} "
                    f"ntfy={'sent' if ok else 'FAILED'}")
                triggers.append(f"TRIGGER: {flag['raw_text']}")
                push_delivery_file(md_path)
                push_log_file()
    # Prune games from before yesterday (keeps the file small; finals
    # stay out of future checks either way).
    cutoff = (now_et().date().isoformat())
    for gid in list(games):
        if games[gid].get("date", "") < cutoff and games[gid].get("final"):
            games.pop(gid, None)
    if not probe_only:
        save_json(STATE_PATH, state)
    for t in triggers:
        print(t)
    if not triggers:
        print("NO TRIGGER")
    print(f"SUMMARY: {seen} games checked, "
          f"{len(halftime_games)} at halftime, "
          f"{len(triggers)} new flag(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
