#!/usr/bin/env python3
"""Big-favorite-live-losing watch (NFL / college football / NBA / NCAAB).

Polls ESPN's public scoreboards. Pregame spreads exist on the scoreboard
only while a game is SCHEDULED — they vanish once it goes live — so this
script snapshots each scheduled game's spread first, then judges live
games against that frozen pregame line. A game whose pregame spread was
never captured (odds missing, or the script started mid-game) is NEVER
judged: no invented lines, ever.

Football trigger: pregame favorite laying 10.5+ points is strictly
TRAILING on the live scoreboard, and this game has not alerted before.
One alert per game (a favorite that goes back ahead and trails again
does not re-fire).

Basketball trigger (NBA + NCAAB; changed 2026-11-05 at Michael's tap):
the same trailing condition, PLUS the favorite's LIVE moneyline must be
-200 or better (bettor-friendly: -200, -180, any plus price) at the
moment of the check. ESPN's basketball scoreboards carry no usable live
moneyline (live NCAAB games have no odds object at all), so the live ML
is read from ScoresAndOdds' public board — fetched ONLY on runs where a
basketball candidate exists. If the live ML cannot be read for a game,
that game does not alert: never an invented price. One alert per game,
fired on the first check where BOTH conditions hold.

Freshness / read-correctness hardening (2026-11-06, after the ETSU at
NC State alert reached chat ~10 minutes after the scan read it, by
which time NC State had retaken the lead; the -120 ML in that alert was
read in the same cycle as the 52-45 score it was paired with at scan
time, but the delivered text paired it with a newer score):
- The ML is fetched in the SAME run as the score that triggered the
  check (never cached across runs), and every trigger is stamped: the
  state record and the TRIGGER line carry the board-read time, the ML
  read time, and an alert_expires_at (+3 min basketball, +5 min
  football) so the delivery layer can show the data's age and drop
  alerts that arrive stale.
- Sanity hold: before a basketball alert fires, the read ML is cross-
  checked against the SAME ScoresAndOdds board's live spread for the
  favorite and against the score/clock. A wildly inconsistent read
  (e.g. ML says near pick'em while the same board prices the favorite
  as a 4.5+ point live underdog) HOLDS the alert — the game is not
  marked alerted, the reason is logged, and a later consistent cycle
  can still fire it.
- Re-verification: a passing basketball candidate is re-checked against
  a fresh ESPN fetch of that sport's board before the alert emits. If
  the favorite is no longer trailing (or the game is over), the alert
  is suppressed for this cycle instead of firing dead.

Output: one "TRIGGER: ..." line per new trigger, then a SUMMARY line.
No triggers -> prints "NO TRIGGER" (the cron stays silent on that).
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR
STATE_PATH = DATA_DIR / "upset-watch-state.json"
THRESHOLD = 10.5
ML_GATE = -200  # basketball: live ML must be -200 or better (bettor-friendly)
ALERT_TTL_MIN = {"basketball": 3, "football": 5}  # delivery freshness window

SPORTS = [
    ("NFL", "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"),
    # ESPN's default CFB scoreboard silently omits some games (found
    # 2026-11-04: Toledo at Sacramento St, an FBS-at-FCS game, was missing
    # from the default board all game, so its upset trigger never fired).
    # Fetch both Division I groups explicitly: 80 = FBS, 81 = FCS.
    ("CFB", "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=80"),
    ("CFB", "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=81"),
    ("NBA", "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"),
    # NCAAB added 2026-11-05 with the basketball ML-gate tweak. groups=50
    # is ESPN's Division I group (the board the closing-lines work uses).
    ("NCAAB", "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?groups=50"),
]

BASKETBALL = {"NBA", "NCAAB"}
SAO_URLS = {
    "NBA": "https://www.scoresandodds.com/nba",
    "NCAAB": "https://www.scoresandodds.com/ncaab",
}
ESPN_URLS = {sport: url for sport, url in SPORTS}


def now_et():
    """PC local time; the PC lives in Cherry Hill on Eastern Time and
    the scheduler stamps its log in ET, so local naive time is ET."""
    return datetime.now()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def fetch_text(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def norm_team(name):
    n = name.lower().strip()
    n = n.replace("&", "and").replace(".", "").replace("'", "")
    n = re.sub(r"\s+", " ", n)
    if n.endswith(" st"):
        n = n[:-3] + " state"
    return {"uconn": "connecticut"}.get(n, n)


def parse_price(text):
    t = text.strip().lower()
    if t == "even":
        return 100
    try:
        return int(t.replace("+", ""))
    except ValueError:
        return None


def parse_spread(text):
    t = text.strip().lower()
    if t in ("even", "pk", "pick", "pick'em"):
        return 0.0
    try:
        return float(t.replace("+", ""))
    except ValueError:
        return None


def _first_data_value(td_html):
    m = re.search(r'<span class="data-value">\s*([^<]+?)\s*</span>', td_html)
    return m.group(1) if m else None


def sao_live_board(html):
    """Map normalized team name -> {"ml": int|None, "spread": float|None}
    from a ScoresAndOdds board page, all read from the SAME page fetch.
    Rows without a readable live ML are simply absent from the map."""
    out = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        td = re.search(r'<td[^>]*data-field="live-moneyline"[^>]*>(.*?)</td>',
                       row, re.S)
        if not td:
            continue
        name_m = re.search(r'aria-label="([^"]+)"', row)
        val_m = re.search(r'<span class="data-value">\s*([^<]+?)\s*</span>',
                          td.group(1))
        if not name_m or not val_m:
            continue
        price = parse_price(val_m.group(1))
        if price is None:
            continue
        spread = None
        std = re.search(r'<td[^>]*data-field="live-spread"[^>]*>(.*?)</td>',
                        row, re.S)
        if std:
            sval = _first_data_value(std.group(1))
            if sval is not None:
                spread = parse_spread(sval)
        out[norm_team(name_m.group(1))] = {"ml": price, "spread": spread}
    return out


def sao_live_moneylines(html):
    """Back-compat wrapper: team -> live moneyline only."""
    return {team: v["ml"] for team, v in sao_live_board(html).items()}


def seconds_left_in_game(detail):
    """Seconds remaining in a basketball game from an ESPN status detail
    like '11:34 - 2nd Half' or '5:23 - 4th Quarter'. None if unparseable."""
    m = re.match(r"\s*(\d+):(\d+)\s*-\s*(.+?)\s*$", detail or "")
    if not m:
        return None
    clock = int(m.group(1)) * 60 + int(m.group(2))
    label = m.group(3).lower()
    if "1st half" in label:
        return clock + 20 * 60
    if "2nd half" in label:
        return clock
    qm = re.search(r"(\d)(?:st|nd|rd|th) quarter", label)
    if qm:
        return clock + (4 - int(qm.group(1))) * 12 * 60
    if "overtime" in label or label == "ot":
        return clock
    return None


def ml_sanity_hold(ml, live_spread, deficit, secs_left):
    """Return a hold reason if the read ML is wildly inconsistent with
    the same board's live spread or with the score/clock, else None.
    Brackets are deliberately extreme — this catches wrong-row and
    stale-board reads, it does not second-guess ordinary market moves."""
    if live_spread is not None:
        if live_spread >= 4.5 and ml <= -110:
            return (f"live ML {ml:+d} inconsistent with the same board's "
                    f"live spread {live_spread:+g} (favorite is now a live "
                    f"underdog there)")
        if (live_spread <= -9.5 and deficit >= 4
                and secs_left is not None and secs_left <= 10 * 60):
            return (f"live spread {live_spread:+g} (favorite still laying "
                    f"9.5+) contradicts the scoreboard showing the favorite "
                    f"trailing late")
    if secs_left is not None:
        if deficit >= 10 and secs_left <= 5 * 60 and ml <= -150:
            return (f"live ML {ml:+d} implausible for a favorite down "
                    f"{deficit} with {secs_left // 60}:{secs_left % 60:02d} left")
        if deficit >= 6 and secs_left <= 2 * 60 and ml <= -175:
            return (f"live ML {ml:+d} implausible for a favorite down "
                    f"{deficit} with {secs_left // 60}:{secs_left % 60:02d} left")
    return None


def stamp_alert(rec, kind, board_at, detail, fav_score, dog_score,
                ml=None, ml_at=None):
    """Record freshness stamps for an emitted alert."""
    emit = now_et()
    rec["alerted"] = True
    rec["alerted_at"] = emit.isoformat(timespec="seconds")
    rec["board_read_at"] = board_at.isoformat(timespec="seconds")
    rec["alert_expires_at"] = (
        emit + timedelta(minutes=ALERT_TTL_MIN[kind])).isoformat(
            timespec="seconds")
    rec["alert_detail"] = detail
    rec["alert_fav_score"] = fav_score
    rec["alert_dog_score"] = dog_score
    if ml is not None:
        rec["alert_ml"] = ml
        rec["ml_read_at"] = ml_at.isoformat(timespec="seconds")
    rec.pop("ml_hold_reason", None)
    rec.pop("ml_hold_at", None)
    return emit


def parse_game(sport, event):
    comp = event["competitions"][0]
    status = comp["status"]["type"]["name"]
    detail = comp["status"]["type"].get("detail", "")
    competitors = {c["homeAway"]: c for c in comp["competitors"]}
    home, away = competitors["home"], competitors["away"]
    game = {
        "sport": sport,
        "id": event["id"],
        "matchup": f"{away['team']['shortDisplayName']} at {home['team']['shortDisplayName']}",
        "home": home["team"]["shortDisplayName"],
        "away": away["team"]["shortDisplayName"],
        "home_score": home.get("score"),
        "away_score": away.get("score"),
        "status": status,
        "detail": detail,
        "date": event.get("date", "")[:10],
    }
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
            # Fallback: ESPN spread is the home team's line
            # (negative = home favorite, positive = home underdog).
            fav = "home" if spread < 0 else "away"
        if fav and isinstance(spread, (int, float)):
            game["fav"] = fav
            game["spread"] = abs(float(spread))
    return game


def main():
    state = {"games": {}}
    if os.path.exists(STATE_PATH):
        try:
            state = json.load(open(STATE_PATH))
        except Exception:
            state = {"games": {}}
    games = state.setdefault("games", {})
    triggers = []
    bb_candidates = []  # basketball: trailing 10.5+ favs awaiting the ML gate
    seen = 0
    for sport, url in SPORTS:
        try:
            data = fetch(url)
        except Exception as exc:  # a failed feed is a missed check, not no-news
            print(f"FEED FAIL {sport}: {exc}", file=sys.stderr)
            continue
        board_at = now_et()
        for event in data.get("events", []):
            g = parse_game(sport, event)
            seen += 1
            rec = games.setdefault(g["id"], {
                "sport": sport, "matchup": g["matchup"],
                "date": g["date"], "alerted": False,
            })
            if g["status"] == "STATUS_SCHEDULED":
                # Snapshot the pregame line; refresh it while still scheduled.
                if g.get("fav") and g.get("spread"):
                    rec["fav"] = g["fav"]
                    rec["spread"] = g["spread"]
                    rec["fav_team"] = g["home"] if g["fav"] == "home" else g["away"]
                    rec["dog_team"] = g["away"] if g["fav"] == "home" else g["home"]
            elif g["status"] == "STATUS_IN_PROGRESS":
                if rec.get("fav") and rec.get("spread") and rec["spread"] >= THRESHOLD \
                        and not rec.get("alerted"):
                    try:
                        fav_score = int(g["home_score"] if rec["fav"] == "home" else g["away_score"])
                        dog_score = int(g["away_score"] if rec["fav"] == "home" else g["home_score"])
                    except (TypeError, ValueError):
                        continue
                    if fav_score < dog_score:
                        if sport in BASKETBALL:
                            bb_candidates.append(
                                (sport, rec, g, fav_score, dog_score, board_at))
                        else:
                            emit = stamp_alert(rec, "football", board_at,
                                               g["detail"], fav_score, dog_score)
                            triggers.append(
                                f"TRIGGER: {sport} | {g['matchup']} | pregame favorite "
                                f"{rec['fav_team']} -{rec['spread']:g} is LOSING "
                                f"{rec['dog_team']} {dog_score}-{rec['fav_team']} {fav_score} "
                                f"| {g['detail']} | as of {emit:%H:%M:%S} ET")
            elif g["status"] in ("STATUS_FINAL", "STATUS_CANCELED"):
                rec["final"] = True
    # Basketball ML gate: one ScoresAndOdds fetch per sport that has a
    # candidate this run; a game whose live ML is unreadable never alerts.
    # The board fetch happens in THIS run, seconds after the ESPN fetch
    # that produced the candidate — the ML is never carried across runs.
    if bb_candidates:
        by_sport = {}
        for sport, rec, g, fav_score, dog_score, board_at in bb_candidates:
            by_sport.setdefault(sport, []).append(
                (rec, g, fav_score, dog_score, board_at))
        for sport, cands in by_sport.items():
            try:
                board = sao_live_board(fetch_text(SAO_URLS[sport]))
            except Exception as exc:  # a failed board read is a missed check
                print(f"SAO FAIL {sport}: {exc}", file=sys.stderr)
                continue
            ml_at = now_et()
            passing = []
            for rec, g, fav_score, dog_score, board_at in cands:
                entry = board.get(norm_team(rec["fav_team"]))
                if entry is None:
                    continue
                ml = entry["ml"]
                rec["live_ml"] = ml
                if ml < ML_GATE:
                    continue
                hold = ml_sanity_hold(
                    ml, entry.get("spread"), dog_score - fav_score,
                    seconds_left_in_game(g["detail"]))
                if hold:
                    rec["ml_hold_reason"] = hold
                    rec["ml_hold_at"] = now_et().isoformat(timespec="seconds")
                    print(f"HOLD {sport} | {g['matchup']} | {hold}",
                          file=sys.stderr)
                    continue
                passing.append((rec, g, fav_score, dog_score, board_at, ml))
            if not passing:
                continue
            # Re-verification: one fresh ESPN fetch of this sport's board.
            # A favorite that has retaken the lead (or finished) since the
            # triggering fetch does not alert this cycle.
            fresh = {}
            try:
                data2 = fetch(ESPN_URLS[sport])
                for event in data2.get("events", []):
                    g2 = parse_game(sport, event)
                    fresh[g2["id"]] = g2
            except Exception as exc:
                print(f"RECHECK FAIL {sport}: {exc} — proceeding on the "
                      f"triggering snapshot", file=sys.stderr)
            for rec, g, fav_score, dog_score, board_at, ml in passing:
                g2 = fresh.get(g["id"])
                if g2 is not None:
                    if g2["status"] != "STATUS_IN_PROGRESS":
                        print(f"SUPPRESS {sport} | {g['matchup']} | no "
                              f"longer in progress on re-check", file=sys.stderr)
                        continue
                    try:
                        fs2 = int(g2["home_score"] if rec["fav"] == "home"
                                  else g2["away_score"])
                        ds2 = int(g2["away_score"] if rec["fav"] == "home"
                                  else g2["home_score"])
                    except (TypeError, ValueError):
                        fs2 = ds2 = None
                    if fs2 is not None:
                        if fs2 >= ds2:
                            print(f"SUPPRESS {sport} | {g['matchup']} | "
                                  f"favorite no longer trailing on re-check "
                                  f"({rec['dog_team']} {ds2}-{rec['fav_team']} "
                                  f"{fs2})", file=sys.stderr)
                            continue
                        fav_score, dog_score = fs2, ds2
                        g = g2
                emit = stamp_alert(rec, "basketball", board_at, g["detail"],
                                   fav_score, dog_score, ml=ml, ml_at=ml_at)
                triggers.append(
                    f"TRIGGER: {sport} | {g['matchup']} | pregame favorite "
                    f"{rec['fav_team']} -{rec['spread']:g} is LOSING "
                    f"{rec['dog_team']} {dog_score}-{rec['fav_team']} {fav_score} "
                    f"| {g['detail']} | live ML {ml:+d} (ScoresAndOdds, read "
                    f"{ml_at:%H:%M:%S} ET) | as of {emit:%H:%M:%S} ET")
    # Prune games from before yesterday (keeps the file small; finals stay
    # out of future checks either way).
    with open(STATE_PATH, "w") as fh:
        json.dump(state, fh, indent=1)
    for t in triggers:
        print(t)
    if not triggers:
        print("NO TRIGGER")
    print(f"SUMMARY: {seen} games checked, {len(triggers)} new trigger(s)")


if __name__ == "__main__":
    main()
