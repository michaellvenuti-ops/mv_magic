#!/usr/bin/env python3
"""Wide middle builds mined from the PC arbitrage pair states.

Michael's ask (2026-11-11): "build a wide middle out of every
arbitration alert" + "have pc do the scan as well". This scan reads
the pairs already banked by dh_arbitrage.py and dh_kalshi_poly_arb.py
(data/arbitrage-state.json, data/kalshi-poly-arb-state.json), groups
their legs by game + market, and assembles WIDE BUILDS: an Over leg at
a low line paired with an Under leg at a higher line (totals / props),
or the two spread legs whose both-win margin window is widest.

Qualifying bar (Michael's, set 2026-11-10/11):
  - both-win window of 3 to 5 numbers,
  - balanced stakes scaled to ~$200 total (equal leg payouts),
  - miss cost at most ~10% of the ~$200 staked,
  - both legs at standard books with American odds (exchange legs in
    cents are skipped in v1), no BroThrow / Fliff legs,
  - half-point lines only in v1 (integer lines can push and change
    the miss math),
  - game not started yet.

State: data/wide-builds-state.json. Banked: data/wide-builds-<date>.md
with an "N NEW wide builds" header + "NEW BUILD:" lines (the delivery
watcher's signal). Board: wide-builds-board-pc.html via
build_pc_boards.py. Read-only; never places a bet.
"""
import math
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from config import DATA_DIR
from state import load_state, save_state

ET = ZoneInfo("America/New_York")
SCAN = "wide-builds"
SOURCES = ["arbitrage", "kalshi-poly-arb"]
TOTAL_STAKE = 200.0
MAX_MISS = 20.50  # ~10% of $200
EXCLUDED_BOOKS = {"BroThrow", "Fliff"}

TOTAL_RE = re.compile(r"(?:Alt )?Total (Over|Under) (\d+(?:\.\d+)?)\s*$")
SPREAD_RE = re.compile(r"(?:Alt )?Spread (.+?) ([+-]\d+(?:\.\d+)?)\s*$")
PROP_OU_RE = re.compile(r"(.+?) (Over|Under) (\d+(?:\.\d+)?)\s*$")
PROP_ALT_RE = re.compile(r"(.+?) (\d+)\+\s*$")
PRICE_RE = re.compile(r"^[+-]?\d+$")
TODAY_TIME_RE = re.compile(r"(Today|Tomorrow) (\d+):(\d+) (AM|PM)")
DATED_TIME_RE = re.compile(
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-(\d+)"
    r" (\d+):(\d+) (AM|PM)")
MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def dec(price):
    return 1.0 + (price / 100.0 if price > 0 else 100.0 / abs(price))


def parse_start(event_text, now):
    """Kickoff from the identity's date text; None when unparseable."""
    m = TODAY_TIME_RE.search(event_text)
    if m:
        base = now.date() + (timedelta(days=1) if m.group(1) == "Tomorrow"
                             else timedelta())
        hh = int(m.group(2)) % 12 + (12 if m.group(4) == "PM" else 0)
        return datetime(base.year, base.month, base.day, hh,
                        int(m.group(3)), tzinfo=ET)
    m = DATED_TIME_RE.search(event_text)
    if m:
        hh = int(m.group(3)) % 12 + (12 if m.group(5) == "PM" else 0)
        return datetime(now.year, MONTHS[m.group(1)], int(m.group(2)),
                        hh, int(m.group(4)), tzinfo=ET)
    return None


def parse_leg(segment):
    """One market segment -> leg dict or None."""
    seg = segment.strip()
    half = "1st Half" in seg or " 1H" in seg
    m = TOTAL_RE.search(seg)
    if m:
        return {"kind": "total", "side": m.group(1),
                "line": float(m.group(2)), "half": half}
    m = SPREAD_RE.search(seg)
    if m:
        return {"kind": "spread", "side": m.group(1).strip(),
                "line": float(m.group(2)), "half": half}
    m = PROP_ALT_RE.search(seg)
    if m and "Spread" not in seg and "Total" not in seg:
        grp = re.sub(r"^Alt ", "", m.group(1).strip())
        return {"kind": "prop", "side": "Over",
                "line": int(m.group(2)) - 0.5, "group": grp, "half": half}
    m = PROP_OU_RE.search(seg)
    if m and "Spread" not in seg and "Total" not in seg:
        grp = re.sub(r"^Alt ", "", m.group(1).strip())
        return {"kind": "prop", "side": m.group(2),
                "line": float(m.group(3)), "group": grp, "half": half}
    return None


def collect_legs(now):
    """Pool of usable legs grouped by (kind, group key)."""
    today = now.strftime("%Y-%m-%d")
    groups = {}
    for src in SOURCES:
        state = load_state(src)
        for key, entry in (state.get("entries") or {}).items():
            if not isinstance(entry, dict):
                continue
            if entry.get("last_seen") != today:
                continue
            ident = key.split(" | ")[0]
            segs = ident.split(" / ")
            if len(segs) < 2:
                continue
            event = segs[0]
            markets = [s for s in segs[1:] if s != "table"]
            if len(markets) != 2:
                continue
            for idx, seg in enumerate(markets):
                leg_meta = parse_leg(seg)
                leg = entry.get(f"leg{idx + 1}") or {}
                book = str(leg.get("book", "")).strip()
                price_s = str(leg.get("price", "")).strip()
                if not leg_meta or book in EXCLUDED_BOOKS:
                    continue
                if not PRICE_RE.match(price_s):
                    continue
                price = int(price_s)
                if abs(price) < 100:
                    continue
                if leg_meta["line"] % 1 != 0.5 and leg_meta["line"] % 1 != -0.5:
                    if float(leg_meta["line"]).is_integer():
                        continue  # v1: half-point lines only
                rec = {**leg_meta, "book": book, "price": price,
                       "event": event, "src": src}
                if leg_meta["kind"] == "total":
                    gk = ("total", event, leg_meta["half"])
                elif leg_meta["kind"] == "spread":
                    gk = ("spread", event, leg_meta["half"])
                else:
                    gk = ("prop", event, leg_meta["group"], leg_meta["half"])
                pool = groups.setdefault(gk, {})
                pk = (rec["side"], rec["line"], book)
                if pk not in pool or src == "arbitrage":
                    pool[pk] = rec
    return groups


def balanced(leg_a, leg_b):
    """Balanced ~$200 stakes; returns dict or None when the miss is
    too expensive / the land is not profitable."""
    d1, d2 = dec(leg_a["price"]), dec(leg_b["price"])
    payout = TOTAL_STAKE / (1.0 / d1 + 1.0 / d2)
    s1 = round(payout / d1)
    s2 = round(TOTAL_STAKE - s1)
    if s1 <= 0 or s2 <= 0:
        return None
    pay1, pay2 = s1 * d1, s2 * d2
    land = pay1 + pay2 - TOTAL_STAKE
    miss_a = pay1 - TOTAL_STAKE  # leg A wins alone
    miss_b = pay2 - TOTAL_STAKE  # leg B wins alone
    if land <= 0 or -min(miss_a, miss_b) > MAX_MISS:
        return None
    return {"stakes": (s1, s2), "land": round(land, 2),
            "miss_a": round(miss_a, 2), "miss_b": round(miss_b, 2)}


def window_ints(lo_line, hi_line):
    lo = math.floor(lo_line) + 1
    hi = math.ceil(hi_line) - 1
    return list(range(lo, hi + 1)) if hi >= lo else []


def pick_best(cands):
    """cands: (window, math_out, leg_a, leg_b). Prefer the widest
    qualifying window, then the cheapest worst-side miss."""
    if not cands:
        return None
    return max(cands, key=lambda c: (len(c[0]),
                                     min(c[1]["miss_a"], c[1]["miss_b"]),
                                     c[1]["land"]))


def assemble(groups, now):
    builds = []
    for gk, pool in groups.items():
        legs = list(pool.values())
        kind = gk[0]
        event = gk[1]
        start = parse_start(event, now)
        if start is not None and start <= now:
            continue
        if kind in ("total", "prop"):
            overs = [l for l in legs if l["side"] == "Over"]
            unders = [l for l in legs if l["side"] == "Under"]
            cands = []
            for a in overs:
                for b in unders:
                    window = window_ints(a["line"], b["line"])
                    if not (3 <= len(window) <= 5):
                        continue
                    math_out = balanced(a, b)
                    if math_out:
                        cands.append((window, math_out, a, b))
            best = pick_best(cands)
            if not best:
                continue
            window, math_out, a, b = best
            market = "Total" if kind == "total" else gk[2]
            builds.append(make_build(
                event, market, None,
                {"side": f"Over {a['line']:g}", "book": a["book"],
                 "price": a["price"]},
                {"side": f"Under {b['line']:g}", "book": b["book"],
                 "price": b["price"]},
                window, math_out, a, b, start,
                miss_low=math_out["miss_b"], miss_high=math_out["miss_a"]))
        else:  # spread
            favs = [l for l in legs if l["line"] < 0]
            dogs = [l for l in legs if l["line"] > 0]
            cands = []
            for fav in favs:
                for dog in dogs:
                    if fav["side"] == dog["side"]:
                        continue
                    window = window_ints(abs(fav["line"]), dog["line"])
                    if not (3 <= len(window) <= 5):
                        continue
                    math_out = balanced(fav, dog)
                    if math_out:
                        cands.append((window, math_out, fav, dog))
            best = pick_best(cands)
            if not best:
                continue
            window, math_out, fav, dog = best
            builds.append(make_build(
                event, "Spread", fav["side"],
                {"side": f"{fav['side']} {fav['line']:+g}",
                 "book": fav["book"], "price": fav["price"]},
                {"side": f"{dog['side']} +{dog['line']:g}",
                 "book": dog["book"], "price": dog["price"]},
                window, math_out, fav, dog, start,
                miss_low=math_out["miss_b"], miss_high=math_out["miss_a"]))
    return builds


def make_build(event, market, margin_team, leg1, leg2, window, math_out,
               src_a, src_b, start, miss_low, miss_high):
    s1, s2 = math_out["stakes"]
    leg1 = {**leg1, "stake": float(s1)}
    leg2 = {**leg2, "stake": float(s2)}
    league = event.split(" ")[0] if event else ""
    srcs = sorted({src_a["src"], src_b["src"]})
    return {
        "game": event, "league": league,
        "game_date": (start.strftime("%Y-%m-%d") if start else
                      datetime.now(ET).strftime("%Y-%m-%d")),
        "tipoff_et": (start.strftime("%I:%M %p").lstrip("0") if start
                      else ""),
        "market": market, "margin_team": margin_team,
        "leg1": leg1, "leg2": leg2,
        "total_staked": TOTAL_STAKE, "window": window,
        "miss_pl": {"low": miss_low, "high": miss_high},
        "land_profit": math_out["land"],
        "source_scan": (" + ".join(srcs)),
    }


def build_key(b):
    return (f"{b['game']}|{b['market']}|{b['leg1']['side']}"
            f"|{b['leg2']['side']}")


def build_line(b):
    win = ", ".join(str(w) for w in b["window"])
    return (f"{b['game']} — {b['market']} — "
            f"{b['leg1']['side']} {b['leg1']['book']} "
            f"{b['leg1']['price']:+d} (${b['leg1']['stake']:,.0f}) / "
            f"{b['leg2']['side']} {b['leg2']['book']} "
            f"{b['leg2']['price']:+d} (${b['leg2']['stake']:,.0f}) — "
            f"both win {win} — land +${b['land_profit']:,.2f}, "
            f"miss {b['miss_pl']['low']:+,.2f} / "
            f"{b['miss_pl']['high']:+,.2f} on ~$200 balanced")


def main():
    now = datetime.now(ET)
    today = now.strftime("%Y-%m-%d")
    groups = collect_legs(now)
    builds = assemble(groups, now)
    state = load_state(SCAN)
    entries = state.setdefault("entries", {})
    new_builds = []
    for b in builds:
        k = build_key(b)
        if k in entries:
            entries[k]["last_seen"] = today
        else:
            b["first_reported"] = now.isoformat(timespec="seconds")
            b["last_seen"] = today
            entries[k] = b
            new_builds.append(b)
    save_state(SCAN, state)

    stamp = now.isoformat(timespec="seconds")
    lines = [f"## {stamp} — wide-builds: "
             f"{len(new_builds)} NEW wide builds"]
    for b in new_builds:
        lines.append("NEW BUILD: " + build_line(b))
    lines.append("(end of new builds — full list below)")
    for k, b in entries.items():
        if isinstance(b, dict) and b.get("last_seen") == today:
            lines.append(build_line(b))
    out_path = Path(DATA_DIR) / f"{SCAN}-{today}.md"
    with open(out_path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"wide_builds: {len(builds)} qualifying builds, "
          f"{len(new_builds)} NEW (banked {out_path.name})")
    for b in new_builds:
        print("NEW BUILD: " + build_line(b))


if __name__ == "__main__":
    main()
