#!/usr/bin/env python3
"""DarkHorse promo converter — ON-DEMAND CLI (PC port).

Usage:
  python dh_promo_convert.py --type bonus-bet --book FanDuel --amount 100
  python dh_promo_convert.py --type profit-boost --book FanDuel \
      --amount 50 --boost 50
  python dh_promo_convert.py --type site-credit --book FanDuel --amount 100
  python dh_promo_convert.py --type second-chance --book FanDuel --amount 100
  python dh_promo_convert.py --type qualifying-bet --book FanDuel \
      --amount 100

Runs the matching DarkHorse converter finder with the book + amount
(+ boost %) exactly as given and prints the BEST NJ-placeable hedge
pair with its displayed conversion % and converted $. Hedge legs on
Polymarket International are EXCLUDED (not placeable from NJ) — the
best NJ-only conversion can be lower than the tool's displayed top
conversion, and the output says so when the top row was skipped.
Read-only; never places a bet. Amounts are never invented: all three
of type/book/amount come from the command line (Michael's tap).
"""
import argparse
import sys

from dh_common import FINDER_URLS, open_session, signed_in
from dh_pairs import collect_pairs, placeable

TYPE_TO_FINDER = {
    "profit-boost": "profit_boost",
    "bonus-bet": "bonus_bet",
    "site-credit": "site_credit",
    "second-chance": "second_chance",
    "qualifying-bet": "qualifying_bet",
}


def fill_converter_form(page, book, amount, boost):
    """Best-effort form fill (calibration surface): set the sportsbook
    select to `book`, the amount input to `amount`, the boost input
    when present. View inputs only — no account settings touched."""
    try:
        selects = page.locator("select")
        for i in range(selects.count()):
            opts = selects.nth(i).locator("option")
            labels = [opts.nth(j).inner_text() for j in range(opts.count())]
            if any(book.lower() in (lab or "").lower() for lab in labels):
                selects.nth(i).select_option(label=next(
                    lab for lab in labels
                    if book.lower() in (lab or "").lower()))
                break
    except Exception:  # noqa: BLE001
        pass
    try:
        inputs = page.locator("input[type='number'], input[inputmode]")
        if inputs.count():
            inputs.first.fill(str(amount))
        if boost is not None and inputs.count() > 1:
            inputs.nth(1).fill(str(boost))
        page.wait_for_timeout(3000)
    except Exception:  # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", required=True,
                    choices=sorted(TYPE_TO_FINDER))
    ap.add_argument("--book", required=True)
    ap.add_argument("--amount", required=True, type=float)
    ap.add_argument("--boost", type=float, default=None,
                    help="boost %% (profit-boost type)")
    args = ap.parse_args()
    pw, ctx = open_session()
    try:
        page = ctx.new_page()
        url = FINDER_URLS[TYPE_TO_FINDER[args.type]]
        if not signed_in(page, url):
            print("DarkHorse check unavailable (signed out) — "
                  "converter skipped, no prices invented.")
            return 0
        fill_converter_form(page, args.book, args.amount, args.boost)
        pairs, _raw = collect_pairs(page)
        skipped_top = False
        best = None
        for pair in pairs:
            conv = max(pair["pcts"]) if pair["pcts"] else None
            if best is None and not placeable(pair):
                skipped_top = True
            if placeable(pair):
                best = (pair, conv)
                break
        if best is None:
            print(f"No NJ-placeable hedge pair found for "
                  f"{args.type} at {args.book} (${args.amount:g}). "
                  f"Pairs with Polymarket Intl. hedges were excluded.")
            return 0
        pair, conv = best
        converted = (args.amount * conv / 100.0) if conv else None
        print(f"BEST NJ-PLACEABLE HEDGE — {args.type} at {args.book}, "
              f"${args.amount:g}"
              + (f", boost {args.boost:g}%" if args.boost else ""))
        print(f"  Leg 1: {pair['leg1']['side']} {pair['leg1']['price']} "
              f"at {pair['leg1']['book']} (stake {pair['leg1']['stake']})")
        print(f"  Leg 2: {pair['leg2']['side']} {pair['leg2']['price']} "
              f"at {pair['leg2']['book']} (stake {pair['leg2']['stake']})")
        if conv is not None:
            print(f"  Conversion: {conv:g}%"
                  + (f" -> ${converted:,.2f} converted"
                     if converted is not None else ""))
        if skipped_top:
            print("  Note: the tool's top pair was skipped (hedge leg "
                  "not NJ-placeable); this is the best NJ-only pair, "
                  "so the conversion may be lower than displayed top.")
        return 0
    finally:
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
