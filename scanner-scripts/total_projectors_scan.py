#!/usr/bin/env python3
"""Total Projectors scan — TWO Frank Civittolo (@linemakersports)
formula total projectors: NCAAB (reel DfP12JtAPdv, Nov 2, 2026) and
NCAAF (reel DexGq9mAXd2, Oct 21, 2026).

This system is SEPARATE from the Football Totals board
(totals-board.html): Football Totals flags games from elite-defense
matchups + line movement from open + splits divergence — a SIGNAL
system. These projectors are pure FORMULA projections — build your
own number, compare it to the book's number. Own scorecards, never
merged.

NCAAB FORMULA (Frank's, reel DfP12JtAPdv, "Easiest Way to Predict
an NCAAB Total!"): for each team, TWO estimates of its score,
averaged:
  offense-based: team's offensive efficiency x possessions
  defense-based: opponent's defensive efficiency (points it ALLOWS
                 per possession) x possessions
Whiteboard example (Tennessee/Alabama pattern confirmed by the
NCAAF reel, where each team's score clearly uses that team's OWN
plays for both estimates): team score = own possessions/game x
(own offensive efficiency + opponent's defensive efficiency) / 2.
Reel example: Illinois ~77.5, Purdue ~74.4 (total ~151.9).
ADAPTATION (labeled on the board): Frank names the inputs
"offensive efficiency", "defensive efficiency" and "possessions
per game" without naming a stats site. This scan uses BartTorvik's
adjusted efficiency (AdjOE / AdjDE, points per 100 possessions) and
adjusted tempo (AdjT, possessions per 40 minutes = per game) — the
closest public equivalents. EARLY-SEASON CAVEAT: Torvik's ratings
blend his preseason priors with results; in the season's first
days (most teams at 0-1 games played) they lean heavily on the
priors and become results-driven as games accumulate.

NCAAF FORMULA (Frank's, reel DexGq9mAXd2, "Easiest way to predict
NCAAF total!"): same shape with per-play rates:
  team score = own plays/game x (own points per play scored
               + points per play the opponent ALLOWS) / 2.
Reel example (Tennessee @ Alabama): Alabama 0.656 PPP x 67.3 plays
= 44.14 offense-based; Tennessee allows 0.251 PPP x 67.3 = 16.89
defense-based; Alabama projected (44.14 + 16.89) / 2 = 30.5.
Tennessee: (0.427 + 0.318) / 2 x 71.2 plays = 26.5. Total ~57.
Inputs are TeamRankings' public season-to-date stats (points per
play, opponent points per play, plays per game) — Frank names no
stats site; these are the named public equivalents of his inputs.

FLAG RULE — CHET'S DISPLAY CHOICE, NOT Frank's claim (neither reel
states a threshold or a win-rate claim): NCAAB flag OVER when
projected total >= book total + 5.0, UNDER when projected <= book
total - 5.0. NCAAF flag OVER at >= book + 3.0, UNDER at <= book -
3.0. Games inside the band are shown, never flagged.

FRANK-ADJUSTED 85% LINE (added Nov 12, 2026 at Michael's ask):
per flag, the total bought up/down to the number where the model
gives the side about 85%. Final total modeled Normal(center,
sigma), center = projected + bias, sigma from graded residuals
Nov 3-11, 2026 (131/132 flags joined): NCAAB bias +4.69 / sigma
18.53 (n=106), NCAAF bias -0.38 / sigma 18.15 (n=25). UNDER =
smallest half-point line with P(final <= line) >= 0.85; OVER =
largest half-point line with P(final >= line) >= 0.85. The ~19
point offset is the model admitting it can't pin a total tightly
this early in the season — NOT a buy-up guide. Grading is
UNCHANGED: graded at the book total as flagged, never at the
adjusted line.

SOURCES, PUBLIC ONLY (never DarkHorse, never a sign-in):
- Schedule + book totals + Over/Under juice: ESPN scoreboard APIs
  (DraftKings totals), the same source the NHL Totals scan uses.
- NCAAB efficiency/tempo: BartTorvik T-Rank page (barttorvik.com),
  public table (adjusted efficiency + adjusted tempo).
- NCAAF per-play stats: TeamRankings public stat pages (points
  per play, opponent points per play, plays per game), 2026
  season-to-date column.
If a team is missing from a stats source (e.g. a non-D1 opponent
is not in BartTorvik), that game is shown as unavailable and never
flagged — never estimated. FAIL LOUD: if ESPN fails, or a stats
source fails to load/parse, the script exits 2 and writes nothing
for the affected sport... in fact it writes nothing at all: a
failed fetch is never reported as "zero flags". A sport with no
games today is NOT a failure — its section is simply empty.

Usage: total_projectors_scan.py [--date YYYY-MM-DD]
(default: today in America/New_York)
Output: updates total-projectors-state.json, writes
total-projectors-snapshot-<date>.json, appends
total-projectors-<date>.md, prints a JSON summary with
over_qualifiers / under_qualifiers per sport for delivery.
"""
import datetime as dt
import http.cookiejar
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser

HF = os.path.expanduser("~/workspace/goals/bet-and-clv-tracker/hidden_files")
ESPN_CBB = ("https://site.api.espn.com/apis/site/v2/sports/"
            "basketball/mens-college-basketball/scoreboard")
ESPN_CFB = ("https://site.api.espn.com/apis/site/v2/sports/"
            "football/college-football/scoreboard")
TORVIK = "https://barttorvik.com/trank.php?year={year}"
TR_BASE = "https://www.teamrankings.com/college-football/stat/{stat}"
GAPS = {"NCAAB": 5.0, "NCAAF": 3.0}
# FRANK-ADJUSTED 85% LINE (added Nov 12, 2026 at Michael's ask):
# Final total modeled Normal(center, sigma) with center = projected
# total + bias and sigma from graded residuals Nov 3-11, 2026
# (validated join, 131/132 flags; NCAAB n=106, NCAAF n=25):
#   NCAAB: bias +4.69, sigma 18.53
#   NCAAF: bias -0.38, sigma 18.15
# UNDER: smallest half-point line L with P(final <= L) >= 0.85.
# OVER: largest half-point line L with P(final >= L) >= 0.85.
# z(0.85) = 1.036. Early-season sample only (ratings least stable)
# — re-fit as the season settles; the ~19-point offset is the model
# admitting it can't pin a total tightly yet, NOT a buy-up guide.
# Grading is UNCHANGED: graded at the book total as flagged, never
# at the adjusted line.
ADJ85 = {"NCAAB": {"bias": 4.69, "sigma": 18.53},
         "NCAAF": {"bias": -0.38, "sigma": 18.15}}
Z85 = 1.036
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")
# ESPN location/short name -> stats-source team name, where they
# differ. Only entries verified against the source pages belong
# here; anything unlisted is matched by exact normalized name.
CBB_ALIASES = {
    "uconn": "Connecticut",
    "ole miss": "Mississippi",
    "nc state": "NC State",
    "miami": "Miami FL",
    "miami (oh)": "Miami OH",
    "usc": "USC",
    "unc": "North Carolina",
    "unlv": "UNLV",
    "ucf": "UCF",
    "uab": "UAB",
    "utsa": "UTSA",
    "utep": "UTEP",
    "smu": "SMU",
    "tcu": "TCU",
    "byu": "BYU",
    "lsu": "LSU",
    "fau": "Florida Atlantic",
    "fiu": "FIU",
    "liu": "LIU",
    "siu edwardsville": "SIU Edwardsville",
    "california baptist": "Cal Baptist",
    "sam houston": "Sam Houston St.",
    "lsu new orleans": "New Orleans",
    "cal poly": "Cal Poly",
    "long beach state": "Long Beach St.",
    "grambling state": "Grambling St.",
    "southern": "Southern",
}
CFB_ALIASES = {
    "miami (oh)": "Miami OH",
    "ole miss": "Ole Miss",
    "uconn": "Connecticut",
    "umass": "Massachusetts",
    "fiu": "FIU",
    "fau": "Florida Atlantic",
}


def _nth_weekday(year, month, weekday, n):
    d = dt.date(year, month, 1)
    delta = (weekday - d.weekday()) % 7
    return d + dt.timedelta(days=delta + 7 * (n - 1))


def eastern_offset(utc_dt):
    """UTC offset in hours for America/New_York (stdlib DST rule)."""
    year = utc_dt.year
    dst_start = dt.datetime.combine(_nth_weekday(year, 3, 6, 2), dt.time(7, 0))
    dst_end = dt.datetime.combine(_nth_weekday(year, 11, 6, 1), dt.time(6, 0))
    if dst_start <= utc_dt.replace(tzinfo=None) < dst_end:
        return -4
    return -5


def fmt_tipoff(iso):
    for fmt in ("%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            utc_dt = dt.datetime.strptime(iso, fmt)
            break
        except ValueError:
            continue
    else:
        return ""
    et = utc_dt + dt.timedelta(hours=eastern_offset(utc_dt))
    hour = et.hour % 12 or 12
    return f"{hour}:{et.minute:02d} {'AM' if et.hour < 12 else 'PM'}"


def parse_juice(val):
    if val is None:
        return None
    try:
        return int(str(val).replace("+", ""))
    except ValueError:
        return None


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_text(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", "replace")


class TableParser(HTMLParser):
    """Collect table rows; skip text inside <span class='lowrow'>
    (BartTorvik glues rank numbers into value cells that way)."""

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


def parse_tables(html_text):
    p = TableParser()
    p.feed(html_text)
    return p.rows


def norm(name):
    toks = re.sub(r"[^a-z0-9]", " ", name.lower()).split()
    # BartTorvik abbreviates "State" as "St." — fold them together.
    return ["st" if t == "state" else t for t in toks]


def key_of(name, aliases):
    n = " ".join(norm(name))
    return " ".join(norm(aliases.get(n, name)))


# ---------------- stats sources ----------------

def torvik_season_year(date_str):
    d = dt.date.fromisoformat(date_str)
    return d.year + 1 if d.month >= 10 else d.year


def norm_cdf(x):
    """Standard normal CDF via erf (no scipy)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def adj85(projected, flag, sport):
    """Frank-adjusted 85% line for a flag.

    Returns (line, prob): line is the bought total on a half-point
    (e.g. 130.5 for "Over 130.5"), prob is the model's P of that
    side hitting at that line. UNDER: smallest half-point L with
    P(final <= L) >= 0.85. OVER: largest half-point L with
    P(final >= L) >= 0.85. Final ~ Normal(projected + bias, sigma)
    per ADJ85 above.
    """
    p = ADJ85[sport]
    center = projected + p["bias"]
    off = Z85 * p["sigma"]
    if flag == "under":
        raw = center + off
        line = math.ceil(raw * 2) / 2.0
        prob = norm_cdf((line - center) / p["sigma"])
    else:
        raw = center - off
        line = math.floor(raw * 2) / 2.0
        prob = 1.0 - norm_cdf((line - center) / p["sigma"])
    return line, round(prob, 3)


def fetch_torvik(year):
    """BartTorvik sits behind a JS-check form post; satisfy it with
    a cookie jar (GET -> POST js_test_submitted=1 -> GET)."""
    url = TORVIK.format(year=year)
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj))
    headers = {"User-Agent": UA}

    def go(req_url, data=None):
        req = urllib.request.Request(req_url, data=data, headers=headers)
        with opener.open(req, timeout=45) as resp:
            return resp.read().decode("utf-8", "replace")

    go(url)
    try:
        go(url, urllib.parse.urlencode(
            {"js_test_submitted": "1"}).encode())
    except Exception:
        pass
    html_text = go(url)
    rows = parse_tables(html_text)
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
        name = r[idx["Team"]]
        try:
            teams[name] = {
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


def fetch_teamrankings(stat):
    rows = parse_tables(fetch_text(TR_BASE.format(stat=stat)))
    out = {}
    for r in rows:
        if len(r) >= 3 and r[0].isdigit():
            try:
                out[r[1]] = float(r[2])
            except ValueError:
                continue
    if len(out) < 100:
        raise ValueError(
            f"TeamRankings {stat} parse found only {len(out)} teams")
    return out


# ---------------- ESPN board ----------------

def espn_games(url, date, groups=None):
    q = f"?dates={date.replace('-', '')}&limit=400"
    if groups:
        q += f"&groups={groups}"
    board = fetch_json(url + q)
    out = []
    for ev in board.get("events", []):
        comp = ev["competitions"][0]
        by_side = {}
        for c in comp["competitors"]:
            by_side[c["homeAway"]] = c
        away = by_side.get("away")
        home = by_side.get("home")
        if not away or not home:
            continue
        odds = comp.get("odds", [])
        total = over_juice = under_juice = None
        if odds:
            o = odds[0]
            total = o.get("overUnder")
            tot = o.get("total", {})
            over_juice = parse_juice(
                (tot.get("over", {}).get("close") or {}).get("odds"))
            under_juice = parse_juice(
                (tot.get("under", {}).get("close") or {}).get("odds"))
        out.append({
            "away": away["team"]["displayName"],
            "home": home["team"]["displayName"],
            "away_key_name": away["team"].get("location")
                             or away["team"]["displayName"],
            "home_key_name": home["team"].get("location")
                             or home["team"]["displayName"],
            "tipoff_et": fmt_tipoff(ev["date"]),
            "tipoff_utc": ev["date"],
            "status": comp.get("status", {}).get("type", {}).get("state", ""),
            "total": float(total) if total is not None else None,
            "total_source": ("ESPN / DraftKings" if total is not None
                             else "ESPN — no total listed"),
            "over_juice": over_juice,
            "under_juice": under_juice,
        })
    return out


def lookup(stats, name, aliases):
    """Find a team's stats dict by normalized name + aliases."""
    target = key_of(name, aliases)
    for k, v in stats.items():
        if " ".join(norm(k)) == target:
            return v
    return None


def project_games(sport, games, stats, aliases, kind):
    """kind: 'cbb' (stats rows have adj_oe/adj_de/adj_t) or 'cfb'
    (stats rows have ppp_off/ppp_allow/plays)."""
    gap_bar = GAPS[sport]
    games_out, over_q, under_q = [], [], []
    teams_out = {}
    for g in games:
        a = lookup(stats, g["away_key_name"], aliases)
        h = lookup(stats, g["home_key_name"], aliases)
        entry = {k: g[k] for k in ("away", "home", "tipoff_et", "total",
                                   "total_source", "over_juice",
                                   "under_juice")}
        if a is None or h is None:
            missing = [n for n, s in ((g["away"], a), (g["home"], h))
                       if s is None]
            entry.update({
                "projected_total": None, "gap": None, "flag": None,
                "unavailable": "no stats for " + ", ".join(missing),
            })
            games_out.append(entry)
            continue
        if kind == "cbb":
            a_vol, h_vol = a["adj_t"], h["adj_t"]
            a_off, a_def = a["adj_oe"] / 100.0, a["adj_de"] / 100.0
            h_off, h_def = h["adj_oe"] / 100.0, h["adj_de"] / 100.0
            entry.update({
                "away_adj_oe": a["adj_oe"], "away_adj_de": a["adj_de"],
                "away_adj_t": a["adj_t"], "away_games": a["games"],
                "home_adj_oe": h["adj_oe"], "home_adj_de": h["adj_de"],
                "home_adj_t": h["adj_t"], "home_games": h["games"],
            })
            teams_out[g["away"]] = {"adj_oe": a["adj_oe"],
                                    "adj_de": a["adj_de"],
                                    "adj_t": a["adj_t"], "games": a["games"]}
            teams_out[g["home"]] = {"adj_oe": h["adj_oe"],
                                    "adj_de": h["adj_de"],
                                    "adj_t": h["adj_t"], "games": h["games"]}
        else:
            a_vol, h_vol = a["plays"], h["plays"]
            a_off, a_def = a["ppp_off"], a["ppp_allow"]
            h_off, h_def = h["ppp_off"], h["ppp_allow"]
            entry.update({
                "away_ppp_off": a["ppp_off"], "away_ppp_allow": a["ppp_allow"],
                "away_plays": a["plays"],
                "home_ppp_off": h["ppp_off"], "home_ppp_allow": h["ppp_allow"],
                "home_plays": h["plays"],
            })
            teams_out[g["away"]] = dict(a)
            teams_out[g["home"]] = dict(h)
        away_exp = round(a_vol * (a_off + h_def) / 2.0, 2)
        home_exp = round(h_vol * (h_off + a_def) / 2.0, 2)
        predicted = round(away_exp + home_exp, 1)
        gap = flag = None
        adj85_line = adj85_prob = None
        if g["total"] is not None:
            gap = round(predicted - g["total"], 1)
            if gap >= gap_bar:
                flag = "over"
            elif gap <= -gap_bar:
                flag = "under"
            if flag in ("over", "under"):
                adj85_line, adj85_prob = adj85(predicted, flag, sport)
        entry.update({"away_exp_points": away_exp,
                      "home_exp_points": home_exp,
                      "projected_total": predicted,
                      "gap": gap, "flag": flag,
                      "adj85_line": adj85_line,
                      "adj85_prob": adj85_prob})
        games_out.append(entry)
        if flag in ("over", "under"):
            qual = {"away": g["away"], "home": g["home"],
                    "tipoff_et": g["tipoff_et"], "total": g["total"],
                    "total_source": g["total_source"],
                    "projected_total": predicted, "gap": gap,
                    "over_juice": g["over_juice"],
                    "under_juice": g["under_juice"],
                    "away_exp_points": away_exp,
                    "home_exp_points": home_exp,
                    "adj85_line": adj85_line,
                    "adj85_prob": adj85_prob}
            (over_q if flag == "over" else under_q).append(qual)
    return games_out, over_q, under_q, teams_out


def main():
    date = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--date":
            date = args[i + 1]
    if date is None:
        now_utc = dt.datetime.utcnow()
        now_et = now_utc + dt.timedelta(hours=eastern_offset(now_utc))
        date = now_et.date().isoformat()

    try:
        cbb_board = espn_games(ESPN_CBB, date, groups=50)
        cfb_board = espn_games(ESPN_CFB, date)
    except Exception as exc:
        print(f"FETCH FAILED (ESPN): {exc}", file=sys.stderr)
        return 2

    sports_out = {}
    errors = []
    # ---- NCAAB ----
    if cbb_board:
        try:
            torvik = fetch_torvik(torvik_season_year(date))
            stats = {" ".join(norm(k)): v for k, v in torvik.items()}
            g_out, oq, uq, t_out = project_games(
                "NCAAB", cbb_board, stats, CBB_ALIASES, "cbb")
            sports_out["NCAAB"] = {"games": g_out, "over_qualifiers": oq,
                                   "under_qualifiers": uq, "teams": t_out}
        except Exception as exc:
            errors.append(f"NCAAB stats: {exc}")
    else:
        sports_out["NCAAB"] = {"games": [], "over_qualifiers": [],
                               "under_qualifiers": [], "teams": {}}
    # ---- NCAAF ----
    if cfb_board:
        try:
            ppp = fetch_teamrankings("points-per-play")
            opp = fetch_teamrankings("opponent-points-per-play")
            plays = fetch_teamrankings("plays-per-game")
            stats = {}
            for name in ppp:
                if name in opp and name in plays:
                    stats[" ".join(norm(name))] = {
                        "ppp_off": ppp[name], "ppp_allow": opp[name],
                        "plays": plays[name]}
            g_out, oq, uq, t_out = project_games(
                "NCAAF", cfb_board, stats, CFB_ALIASES, "cfb")
            sports_out["NCAAF"] = {"games": g_out, "over_qualifiers": oq,
                                   "under_qualifiers": uq, "teams": t_out}
        except Exception as exc:
            errors.append(f"NCAAF stats: {exc}")
    else:
        sports_out["NCAAF"] = {"games": [], "over_qualifiers": [],
                               "under_qualifiers": [], "teams": {}}
    if errors:
        print("FETCH FAILED: " + "; ".join(errors), file=sys.stderr)
        return 2

    from zoneinfo import ZoneInfo
    read_stamp = dt.datetime.now(ZoneInfo("America/New_York")).strftime(
        "%Y-%m-%d ~%I:%M %p ET")
    source_note = (
        "ESPN scoreboard APIs (DraftKings totals + juice) + BartTorvik "
        "T-Rank adjusted efficiency/tempo (NCAAB) + TeamRankings "
        "points-per-play / opponent points-per-play / plays-per-game "
        f"(NCAAF), read {read_stamp}")
    snap = {"date": date, "source": source_note, "sports": sports_out}
    with open(os.path.join(HF, f"total-projectors-snapshot-{date}.json"),
              "w") as fh:
        json.dump(snap, fh, indent=1)

    state_path = os.path.join(HF, "total-projectors-state.json")
    try:
        state = json.load(open(state_path))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"meta": {}, "days": {}}
    state["meta"] = {
        "source": source_note,
        "last_scan": date,
        "method": (
            "NCAAB (Frank reel DfP12JtAPdv): team score = own "
            "possessions/game x (own offensive efficiency + opponent "
            "defensive efficiency) / 2, both teams summed. Inputs: "
            "BartTorvik AdjOE/AdjDE (per 100 poss) and AdjT — closest "
            "public equivalents to Frank's per-possession efficiency "
            "+ possessions inputs (adaptation labeled on the board). "
            "NCAAF (Frank reel DexGq9mAXd2): team score = own "
            "plays/game x (own points per play + opponent points "
            "allowed per play) / 2, both teams summed. Inputs: "
            "TeamRankings points per play / opponent points per play "
            "/ plays per game. Flag OVER at projected >= book + gap, "
            "UNDER at projected <= book - gap; gap = 5.0 NCAAB / 3.0 "
            "NCAAF (CHET'S DISPLAY CHOICE, NOT Frank's claim — the "
            "reels state no threshold and no win-rate claim). "
            "Frank-adjusted 85% line (added Nov 12, 2026): per flag, "
            "the total bought up/down to ~85% modeled — final total "
            "Normal(projected + bias, sigma), NCAAB bias +4.69 / "
            "sigma 18.53, NCAAF bias -0.38 / sigma 18.15, from "
            "graded residuals Nov 3-11 (early-season only). Grading "
            "stays at the book total as flagged. "
            "Separate from the Football Totals signal board."),
    }
    state.setdefault("days", {})[date] = sports_out
    json.dump(state, open(state_path, "w"), indent=1)

    lines = [f"# Total Projectors scan — {date}",
             f"Source: {source_note}",
             "Frank-adjusted 85% line (added Nov 12, 2026): per flag, "
             "the total bought up/down to ~85% modeled — final total "
             "Normal(projected + bias, sigma), NCAAB bias +4.69 / "
             "sigma 18.53, NCAAF bias -0.38 / sigma 18.15, from graded "
             "residuals Nov 3-11 (early-season only). Grading stays at "
             "the book total as flagged.", ""]
    for sport in ("NCAAB", "NCAAF"):
        s = sports_out[sport]
        gap_bar = GAPS[sport]
        lines += [f"## {sport} — {len(s['games'])} games, "
                  f"{len(s['over_qualifiers'])} Over flags, "
                  f"{len(s['under_qualifiers'])} Under flags "
                  f"(flag gap {gap_bar})",
                  "| Matchup | Tipoff ET | Away exp | Home exp | "
                  "Projected | Book total | Gap | Flag | Frank-adj 85% |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for g in s["games"]:
            adj = "—"
            if g.get("flag") and g.get("adj85_line") is not None:
                side = "O" if g["flag"] == "over" else "U"
                adj = (f"{side}{g['adj85_line']} "
                       f"(~{g['adj85_prob'] * 100:.0f}%)")
            lines.append(
                f"| {g['away']} @ {g['home']} | {g['tipoff_et']} | "
                f"{g.get('away_exp_points', '—')} | "
                f"{g.get('home_exp_points', '—')} | "
                f"{g.get('projected_total', '—')} | {g['total']} | "
                f"{g.get('gap', '—')} | {g.get('flag') or '—'} | "
                f"{adj} |")
        lines.append("")
    with open(os.path.join(HF, f"total-projectors-{date}.md"), "w") as fh:
        fh.write("\n".join(lines))

    print(json.dumps({
        "date": date,
        "NCAAB": {"games": len(sports_out["NCAAB"]["games"]),
                  "over_qualifiers": sports_out["NCAAB"]["over_qualifiers"],
                  "under_qualifiers": sports_out["NCAAB"]["under_qualifiers"]},
        "NCAAF": {"games": len(sports_out["NCAAF"]["games"]),
                  "over_qualifiers": sports_out["NCAAF"]["over_qualifiers"],
                  "under_qualifiers": sports_out["NCAAF"]["under_qualifiers"]},
        "games_detail": {s: sports_out[s]["games"] for s in sports_out},
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
