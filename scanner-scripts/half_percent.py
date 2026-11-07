#!/usr/bin/env python3
"""Half Percent Club — daily one-stock trade scan (PC, 2026-11-06).

Modeled on The Half Percent Club reel (Instagram DfX8wkcxURK,
@the_half_percent_club, posted 2026-11-05): ONE stock in play, ONE
trade per day, target +0.5%, then done for the day. The creator's
example was Unity (U) $47.33 -> $47.60 in 3 minutes (+0.57%). His
15-consecutive-day streak and +44.21% YTD claims are UNVERIFIED —
they are never stated as fact in the report.

Chet's v1 rules (set 2026-11-06 at Michael's tap — "build it, I'll
trade it live small" + "put the scan on my computer").

V2 AMENDMENTS (2026-11-06 evening, Michael's verbal tap — "go
ahead, update it"): (1) the qualifying premarket gap is capped at
+3% to +12% — bigger gaps are fade territory (the Nov 6 NET pick
gapped +20.2% and faded all day); (2) the modeled grading entry is
the ACTUAL 9:30 AM ET open (first 5-min bar open), with target /
stop rebased to +0.5% / -0.5% off that open; if the open prints
below the pick's plan stop (premarket entry x 0.995), the day
grades NO TRADE — excluded from the record, no modeled P/L.
V3 AMENDMENTS (2026-11-06 night, Michael's tap on the options
card — "Add re-check + open confirmation"): two anti-head-fake
layers. (1) A 9:28 AM ET PRE-BELL RE-CHECK (scheduler daily
09:28 entry running this script with --recheck): today's pick
is re-priced off Nasdaq quote info; if the gap has left the
+3% to +12% band or the price is below the plan stop, the pick
is CANCELLED before the bell (a cancel note is banked for
delivery; it grades NO TRADE next morning with the reason
recorded). An unreadable or stale quote NEVER cancels — the
pick stands, marked unverified, and nothing is delivered.
(2) OPEN CONFIRMATION inside grading: after the v2 open
anchor, the first two 5-min bars (09:30 + 09:35) must both
CLOSE at or above the rebased stop; if either closes below
it, the day grades NO TRADE ("open confirmation failed") —
the trade only counts when the open actually holds.
V3.1 AMENDMENT (2026-11-06 night, Michael's tap — "Add the
9:28–9:35 validity window"): the --recheck may only EVALUATE
inside 09:28–09:35 ET. Outside that window it is a complete
no-op — no state writes, no recheck keys, no delivery — so a
scheduler catch-up fire after a restart (the 20:20 ET incident
on deploy night, when an after-hours quote briefly cancelled
the NET pick) can never act on a stale quote.
- Weekdays only, ~09:00 ET (scheduler "daily" entry; the script
  itself no-ops Sat/Sun). If the market is closed / there is no fresh
  premarket data, write a NO PICK note and stop.
- Selection from free/public sources (all verified reachable from
  this PC 2026-11-06):
  * Nasdaq earnings calendar (today + yesterday) — catalyst set,
    carries market cap. https://api.nasdaq.com/api/calendar/earnings
  * Yahoo Finance predefined screeners day_gainers + most_actives —
    momentum/active set; rows carry marketCap, quoteType, bid/ask.
  * Nasdaq quote info per candidate — live premarket last sale,
    % change vs prior close, premarket volume, bid/ask, market
    status, last-trade timestamp (the freshness proof).
- Filters: LONG only; US-listed common stock (no ETFs/ETNs/funds,
  no share-class symbols); price $10-$500; market cap > $1B;
  premarket move +3% to +12% (v2 cap); tight spread ((ask-bid)/price <= 1% when
  quoted); elevated premarket volume (>= 25k shares by ~09:00 with
  an earnings catalyst, >= 100k without). Rank: earnings catalyst
  first, then premarket % change, then volume.
- EXACTLY ONE pick, or NO PICK if nothing clears the bar — a trade
  is never forced.
- ENTRY PLAN: entry zone = reference (premarket last ~09:00 ET)
  +/- 0.2%. PLAN TARGET = reference x 1.005, PLAN STOP =
  reference x 0.995 (1:1), TIME STOP = out by 10:00 AM ET if
  neither hits. V2: the MODELED grading entry is the actual
  9:30 AM ET open, with target/stop rebased off that open; an
  open below the plan stop grades NO TRADE.
- Modeled size $10,000 flat for the running record. Michael places
  his own trade at his own small live size — Chet NEVER places
  trades; this script only writes a report.
- GRADING (first step of each run, before the new pick): the prior
  pick is graded from Yahoo Finance 5-minute bars (v8 chart API,
  no auth) over the 09:30-10:00 ET entry window. V2 anchor: entry
  = the first 5-min bar's open (the 9:30 AM open); TARGET/STOP
  rebase to entry x 1.005 / x 0.995; an open below the pick's
  plan stop grades NO TRADE instead (no W/L, no P/L). WIN if TARGET is
  touched before STOP (+0.5% modeled), LOSS if STOP first (-0.5%),
  TIME-EXIT at the 10:00 ET close otherwise (modeled P/L = that
  move). If one 5-min bar touches BOTH target and stop, it grades
  LOSS (stop assumed first — conservative) and says so. No bars =
  left ungraded and retried; after 5 tries it is recorded NO DATA
  and excluded from the W-L record. V3: a pick cancelled at the
  9:28 AM re-check also grades NO TRADE (cancel reason recorded),
  and a trade is confirmed only when the first two 5-min bars
  close at/above the rebased stop — otherwise NO TRADE (open
  confirmation failed). Granularity is 5-min bars;
  fills are modeled, never claimed as real.

Writes data/half-percent-<date>.md (the delivery watcher wakes on
the "N candidates" header line when a pick or a grading lands;
NO PICK days carry no signal line and stay silent, per the
alert-only convention) and data/half-percent-state.json
({"entries": {date: pick/grade}, "meta": {"record": {...}}}).
"""
import datetime as dt
import re
import sys
from zoneinfo import ZoneInfo

import requests

from config import DATA_DIR
from odds_common import bank, now_stamp
from state import load_state, save_state

SCAN = "half-percent"
ET = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36", "Accept": "application/json"}
NASDAQ = "https://api.nasdaq.com/api"
YAHOO = "https://query1.finance.yahoo.com"
MODEL_SIZE = 10000.0
TARGET_PCT = 0.005
STOP_PCT = 0.005
MIN_PRICE, MAX_PRICE = 10.0, 500.0
MIN_MCAP = 1_000_000_000
MIN_MOVE_PCT = 3.0
MAX_MOVE_PCT = 12.0
MAX_SPREAD_PCT = 1.0
MIN_VOL_CATALYST = 25_000
MIN_VOL_PLAIN = 100_000
MAX_CANDIDATES = 60
NAME_BLOCKLIST = ("ETF", "ETN", "DIREXION", "PROSHARES", "ISHARES",
                  "BULL 3X", "BEAR 3X", "INDEX FUND", "TRUST FUND")

# V3.1 validity window (2026-11-06, Michael's tap): the 9:28 AM
# ET pre-bell re-check may only EVALUATE inside 09:28-09:35 ET
# inclusive. Outside the window a --recheck run is a complete
# no-op: the scheduler catch-up-fires missed jobs on restart
# (the 20:20 ET deploy-night incident), and a stale after-hours
# quote must never be read as a 9:28 AM re-check.
RECHECK_WINDOW_START = (9, 28)  # (hour, minute) ET, inclusive
RECHECK_WINDOW_END = (9, 35)    # (hour, minute) ET, inclusive


def in_recheck_window(now):
    """True only inside the 09:28–09:35 ET validity window."""
    return RECHECK_WINDOW_START <= (now.hour, now.minute) \
        <= RECHECK_WINDOW_END


def today_et():
    return dt.datetime.now(ET).date()


def money(x):
    return f"${x:,.2f}"


def parse_money(text):
    if text is None:
        return None
    m = re.search(r"[\d,]+\.?\d*", str(text).replace("$", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_pct(text):
    """'+6.23%' / '-3.50%' -> signed float (parse_money drops signs)."""
    if text is None:
        return None
    m = re.search(r"([+-]?)\s*([\d,]+\.?\d*)", str(text))
    if not m:
        return None
    try:
        val = float(m.group(2).replace(",", ""))
    except ValueError:
        return None
    return -val if m.group(1) == "-" else val


def fetch_earnings(date_str):
    """Nasdaq earnings calendar for one date -> {symbol: {...}}."""
    try:
        data = requests.get(f"{NASDAQ}/calendar/earnings",
                            params={"date": date_str}, headers=UA,
                            timeout=25).json()
    except (requests.RequestException, ValueError):
        return {}
    out = {}
    for row in ((data.get("data") or {}).get("rows") or []):
        sym = (row.get("symbol") or "").strip().upper()
        if sym:
            out[sym] = {"name": row.get("name") or sym,
                        "mcap": parse_money(row.get("marketCap")),
                        "when": row.get("time") or "",
                        "date": date_str}
    return out


def fetch_screener(scr_id, count=25):
    """Yahoo predefined screener -> {symbol: quote dict}."""
    try:
        data = requests.get(
            f"{YAHOO}/v1/finance/screener/predefined/saved",
            params={"scrIds": scr_id, "count": count},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=25).json()
    except (requests.RequestException, ValueError):
        return {}
    try:
        quotes = data["finance"]["result"][0].get("quotes") or []
    except (KeyError, IndexError, TypeError):
        return {}
    return {(q.get("symbol") or "").upper(): q for q in quotes
            if q.get("symbol")}


def fetch_info(sym):
    """Nasdaq quote info -> primaryData dict or None."""
    try:
        data = requests.get(f"{NASDAQ}/quote/{sym}/info",
                            params={"assetclass": "stocks"}, headers=UA,
                            timeout=20).json()
    except (requests.RequestException, ValueError):
        return None
    d = data.get("data") or {}
    if not d.get("primaryData"):
        return None
    return d


def fresh_stamp(date_et):
    """Nasdaq lastTradeTimestamp prefix for a date, e.g. 'Nov 6, 2026'."""
    return date_et.strftime("%b ") + f"{date_et.day}, {date_et.year}"


def fetch_bars_5m(sym, date_et):
    """Yahoo 5-min bars for one ET date -> [(et_dt, o, h, l, c)]."""
    start = dt.datetime.combine(date_et, dt.time(9, 30), ET)
    end = dt.datetime.combine(date_et, dt.time(10, 5), ET)
    try:
        data = requests.get(
            f"{YAHOO}/v8/finance/chart/{sym}",
            params={"period1": int(start.timestamp()),
                    "period2": int(end.timestamp()),
                    "interval": "5m"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=25).json()
        res = data["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
    except (requests.RequestException, ValueError, KeyError,
            IndexError, TypeError):
        return []
    bars = []
    for i, t in enumerate(ts):
        try:
            o, h, l, c = (q["open"][i], q["high"][i], q["low"][i],
                          q["close"][i])
        except (IndexError, KeyError, TypeError):
            continue
        if None in (o, h, l, c):
            continue
        bars.append((dt.datetime.fromtimestamp(t, ET), o, h, l, c))
    return sorted(bars)


def grade_pick(date_str, pick):
    """Grade one pick dict -> grade dict or None (retry later).

    V2 (2026-11-06, Michael's tap): the modeled entry is the
    actual 9:30 AM ET open — the first 5-min bar's open — and
    target/stop rebase to +0.5%/-0.5% off that open. If the open
    prints BELOW the pick's plan stop (the premarket plan's
    entry x 0.995), the day grades NO TRADE: excluded from the
    W-L record, no modeled P/L.
    V3 (2026-11-06): OPEN CONFIRMATION — the first two 5-min
    bars must both close at/above the rebased stop, else the
    day also grades NO TRADE ("open confirmation failed").
    """
    date_et = dt.date.fromisoformat(date_str)
    bars = fetch_bars_5m(pick["ticker"], date_et)
    window = [b for b in bars
              if dt.time(9, 30) <= b[0].time() < dt.time(10, 0)]
    if not window:
        return None
    open_price = window[0][1]
    plan_stop = pick["stop"]
    if open_price < plan_stop:
        return {"outcome": "NO TRADE", "pl": 0.0,
                "open": open_price, "plan_stop": plan_stop,
                "reason": "open_below_stop",
                "note": "9:30 AM open printed below the plan stop "
                        "- v2 no-trade rule (excluded from the "
                        "record, no modeled position)"}
    entry = open_price
    target = round(entry * (1 + TARGET_PCT), 2)
    stop = round(entry * (1 - STOP_PCT), 2)
    anchor = {"entry": entry, "target": target, "stop": stop}
    # V3 layer 2 — OPEN CONFIRMATION: the first two 5-min bars
    # (09:30 + 09:35) must both CLOSE at or above the rebased
    # stop. A target-touching bar closes high by construction,
    # so a close below the stop is the head-fake failure
    # signal; the trade never counted -> NO TRADE, excluded.
    for when, _o, _h, _l, close in window[:2]:
        if close < stop:
            return {"outcome": "NO TRADE", "pl": 0.0,
                    "open": open_price, "plan_stop": plan_stop,
                    "reason": "open_confirmation",
                    "confirm_bar": when.strftime("%H:%M ET"),
                    "confirm_close": close,
                    "note": "open confirmation failed - a "
                            "first-minutes bar closed below the "
                            "rebased stop (v3 layer 2)",
                    **anchor}
    for when, _o, hi, lo, _c in window:
        hit_t, hit_s = hi >= target, lo <= stop
        if hit_t and hit_s:
            return {"outcome": "LOSS", "exit_price": stop,
                    "exit_time": when.strftime("%H:%M ET"),
                    "pl": -MODEL_SIZE * STOP_PCT,
                    "note": "target and stop both inside one 5-min "
                            "bar — stop assumed first (conservative)",
                    **anchor}
        if hit_t:
            return {"outcome": "WIN", "exit_price": target,
                    "exit_time": when.strftime("%H:%M ET"),
                    "pl": MODEL_SIZE * TARGET_PCT,
                    "note": "target touched before stop", **anchor}
        if hit_s:
            return {"outcome": "LOSS", "exit_price": stop,
                    "exit_time": when.strftime("%H:%M ET"),
                    "pl": -MODEL_SIZE * STOP_PCT,
                    "note": "stop touched before target", **anchor}
    # time exit: close of the last bar starting before 10:00 ET
    exit_close = window[-1][4]
    # prefer an actual 10:00 bar close when present
    for when, _o, _h, _l, c in bars:
        if when.time() == dt.time(10, 0):
            exit_close = c
            break
    pl = (exit_close / entry - 1.0) * MODEL_SIZE
    return {"outcome": "TIME-EXIT", "exit_price": round(exit_close, 2),
            "exit_time": "10:00 ET", "pl": round(pl, 2),
            "note": "neither target nor stop touched by 10:00 ET",
            **anchor}


def record_line(meta):
    r = meta.get("record") or {}
    nt = r.get("no_trades", 0)
    nt_part = f"-{nt}NT" if nt else ""
    return (f"Running record: {r.get('wins', 0)}W-"
            f"{r.get('losses', 0)}L-{r.get('time_exits', 0)}T"
            f"{nt_part}, "
            f"modeled P/L {money(r.get('modeled_pl', 0.0))} "
            f"on $10,000 flat modeled size.")


def grade_pending(state, today_str_):
    """Grade every ungraded prior pick; returns report lines."""
    lines = []
    meta = state.setdefault("meta", {})
    rec = meta.setdefault("record", {"wins": 0, "losses": 0,
                                     "time_exits": 0, "no_data": 0,
                                     "no_trades": 0,
                                     "modeled_pl": 0.0})
    rec.setdefault("no_trades", 0)
    graded = 0
    for date_str in sorted(state.get("entries", {})):
        if date_str >= today_str_:
            continue
        entry_rec = state["entries"][date_str]
        pick = entry_rec.get("pick")
        if not pick or entry_rec.get("grade"):
            continue
        if pick.get("status") == "cancelled":
            # V3 layer 1: cancelled at the 9:28 AM re-check —
            # grades NO TRADE regardless of what the bars did.
            rc = pick.get("recheck") or {}
            entry_rec["grade"] = {
                "outcome": "NO TRADE", "pl": 0.0,
                "reason": "cancelled_recheck",
                "note": "cancelled at the 9:28 AM re-check "
                        "(v3 layer 1)"}
            rec["no_trades"] = rec.get("no_trades", 0) + 1
            graded += 1
            lines.append(
                f"- GRADED {date_str} {pick['ticker']}: NO TRADE "
                f"— pick CANCELLED at the 9:28 AM re-check "
                f"({rc.get('reason', 're-check failed')}). V3 "
                f"rule: no modeled position — excluded from the "
                f"record, no modeled P/L.")
            continue
        grade = grade_pick(date_str, pick)
        if grade is None:
            entry_rec["grade_attempts"] = \
                entry_rec.get("grade_attempts", 0) + 1
            if entry_rec["grade_attempts"] >= 5:
                entry_rec["grade"] = {"outcome": "NO DATA",
                                      "pl": 0.0,
                                      "note": "no 5-min bars after "
                                              "5 tries — excluded "
                                              "from the record"}
                rec["no_data"] += 1
                graded += 1
                lines.append(
                    f"- GRADED {date_str} {pick['ticker']}: NO DATA "
                    f"— no 5-min bars available; excluded from the "
                    f"record.")
            continue
        entry_rec["grade"] = grade
        graded += 1
        if grade["outcome"] == "NO TRADE":
            rec["no_trades"] = rec.get("no_trades", 0) + 1
            if grade.get("reason") == "open_confirmation":
                lines.append(
                    f"- GRADED {date_str} {pick['ticker']}: NO "
                    f"TRADE — the 9:30 AM open "
                    f"{money(grade['open'])} cleared the plan "
                    f"stop, but the {grade.get('confirm_bar', '')} "
                    f"bar closed "
                    f"{money(grade.get('confirm_close', 0))}, below "
                    f"the rebased stop "
                    f"{money(grade.get('stop', pick['stop']))} — "
                    f"open confirmation failed (v3 layer 2). No "
                    f"modeled position — excluded from the "
                    f"record, no modeled P/L.")
            else:
                lines.append(
                    f"- GRADED {date_str} {pick['ticker']}: NO TRADE "
                    f"— the 9:30 AM open {money(grade['open'])} "
                    f"printed below the plan stop "
                    f"{money(grade['plan_stop'])} (premarket plan "
                    f"entry {money(pick['entry'])}). V2 rule: no "
                    f"modeled position — excluded from the record, "
                    f"no modeled P/L.")
            continue
        if grade["outcome"] == "WIN":
            rec["wins"] += 1
        elif grade["outcome"] == "LOSS":
            rec["losses"] += 1
        else:
            rec["time_exits"] += 1
        rec["modeled_pl"] = round(rec.get("modeled_pl", 0.0)
                                  + grade["pl"], 2)
        lines.append(
            f"- GRADED {date_str} {pick['ticker']}: "
            f"{grade['outcome']} — modeled entry (9:30 AM open) "
            f"{money(grade.get('entry', pick['entry']))}, "
            f"target {money(grade.get('target', pick['target']))}, "
            f"stop {money(grade.get('stop', pick['stop']))}; "
            f"exit {money(grade['exit_price'])}"
            f" at {grade['exit_time']} ({grade['note']}). "
            f"Modeled P/L {money(grade['pl'])} on $10,000 flat "
            f"(5-min Yahoo bars — modeled, not a real fill).")
    if graded:
        head = (f"## {now_stamp()} — Half Percent Club grading: "
                f"{graded} candidate{'s' if graded != 1 else ''} "
                f"graded")
        lines = [head] + lines + [f"- {record_line(meta)}"]
    return lines


def build_candidates(today):
    """Merged candidate map: symbol -> working dict."""
    earn_today = fetch_earnings(today.isoformat())
    yday = today - dt.timedelta(days=1)
    earn_yday = fetch_earnings(yday.isoformat())
    earnings = dict(earn_yday)
    earnings.update(earn_today)  # today's listing wins on overlap
    gainers = fetch_screener("day_gainers", 25)
    actives = fetch_screener("most_actives", 25)
    cands = {}
    for sym, q in {**actives, **gainers}.items():
        cands[sym] = {"ticker": sym,
                      "name": q.get("longName") or q.get("shortName")
                      or sym,
                      "quote_type": q.get("quoteType"),
                      "mcap": q.get("marketCap"),
                      "avg_vol": q.get("averageDailyVolume3Month"),
                      "pre_pct": q.get("preMarketChangePercent"),
                      "pre_price": q.get("preMarketPrice"),
                      "in_gainers": sym in gainers}
    for sym, e in earnings.items():
        c = cands.setdefault(sym, {"ticker": sym, "name": e["name"],
                                   "quote_type": None, "mcap": None,
                                   "avg_vol": None, "pre_pct": None,
                                   "pre_price": None,
                                   "in_gainers": False})
        c["earnings"] = e
        if c.get("mcap") is None:
            c["mcap"] = e.get("mcap")

    def rank(c):
        # earnings names first by market cap (the $1B+ reporters are
        # the plausible picks), then screener names by Yahoo's
        # premarket % — the cap keeps the Nasdaq-call count sane.
        if c.get("earnings"):
            return (0, -(c.get("mcap") or 0))
        return (1, -(c.get("pre_pct") or -999))

    ordered = sorted(cands.values(), key=rank)
    return ordered[:MAX_CANDIDATES], bool(earn_today or gainers
                                          or actives)


def evaluate(cand, today):
    """Fetch live premarket data + apply filters.
    Returns (ok, fresh, detail_dict, fail_reason)."""
    sym = cand["ticker"]
    if not re.fullmatch(r"[A-Z]{1,5}", sym):
        return False, None, None, "share-class/odd symbol"
    name_up = (cand.get("name") or "").upper()
    if any(b in name_up for b in NAME_BLOCKLIST):
        return False, None, None, "ETF/fund-style name"
    if cand.get("quote_type") not in (None, "EQUITY"):
        return False, None, None, f"quote type {cand.get('quote_type')}"
    if cand.get("mcap") is not None and cand["mcap"] <= MIN_MCAP:
        return False, None, None, "market cap not over $1B"
    info = fetch_info(sym)
    if not info:
        return False, None, None, "Nasdaq quote unreadable"
    pd = info["primaryData"]
    price = parse_money(pd.get("lastSalePrice"))
    pct = parse_pct(pd.get("percentageChange"))
    vol = parse_money(pd.get("volume")) or 0
    bid = parse_money(pd.get("bidPrice"))
    ask = parse_money(pd.get("askPrice"))
    stamp = pd.get("lastTradeTimestamp") or ""
    fresh = fresh_stamp(today) in stamp
    detail = {"price": price, "pct": pct, "vol": int(vol),
              "bid": bid, "ask": ask, "stamp": stamp,
              "status": info.get("marketStatus") or "",
              "company": info.get("companyName") or cand["name"]}
    if price is None or pct is None:
        return False, fresh, detail, "no premarket price/change"
    if not (MIN_PRICE <= price <= MAX_PRICE):
        return False, fresh, detail, f"price {money(price)} outside " \
                                     f"$10-$500"
    mcap = cand.get("mcap")
    if not mcap or mcap <= MIN_MCAP:
        return False, fresh, detail, "market cap not over $1B " \
                                     "(or unverified)"
    if pct < MIN_MOVE_PCT:
        return False, fresh, detail, f"premarket move {pct:+.2f}% " \
                                     f"under +3%"
    if pct > MAX_MOVE_PCT:
        return False, fresh, detail, f"premarket move {pct:+.2f}% " \
                                     f"over the +12% cap (fade " \
                                     f"territory - v2)"
    if bid and ask and ask > bid:
        spread_pct = (ask - bid) / price * 100.0
        detail["spread_pct"] = round(spread_pct, 2)
        if spread_pct > MAX_SPREAD_PCT:
            return False, fresh, detail, f"spread {spread_pct:.2f}% " \
                                         f"over 1%"
    min_vol = MIN_VOL_CATALYST if cand.get("earnings") \
        else MIN_VOL_PLAIN
    if vol < min_vol:
        return False, fresh, detail, f"premarket volume {int(vol):,} " \
                                     f"under {min_vol:,}"
    return True, fresh, detail, ""


def recheck():
    """V3 layer 1 — 9:28 AM ET pre-bell re-check of today's pick.

    Re-prices today's stored pick off Nasdaq quote info (the same
    source the 09:00 scan uses) and reads the gap vs prior close.
    CANCELS the pick before the bell if the gap is outside the
    +3%..+12% band or the price is below the plan stop; the
    cancel is banked as a delivery note and the pick grades NO
    TRADE next morning. An unreadable or stale quote NEVER
    cancels — the pick stands, marked unverified, nothing
    delivered. A pass is recorded and stays silent. No pick
    stored -> exit silently.
    V3.1: the re-check only EVALUATES inside the 09:28–09:35
    ET validity window (in_recheck_window); outside it this
    run is a complete no-op — state untouched, no recheck
    keys written, no delivery — so a scheduler catch-up fire
    after a restart can never cancel a pick on a stale quote.
    """
    now = dt.datetime.now(ET)
    if not in_recheck_window(now):
        print(f"half-percent recheck: {now:%H:%M} ET is "
              f"outside the 09:28-09:35 ET validity window "
              f"— no-op, nothing touched.")
        return 0
    today = now.date()
    today_str_ = today.isoformat()
    if today.weekday() >= 5:
        print("half-percent recheck: weekend — no run.")
        return 0
    state = load_state(SCAN)
    entry_rec = state.get("entries", {}).get(today_str_)
    if not entry_rec or entry_rec.get("kind") != "pick":
        print(f"half-percent recheck: no pick stored for "
              f"{today_str_} — nothing to re-check.")
        return 0
    pick = entry_rec.get("pick") or {}
    if pick.get("recheck"):
        print("half-percent recheck: already re-checked today "
              "— no-op.")
        return 0
    checked_at = now.isoformat(timespec="seconds")
    info = fetch_info(pick["ticker"])
    price = pct = None
    fresh = False
    if info:
        pdata = info["primaryData"]
        price = parse_money(pdata.get("lastSalePrice"))
        pct = parse_pct(pdata.get("percentageChange"))
        stamp = pdata.get("lastTradeTimestamp") or ""
        fresh = fresh_stamp(today) in stamp
    if price is None or pct is None or not fresh:
        pick["recheck"] = {
            "checked_at": checked_at, "result": "unverified",
            "reason": "quote unreadable or stale at the 9:28 AM "
                      "re-check — pick stands"}
        save_state(SCAN, state)
        print("half-percent recheck: quote unverified — pick "
              "stands, no delivery.")
        return 0
    reason = None
    if pct < MIN_MOVE_PCT or pct > MAX_MOVE_PCT:
        reason = (f"gap {pct:+.2f}% is outside the +3%..+12% "
                  f"band at 9:28 AM")
    elif price < pick["stop"]:
        reason = (f"price {money(price)} is below the plan stop "
                  f"{money(pick['stop'])} at 9:28 AM")
    if reason is None:
        pick["recheck"] = {"checked_at": checked_at,
                           "price": price, "gap_pct": pct,
                           "result": "pass"}
        save_state(SCAN, state)
        print(f"half-percent recheck: PASS — {pick['ticker']} "
              f"{money(price)} ({pct:+.2f}%) at 9:28 AM; pick "
              f"stands, silent.")
        return 0
    pick["recheck"] = {"checked_at": checked_at, "price": price,
                       "gap_pct": pct, "result": "fail",
                       "reason": reason}
    pick["status"] = "cancelled"
    save_state(SCAN, state)
    lines = [
        f"## {now_stamp()} — Half Percent Club: 1 candidate "
        f"CANCELLED at the 9:28 AM re-check",
        f"- PICK CANCELLED: {pick['ticker']} "
        f"({pick['company']}) — do NOT trade it today. Stand "
        f"down before the bell.",
        f"- Re-check at 9:28 AM ET (Nasdaq quote info): "
        f"{money(price)} ({pct:+.2f}% vs prior close). Fail "
        f"reason: {reason}.",
        f"- This morning's plan was entry {money(pick['entry'])}, "
        f"target {money(pick['target'])}, stop "
        f"{money(pick['stop'])}. The v3 pre-bell re-check killed "
        f"it — head-fake protection working as designed.",
        "- The cancelled pick grades NO TRADE tomorrow morning "
        "(excluded from the record, no modeled P/L).",
        f"- {record_line(state.setdefault('meta', {}))}",
    ]
    bank(SCAN, lines, date=today_str_)
    print(f"half-percent recheck: FAIL — {pick['ticker']} "
          f"cancelled ({reason}); delivery banked.")
    return 0


def main():
    if "--recheck" in sys.argv[1:]:
        return recheck()
    now = dt.datetime.now(ET)
    today = now.date()
    today_str_ = today.isoformat()
    if today.weekday() >= 5:
        print("half-percent: weekend — no run.")
        return 0
    state = load_state(SCAN)
    entries = state.setdefault("entries", {})

    # 1) grade prior picks first
    grade_lines = grade_pending(state, today_str_)
    if grade_lines:
        bank(SCAN, grade_lines, date=today_str_)

    # 2) today's pick (once per day — re-runs are no-ops)
    if today_str_ in entries:
        save_state(SCAN, state)
        print(f"half-percent: {today_str_} already recorded "
              f"({entries[today_str_].get('kind')}); grading done.")
        return 0

    cands, any_source = build_candidates(today)
    if not any_source:
        reason = "all public sources unreadable this morning"
        entries[today_str_] = {"kind": "no_pick", "reason": reason}
        bank(SCAN, [f"## {now_stamp()} — Half Percent Club: NO PICK",
                    f"- Reason: {reason}. No trade today."],
             date=today_str_)
        save_state(SCAN, state)
        print(f"half-percent: NO PICK — {reason}")
        return 0

    passed, near, any_fresh = [], [], False
    for cand in cands:
        ok, fresh, detail, why = evaluate(cand, today)
        if fresh:
            any_fresh = True
        if ok and fresh:
            passed.append((cand, detail))
        elif detail is not None:
            near.append((cand["ticker"], why))

    if not any_fresh:
        reason = ("no fresh premarket data this morning (market "
                  "may be closed)")
        entries[today_str_] = {"kind": "no_pick", "reason": reason}
        bank(SCAN, [f"## {now_stamp()} — Half Percent Club: NO PICK",
                    f"- Reason: {reason}. No trade today."],
             date=today_str_)
        save_state(SCAN, state)
        print(f"half-percent: NO PICK — {reason}")
        return 0

    if not passed:
        top = "; ".join(f"{t} ({w})" for t, w in near[:4]) or "none"
        reason = ("nothing cleared the bar (price $10-$500, cap "
                  ">$1B, premarket +3% to +12%, spread <= 1%, "
                  "volume bar). Near misses: " + top)
        entries[today_str_] = {"kind": "no_pick", "reason": reason}
        bank(SCAN, [f"## {now_stamp()} — Half Percent Club: NO PICK",
                    f"- {reason}",
                    "- No trade today — the system never forces one."],
             date=today_str_)
        save_state(SCAN, state)
        print("half-percent: NO PICK — nothing cleared the bar.")
        return 0

    passed.sort(key=lambda cd: (0 if cd[0].get("earnings") else 1,
                                -(cd[1]["pct"] or 0),
                                -cd[1]["vol"]))
    cand, d = passed[0]
    sym = cand["ticker"]
    ref = round(d["price"], 2)
    target = round(ref * (1 + TARGET_PCT), 2)
    stop = round(ref * (1 - STOP_PCT), 2)
    zone_lo, zone_hi = round(ref * 0.998, 2), round(ref * 1.002, 2)
    earn = cand.get("earnings")
    if earn:
        when = {"time-pre-market": "pre-market",
                "time-after-hours": "after hours",
                "time-not-supplied": "time not supplied"}.get(
                    earn["when"], earn["when"])
        day_word = "today" if earn["date"] == today_str_ else "yesterday"
        catalyst = (f"Earnings on Nasdaq's calendar for {day_word} "
                    f"({earn['date']}, {when}) — reported results / "
                    f"guidance are the catalyst to verify at the book")
        catalyst_src = "Nasdaq earnings calendar"
    else:
        catalyst = (f"Premarket momentum — {d['pct']:+.2f}% this "
                    f"morning on {d['vol']:,} premarket shares "
                    f"(no earnings listing found for today/yesterday; "
                    f"underlying news not independently verified)")
        catalyst_src = "Nasdaq quote info / Yahoo Finance screeners"
    pick = {"ticker": sym, "company": d["company"], "entry": ref,
            "target": target, "stop": stop,
            "catalyst": catalyst, "pct": d["pct"], "vol": d["vol"],
            "mcap": cand.get("mcap")}
    entries[today_str_] = {"kind": "pick", "pick": pick, "grade": None}
    lines = [
        f"## {now_stamp()} — Half Percent Club: 1 candidate",
        f"- PICK: {sym} ({d['company']}) — LONG, one trade today, "
        f"target +0.5%, then done.",
        f"- Catalyst: {catalyst} (source: {catalyst_src}).",
        f"- Premarket: {money(ref)} ({d['pct']:+.2f}% vs prior "
        f"close), {d['vol']:,} shares traded premarket, status "
        f"{d['status']}, last trade {d['stamp']} "
        f"(source: Nasdaq quote info).",
        f"- Market cap: ${cand['mcap'] / 1e9:,.2f}B "
        f"(Nasdaq earnings calendar / Yahoo Finance).",
    ]
    if d.get("spread_pct") is not None:
        lines.append(f"- Spread at read: bid {money(d['bid'])} / ask "
                     f"{money(d['ask'])} ({d['spread_pct']:.2f}%).")
    lines += [
        f"- ENTRY PLAN: enter at the open / first minutes inside "
        f"{money(zone_lo)}-{money(zone_hi)} (reference {money(ref)}). "
        f"Modeled grading (v3): the actual 9:30 AM open is the "
        f"entry, with target/stop rebased +0.5%/-0.5% off that "
        f"open.",
        f"- V3 HEAD-FAKE CHECKS: at 9:28 AM ET the pick is "
        f"re-checked — if the gap has left the +3%..+12% band or "
        f"the price is under the plan stop, the pick is "
        f"CANCELLED before the bell (a cancel note lands here). "
        f"And the trade only counts if the first two 5-min bars "
        f"close at/above the rebased stop (open confirmation); "
        f"otherwise it grades NO TRADE.",
        f"- PLAN TARGET {money(target)} (+0.5% off reference) — "
        f"PLAN STOP {money(stop)} (-0.5%) — TIME STOP: out by "
        f"10:00 AM ET if neither hits. If the 9:30 AM open prints "
        f"below the plan stop, the day grades NO TRADE (no "
        f"modeled position).",
        f"- Modeled size: $10,000 flat for the running record. "
        f"Michael places his own trade at his own small live size — "
        f"Chet never places trades.",
        f"- {record_line(state.setdefault('meta', {}))}",
        "- Strategy source: The Half Percent Club reel "
        "(DfX8wkcxURK). The creator's streak / YTD claims are "
        "UNVERIFIED.",
    ]
    if near:
        lines.append("- Also screened, did not clear: "
                     + "; ".join(f"{t} ({w})" for t, w in near[:5])
                     + ".")
    bank(SCAN, lines, date=today_str_)
    save_state(SCAN, state)
    print(f"half-percent: PICK {sym} ref {money(ref)} "
          f"target {money(target)} stop {money(stop)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
