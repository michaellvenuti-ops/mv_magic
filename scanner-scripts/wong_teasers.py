#!/usr/bin/env python3
"""Wong teaser scan (PC port, 2026-11-06) — Spanky / Be Better
Bettors Ep 14 + Ep 3, twin of the Muse-side weekly Saturday scan.

The Wong edge: a 6-point teaser leg is +EV when it crosses BOTH key
numbers 3 and 7 — underdogs +1.5/+2/+2.5 tease up to +7.5/+8/+8.5,
favorites -7.5/-8/-8.5 tease down to -1.5/-2/-2.5. Construction:
round-robin ALL qualifying teams through 3 and 7; prefer the LOWEST
-total games (a point is worth more at total 37 than at total 85).

Data: ScoresAndOdds public NFL board via so_board (median spread +
median total across the board's books). Games already kicked off
are skipped (ESPN started check, shared with the other public
scans). Teaser PRICING: DarkHorse is checked for any teaser price
actually shown; if none is shown the output says so plainly — a
teaser price is NEVER invented. Paper flags only — never places a
bet. Michael places his own wagers.

Output: data/wong-teasers-<date>.md (banked card when >= 2 legs
qualify; a quiet "0 deliverables" line when fewer do).
Scheduler: weekly Sat 11:44 ET (the Muse twin stays ON during
testing; this PC run is the calibration twin).
"""
import itertools
import sys

from odds_common import bank, game_started, now_stamp, started_matchups
from so_board import BoardUnreadable, load_board, medians

SCAN = "wong-teasers"
WONG_DOG = {1.5, 2.0, 2.5}        # tease +6 -> +7.5/+8/+8.5
WONG_FAV = {-7.5, -8.0, -8.5}     # tease +6 -> -1.5/-2/-2.5
PRIME_TOTAL = 42.0


def teaser_leg(team, spread, total):
    """Qualifying Wong leg dict or None. Teasing moves a side 6
    points toward itself: spread + 6 for either orientation."""
    if spread in WONG_DOG or spread in WONG_FAV:
        return {"team": team, "spread": spread,
                "teased": spread + 6.0, "total": total,
                "prime": (total is not None and total <= PRIME_TOTAL)}
    return None


def fmt(x):
    return f"{x:+g}" if isinstance(x, (int, float)) else "n/a"


def darkhorse_teaser_note(legs_exist):
    """Best-effort DarkHorse teaser-price check, mirroring the Muse
    scan's step 4: report a teaser price only when one is actually
    shown. Never invents one."""
    if not legs_exist:
        return None
    try:
        from dh_common import FINDER_URLS, open_session, signed_in
        pw, ctx = open_session()
        try:
            page = ctx.new_page()
            if not signed_in(page, FINDER_URLS["browse_odds"]):
                return ("DarkHorse check unavailable (signed out) — "
                        "teaser prices not shown.")
            page.wait_for_timeout(3000)
            text = page.locator("body").inner_text(timeout=20000)
            low = text.lower()
            if "teaser" in low:
                i = low.find("teaser")
                snippet = " ".join(text[max(0, i - 60):i + 120].split())
                return f"DarkHorse mentions teasers: ...{snippet}..."
            return ("DarkHorse shows no teaser prices on the odds "
                    "board — none reported (never invented).")
        finally:
            ctx.close()
            pw.stop()
    except Exception as exc:  # noqa: BLE001 - pricing check is a nicety
        brief = " ".join(str(exc).split())[:140]
        return f"DarkHorse teaser check failed ({brief}) — no price shown."


def main():
    try:
        games, _html = load_board("NFL")
    except BoardUnreadable as exc:
        bank(SCAN, [f"## {now_stamp()} — wong-teasers: 0 deliverables "
                    f"(board unreadable: {exc})"])
        print(f"wong-teasers: board unreadable — {exc}")
        return 0
    started = started_matchups(["NFL"])
    legs = []
    seen_games = 0
    for game in games:
        if game_started(game, started):
            continue
        seen_games += 1
        home_spread, total = medians(game)
        if home_spread is None:
            continue
        for team, spread in ((game["home"], home_spread),
                             (game["away"], -home_spread)):
            leg = teaser_leg(team, spread, total)
            if leg:
                leg["matchup"] = f"{game['away']} @ {game['home']}"
                legs.append(leg)
    legs.sort(key=lambda l: (l["total"] is None,
                             l["total"] if l["total"] is not None else 0))
    if len(legs) < 2:
        bank(SCAN, [f"## {now_stamp()} — wong-teasers: 0 deliverables "
                    f"({len(legs)} qualifying leg(s) from {seen_games} "
                    f"upcoming games; a teaser needs two legs)"])
        print(f"wong-teasers: {len(legs)} qualifying leg(s) — silent.")
        return 0
    pairs = list(itertools.combinations(legs, 2))
    pairs.sort(key=lambda p: ((p[0]["total"] or 999)
                              + (p[1]["total"] or 999)))
    triples = (list(itertools.combinations(legs, 3))
               if len(legs) >= 4 else [])
    lines = [f"## {now_stamp()} — WONG TEASERS (PC calibration): "
             f"{len(legs)} qualifying legs, {len(pairs)} round-robin "
             f"pairs" + (f", {len(triples)} 3-team groupings"
                         if triples else "")]
    for leg in legs:
        lines.append(
            f"- LEG: {leg['team']} {fmt(leg['spread'])} -> "
            f"{fmt(leg['teased'])} (crosses 3 and 7) — "
            f"{leg['matchup']}, game total {leg['total']}"
            + (" — PRIME (total <= 42)" if leg["prime"] else ""))
    for a, b in pairs:
        lines.append(f"- PAIR: {a['team']} {fmt(a['teased'])} + "
                     f"{b['team']} {fmt(b['teased'])}")
    for t in triples:
        lines.append("- TRIPLE: " + " + ".join(
            f"{x['team']} {fmt(x['teased'])}" for x in t))
    note = darkhorse_teaser_note(True)
    if note:
        lines.append(f"- TEASER PRICING: {note}")
    bank(SCAN, lines)
    print(f"wong-teasers: {len(legs)} legs, {len(pairs)} pairs, "
          f"{len(triples)} triples banked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
