#!/usr/bin/env python3
"""DarkHorse shots-on-goal (SOG) middles (PC port; added live Oct 31,
2026, after the v3 package shipped — new in v4).

Rules (identical to Chet's scan):
  - Middles finder, Market Type = HOCKEY PROPS only,
  - window (width) >= 1,
  - both legs priced -200..+200 inclusive (manual band — Sportsbook
    Target stays Any Book, so the tool's hedge-odds filter is off),
  - both-sides -140-or-better = "clean" tier flag (a flag, not a
    filter — dirtier pairs still bank/deliver, labeled),
  - pages 1-10 at max items-per-page, COMPACT reporting,
  - Smaller Bets pass (dh_middles.collect_with_smaller), mode
    restored afterwards,
  - basis: one day of closing data (Oct 30, 2026) showed line-1.5
    players landing on exactly 2 shots 34.2% of the time — a signal,
    not a law.
State: data/sog-middles-state.json.
Banked: data/sog-middles-<date>.md. Alert-only: NEW/CHANGED print.

CALIBRATION: the Market Type filter control is set best-effort; if
the click does not land, rows are still filtered in code by the
market label ("shot" must appear in the market when a label parsed).
Run --dump on the first pass and send the dump back for a patch.
"""
import argparse
import sys

from dh_common import (FINDER_URLS, open_session, signed_in,
                       stable_row_identity, strip_volatile)
from dh_middles import collect_with_smaller, price_in
from odds_common import bank, now_stamp, today_str
from state import load_state, save_state

SCAN = "sog-middles"
MIN_WINDOW = 1.0
PRICE_LO, PRICE_HI = -200, 200
CLEAN = -140


def try_set_hockey_props(page):
    """Best-effort: set Market Type = Hockey Props. View/filter
    controls are safe to change (same class as Items-per-page);
    the Include Player Props toggle is NEVER touched."""
    for label in ("Hockey Props", "Hockey"):
        try:
            page.get_by_text(label, exact=False).first.click(timeout=3000)
            page.wait_for_timeout(1200)
            return True
        except Exception:
            continue
    print("sog-middles: could not set the Hockey Props market filter "
          "by label — relying on the in-code market check; send a "
          "--dump back for a selector patch.")
    return False


def middle_key(mid):
    """Stable state key: row identity (event/market/line text from
    the row's non-bet cells — dh_common.stable_row_identity) + the
    two leg books. The old key used player/market fields the parser
    never populates plus the parsed 'line' values, so price moves
    re-keyed known middles as NEW (fix 2026-11-05, same as
    dh_pairs.pair_key)."""
    l1, l2 = mid["leg1"], mid["leg2"]
    ident = stable_row_identity(mid.get("cells"))
    if not ident:
        ident = (f"{mid.get('player', '')} | {mid.get('market', '')} | "
                 f"{strip_volatile(l1['side'])} | "
                 f"{strip_volatile(l2['side'])}")
    return f"{ident} | {l1['book']} | {l2['book']}"


def qualifies(mid):
    if mid["window"] < MIN_WINDOW:
        return False
    market = (mid.get("market") or "").lower()
    if market and "shot" not in market:
        return False
    return (price_in(mid["leg1"]["price"], PRICE_LO, PRICE_HI)
            and price_in(mid["leg2"]["price"], PRICE_LO, PRICE_HI))


def is_clean(mid):
    try:
        return (int(mid["leg1"]["price"]) >= CLEAN
                and int(mid["leg2"]["price"]) >= CLEAN)
    except (TypeError, ValueError):
        return False


def leg_line(leg):
    return (f"{leg['side']} {leg['line']} {leg['price']} "
            f"@ {leg['book']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--no-smaller", action="store_true")
    args = ap.parse_args()
    state = load_state(SCAN)
    pw, ctx = open_session()
    try:
        page = ctx.new_page()
        if not signed_in(page, FINDER_URLS["middles"]):
            print("DarkHorse check unavailable (signed out) — "
                  "sog-middles skipped, no prices invented.")
            return 0
        try_set_hockey_props(page)
        if args.no_smaller:
            from dh_middles import collect_middles
            full, _ = collect_middles(page)
            smaller = []
        else:
            full, smaller = collect_with_smaller(page)
        if not full and not smaller:
            print("sog-middles: zero middles parsed — run with a "
                  "dump and send it back for a parser patch. No "
                  "state written.", file=sys.stderr)
            return 2
        deliverables = []
        lines = [f"## {now_stamp()} — sog-middles: {len(full)} "
                 f"full-size + {len(smaller)} smaller-bets rows "
                 f"parsed (compact: qualifying only below)"]
        for mid in full + smaller:
            if not qualifies(mid):
                continue
            key = middle_key(mid)
            entry = {"player": mid.get("player", ""),
                     "market": mid.get("market", ""),
                     "game": mid.get("game", ""),
                     "window": mid["window"],
                     "conversion_pct": mid["conversion_pct"],
                     "clean_tier": is_clean(mid),
                     "headline": {"leg1": mid["leg1"],
                                  "leg2": mid["leg2"]},
                     "smaller_bets": bool(mid.get("smaller_bets")),
                     "first_reported": now_stamp(),
                     "last_seen": today_str()}
            prior = state["entries"].get(key)
            if prior is None:
                state["entries"][key] = entry
                deliverables.append(("NEW", key, entry))
            else:
                prior["last_seen"] = today_str()
                changed = (not prior.get("clean_tier")
                           and entry["clean_tier"])
                try:
                    changed = changed or any(
                        int(entry["headline"][l]["price"])
                        - int(prior["headline"][l]["price"]) >= 10
                        for l in ("leg1", "leg2"))
                except (TypeError, ValueError, KeyError):
                    pass
                if changed:
                    prior.update(entry)
                    deliverables.append(("UPDATED", key, entry))
        # Chat-delivery wiring (2026-11-10, Michael's tap): the
        # header carries the NEW/CHANGED count and each deliverable
        # also banks as a uniform NEW MIDDLE / CHANGED MIDDLE line —
        # the steam_side.py pattern the pc-data delivery watcher
        # screens for. The original "- NEW ..." lines stay (boards
        # and the log read them).
        lines[0] += f" — {len(deliverables)} NEW/CHANGED middles"
        for tag, key, e in deliverables:
            h = e["headline"]
            kind = "NEW" if tag == "NEW" else "CHANGED"
            lines.append(
                f"{kind} MIDDLE: {'CLEAN ' if e['clean_tier'] else ''}"
                f"W{e['window']:g} {e['player']} ({e['game']}): "
                f"{leg_line(h['leg1'])} / {leg_line(h['leg2'])}"
                f"{' [SMALLER BETS]' if e['smaller_bets'] else ''}")
            lines.append(
                f"- {tag} {'CLEAN ' if e['clean_tier'] else ''}"
                f"W{e['window']:g} {e['player']} ({e['game']}): "
                f"{leg_line(h['leg1'])} / {leg_line(h['leg2'])}"
                f"{' [SMALLER BETS]' if e['smaller_bets'] else ''}")
        if not deliverables:
            lines.append("- 0 deliverables this run")
        bank(SCAN, lines)
        save_state(SCAN, state)
        for ln in lines:
            print(ln)
        return 0
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
