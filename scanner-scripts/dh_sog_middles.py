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

FIX 2026-11-14 (Michael's tap — UI probe browser-task:771724dc):
the filter EXISTS and WORKS, but it lives behind a "Filters: Off"
button opening a full-page Filters panel — there is no "Hockey
Props" text on the board page itself, which is why the old
label-click never landed. Path: Filters panel → Market Type switch
ON → Hockey Props → Player Props → Shots (SOG is labeled just
"Shots"; no "Shots on Goal" label exists). Filtered board reads
"1–100 of 14k+", all NHL Shots rows. The URL never changes —
the filter is app/preset state, NOT a URL param. CRITICAL: the
filter persists, and game/prop middles share /middles — this
script MUST Clear the filter (panel top "Clear", verify the button
returns to "Filters: Off") after its read, even on failure
(try/finally). A stale "Filters: On" left by a crashed run is
cleared at the START of the next run. No named preset is saved
(the "Middles Preset" dropdown is untested).
FIX v2 2026-11-14 (same day, after manual test #2 at 14:40):
the filter still did not set — 'Hockey Props' was not clickable.
Two causes: (1) the header-text click only expands the Market
Type section, it does NOT flip the switch, so the checkboxes
stayed disabled — the flip is now VERIFIED against the Hockey
Props checkbox before proceeding; (2) the mid-sequence Clear
resets the switch itself — Clear is now used ONLY for the stale
filter at run start and the restore in clear_shots_filter.
Failures are recorded in the banked header (FILTER_FAIL) so the
scheduler's own hourly runs self-diagnose.
FIX v3 2026-11-14 (same day, after manual test #3 at 14:47):
the switch now flips and verifies (via header-parent), and
Hockey Props checks — but 'Player Props' never appeared: the
label click checks the box without expanding the branch. The
drill-down now polls for the child node and, if it is not
visible, clicks the row text once (the probe's click, which
expands the branch). Also fixed: FILTER_FAIL assignments in
set_shots_filter were function-locals (missing `global`), so
the banked header never carried the failure reason. A final
checked-state verification guards against the expansion click
toggling Hockey Props off.

FIX 2026-11-14 (same tap): real-line recovery, the Nov 10
key-scan pattern — leg["line"] silently EQUALS the leg price when
a BET cell carries no line, so mid["window"] is price-derived
garbage and must never gate qualification. Lines are recovered
from the identity's Shots segments (last number per segment,
optional "+" threshold suffix — "3+" wins at exactly 3, per the
Nov 13 player-prop rule); leg lines are only a fallback when
they cannot be a price. The both-win numbers are the integers
n with ceil(lo) <= n <= floor(hi) of the recovered pair.

FIX 2026-11-10 (Michael's tap): the in-code check was dead —
dh_middles.parse_middle_row never populates a "market" field, so
`mid.get("market")` was always "" and the guard never fired; with
the best-effort Hockey Props click also not landing, the scan
banked the WHOLE Middles board (13,150 state entries, all game
spreads/totals/moneylines with empty player/game/market). The
filter is now authoritative in code: a row qualifies only when
its own row identity (the non-bet cells: event + market text)
mentions shots ("shot" / "sog"). The polluted state was backed up
and reset the same day.
"""
import argparse
import math
import re
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


def _click_visible_text(page, text, timeout=5000):
    """Click the first VISIBLE element with exactly this text.
    The Filters panel has nested trees (e.g. "Shots" under both
    Hockey Props and Soccer) — the collapsed branches are hidden,
    so :visible keeps the click on the expanded hockey node."""
    try:
        loc = page.locator(f"text={text} >> visible=true")
        if loc.count():
            loc.first.click(timeout=timeout)
            return True
    except Exception:
        pass
    try:
        loc = page.get_by_text(text, exact=True)
        if loc.count():
            loc.first.click(timeout=timeout)
            return True
    except Exception:
        pass
    return False


def _filters_button_state(page):
    """'Off' / 'On' / None — read off the toolbar button label."""
    try:
        text = page.locator("body").inner_text(timeout=10000)
    except Exception:
        return None
    m = re.search(r"Filters:\s*(Off|On)", text)
    return m.group(1) if m else None


def _open_filters_panel(page):
    """Click the toolbar Filters button; True when the panel (with
    its Market Type section) is showing."""
    try:
        btn = page.locator("text=/Filters:\\s*(Off|On)/ >> visible=true")
        if btn.count():
            btn.first.click(timeout=5000)
        else:
            page.get_by_text(re.compile(r"Filters:\s*(Off|On)"),
                             exact=False).first.click(timeout=5000)
    except Exception:
        return False
    page.wait_for_timeout(1500)
    try:
        return page.get_by_text("Market Type", exact=True).count() > 0
    except Exception:
        return False


def _close_filters_panel(page):
    """Back to the board; True when the panel is gone."""
    try:
        back = page.get_by_role("button", name="Back")
        if back.count():
            back.first.click(timeout=5000)
        else:
            _click_visible_text(page, "Back", timeout=5000)
    except Exception:
        return False
    page.wait_for_timeout(2500)
    try:
        return page.get_by_text("Market Type", exact=True).count() == 0
    except Exception:
        return False


def _panel_clear(page):
    """Top 'Clear' in the Filters panel header (first Clear button
    in the panel). Returns True when clicked."""
    try:
        btns = page.get_by_role("button", name="Clear")
        if btns.count():
            btns.first.click(timeout=5000)
            page.wait_for_timeout(1200)
            return True
    except Exception:
        pass
    return _click_visible_text(page, "Clear", timeout=5000)


# Last filter failure reason, for the banked header — the
# scheduler's own hourly runs then self-diagnose without a
# manual test (fix 2026-11-14 v2).
FILTER_FAIL = ""


def _hockey_props_enabled(page):
    """True when the Hockey Props checkbox exists and is enabled
    (i.e. the Market Type switch is really on); False when it
    exists but is disabled; None when no label-associated control
    is found (DOM shape unprobed — caller must not treat None as
    proof either way)."""
    try:
        lbl = page.get_by_label("Hockey Props", exact=True)
        if lbl.count():
            return bool(lbl.first.is_enabled())
    except Exception:
        pass
    return None


def _turn_on_market_type(page):
    """Flip the Market Type section switch on. The switch sits
    beside the section label; its checkboxes stay disabled until
    it is on. Layered attempts — the DOM shape is unprobed — and
    each attempt is VERIFIED against the Hockey Props checkbox:
    the old version returned True after merely clicking the
    section header text, which expands/collapses the section
    WITHOUT flipping the switch (manual test 2026-11-14 14:40:
    'Hockey Props' stayed disabled and the filter never set)."""
    attempts = []

    def _try(fn):
        try:
            if fn():
                page.wait_for_timeout(1200)
                return _hockey_props_enabled(page)
        except Exception:
            pass
        return None

    def _by_switch_role():
        sw = page.get_by_role("switch", name=re.compile(
            "Market Type", re.I))
        if sw.count():
            sw.first.click(timeout=5000)
            return True
        return False

    def _by_label():
        lbl = page.locator("label", has_text="Market Type")
        if lbl.count():
            lbl.first.click(timeout=5000)
            return True
        return False

    def _by_header_parent():
        # The switch is a toggle control inside the section
        # header row: find the header text, walk to its parent,
        # click the first switch/checkbox/button inside it.
        hdr = page.get_by_text("Market Type", exact=True)
        if not hdr.count():
            return False
        try:
            parent = hdr.first.locator("xpath=..")
            for sel in ("[role='switch']", "input[type='checkbox']",
                        "button"):
                cand = parent.locator(sel)
                if cand.count():
                    cand.first.click(timeout=5000)
                    return True
        except Exception:
            pass
        return False

    def _by_header_text():
        return _click_visible_text(page, "Market Type", timeout=5000)

    for name, fn in (("switch-role", _by_switch_role),
                     ("label", _by_label),
                     ("header-parent", _by_header_parent),
                     ("header-text", _by_header_text)):
        enabled = _try(fn)
        attempts.append(f"{name}={'on' if enabled else ('?' if enabled is None else 'off')}")
        if enabled is True:
            print("sog-middles: Market Type switch on "
                  f"(via {name}).")
            return True
    print("sog-middles: Market Type switch did not flip "
          f"({', '.join(attempts)}) — relying on the in-code "
          "market check.")
    return False


def _click_checkbox(page, label):
    """Click a filter-tree checkbox by its label (top-level labels
    are unique); fall back to the visible-text click."""
    try:
        loc = page.get_by_label(label, exact=True)
        if loc.count():
            loc.first.click(timeout=5000)
            return True
    except Exception:
        pass
    return _click_visible_text(page, label)


def _wait_visible(page, text, timeout_ms=8000):
    """Poll until an element with exactly this text is visible."""
    try:
        page.locator(f"text={text} >> visible=true").first.wait_for(
            state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


def _expand_and_click(page, label, child_text=None):
    """Click a filter-tree checkbox by its label; when a child
    node is expected, make sure the subtree expands so the child
    becomes visible. The label click checks the box but does not
    always expand the branch (manual test 2026-11-14 14:47:
    'Player Props' never appeared after checking Hockey Props),
    so if the child is not visible after a poll, click the row
    text once — the probe's click, which expands the branch —
    and poll again. Returns True when the checkbox got clicked
    AND (no child expected OR the child became visible)."""
    if not _click_checkbox(page, label):
        return False
    if child_text is None:
        return True
    if _wait_visible(page, child_text, 8000):
        return True
    _click_visible_text(page, label)
    return _wait_visible(page, child_text, 8000)


def set_shots_filter(page):
    """Drive the Filters panel to Market Type = Hockey Props →
    Player Props → Shots only. Returns True only when the board
    afterwards reads 'Filters: On'. On any failure the panel is
    cleared and closed so no partial filter is left behind; the
    caller then falls back to the in-code market check."""
    global FILTER_FAIL
    if not _open_filters_panel(page):
        FILTER_FAIL = "panel did not open"
        print("sog-middles: Filters panel did not open — relying "
              "on the in-code market check.")
        return False
    try:
        # Self-heal a stale filter left by a crashed run.
        if _filters_button_state(page) == "On":
            _panel_clear(page)
        if not _turn_on_market_type(page):
            FILTER_FAIL = "Market Type switch did not flip"
            print("sog-middles: Market Type switch did not flip — "
                  "relying on the in-code market check.")
            return False
        # NOTE: do NOT press Clear here. The section Clear (or the
        # panel's top Clear) resets the Market Type switch itself
        # — the 2026-11-14 14:40 manual test flipped the switch,
        # then cleared, and 'Hockey Props' was disabled afterwards.
        # A stale filter is already cleared at run start (above);
        # the checkboxes of a freshly flipped switch start
        # unchecked.
        if not _expand_and_click(page, "Hockey Props",
                                  "Player Props"):
            FILTER_FAIL = "'Hockey Props' not clickable / did not expand"
            print("sog-middles: 'Hockey Props' not clickable or "
                  "its subtree did not expand — relying on the "
                  "in-code market check.")
            return False
        page.wait_for_timeout(1200)
        if not _expand_and_click(page, "Player Props", "Shots"):
            FILTER_FAIL = "'Player Props' not clickable / did not expand"
            print("sog-middles: 'Player Props' not clickable or "
                  "its subtree did not expand — relying on the "
                  "in-code market check.")
            return False
        page.wait_for_timeout(1200)
        if not _click_visible_text(page, "Shots"):
            FILTER_FAIL = "'Shots' not clickable"
            print("sog-middles: 'Shots' not clickable — relying "
                  "on the in-code market check.")
            return False
        page.wait_for_timeout(1200)
        # The row-text expansion click can toggle a checkbox off
        # in some trees — verify Hockey Props is still checked
        # before trusting the filter.
        try:
            hp = page.get_by_label("Hockey Props", exact=True)
            if hp.count() and not hp.first.is_checked():
                FILTER_FAIL = "Hockey Props unchecked after drill-down"
                print("sog-middles: Hockey Props unchecked itself "
                      "during drill-down — relying on the in-code "
                      "market check.")
                return False
        except Exception:
            pass
        if not _close_filters_panel(page):
            FILTER_FAIL = "panel did not close"
            print("sog-middles: Filters panel did not close — "
                  "filter state uncertain; relying on the in-code "
                  "market check.")
            return False
        state = _filters_button_state(page)
        if state != "On":
            FILTER_FAIL = f"button reads 'Filters: {state}'"
            print(f"sog-middles: filter set but button reads "
                  f"'Filters: {state}' — treating as unset.")
            return False
        print("sog-middles: Market Type filter set to Hockey Props "
              "→ Player Props → Shots (button: Filters: On).")
        return True
    finally:
        # A failure above must not leave a half-set panel open.
        try:
            if page.get_by_text("Market Type",
                                exact=True).count() > 0:
                _panel_clear(page)
                _close_filters_panel(page)
        except Exception:
            pass


def clear_shots_filter(page):
    """Best-effort restore: open the panel, top Clear, Back, verify
    the button reads 'Filters: Off'. Never raises — the caller logs
    the outcome. MUST run after every read (try/finally): the
    filter is app state shared by every middles scan on /middles."""
    try:
        if _filters_button_state(page) != "On":
            return True
        if not _open_filters_panel(page):
            return False
        _panel_clear(page)
        if not _close_filters_panel(page):
            return False
        ok = _filters_button_state(page) == "Off"
        print("sog-middles: filter cleared — button reads "
              f"'Filters: {_filters_button_state(page)}'.")
        return ok
    except Exception as exc:  # noqa: BLE001 - cleanup must not raise
        print(f"sog-middles: filter clear FAILED ({exc}) — the "
              f"Middles board may still be filtered; the next "
              f"middles scan will self-heal at its start.")
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


def row_identity(mid):
    """The row's own event/market text (non-bet cells). This is the
    only trustworthy market label: parse_middle_row populates no
    player/market/game fields."""
    return stable_row_identity(mid.get("cells")) or ""


# --- Line recovery (fix 2026-11-14, same Nov 10 key-scan pattern)
# parse_middle_row takes the legs from the BET cells, and for many
# books those cells carry only price / stake / book — the line lives
# in the row's MARKET columns ("Shots M. Barzal Under 2.5"). For
# those rows leg["line"] silently equals the leg PRICE, so the old
# code computed windows off prices and mid["window"] was garbage.
# Lines are recovered from the identity's Shots segments first; leg
# lines are only a fallback when they cannot be a price.
_SEG_NUM = re.compile(r"([+-]?\d+(?:\.\d+)?)\+?\s*(?:YES|NO)?\s*$",
                      re.I)


def identity_shots_lines(cells):
    """The two lines named in the identity's Shots segments."""
    ident = stable_row_identity(cells) or ""
    out = []
    for seg in ident.split(" / "):
        if "shot" not in seg.lower():
            continue
        m = _SEG_NUM.search(seg.strip())
        if m:
            out.append(float(m.group(1)))
    return out


def leg_line_trustworthy(mid, leg):
    """A parsed leg line that cannot be the leg's price."""
    try:
        line = float(mid[leg]["line"])
        price = int(mid[leg]["price"])
    except (TypeError, ValueError, KeyError):
        return None
    if line == price:
        return None            # bet cell carried no line: line IS price
    return line


def recovered_lines(mid):
    """(lo, hi) SOG threshold lines for the middle, or None."""
    lines = identity_shots_lines(mid.get("cells"))
    if len(lines) >= 2:
        return min(lines[0], lines[1]), max(lines[0], lines[1])
    l1 = leg_line_trustworthy(mid, "leg1")
    l2 = leg_line_trustworthy(mid, "leg2")
    if l1 is None or l2 is None:
        return None
    return min(l1, l2), max(l1, l2)


def both_win_numbers(lo, hi):
    """Integers n with ceil(lo) <= n <= floor(hi): the actual shot
    totals where BOTH legs win. Threshold props: the Over leg wins
    at exactly its (whole-number) line — a "+" leg is a win at the
    number, never a push (Nov 13 player-prop rule)."""
    return list(range(math.ceil(lo), math.floor(hi) + 1))


def is_sog_row(mid):
    ident = row_identity(mid).lower()
    # "Blocked Shots" is a different market — it also contains
    # "shot", so exclude it explicitly.
    return ("shot" in ident or "sog" in ident) and \
        "blocked shot" not in ident


def qualifies(mid):
    # NOTE: mid["window"] from the parser is computed off the raw
    # leg lines, which for price-only bet cells are the PRICES —
    # never trust it. Width and both-win numbers come from the
    # recovered lines.
    if not is_sog_row(mid):
        return False, None
    lines = recovered_lines(mid)
    if lines is None:
        return False, None
    lo, hi = lines
    if hi - lo < MIN_WINDOW:
        return False, None
    if not (price_in(mid["leg1"]["price"], PRICE_LO, PRICE_HI)
            and price_in(mid["leg2"]["price"], PRICE_LO, PRICE_HI)):
        return False, None
    return True, (lo, hi, both_win_numbers(lo, hi))


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
        filter_set = set_shots_filter(page)
        try:
            if args.no_smaller:
                from dh_middles import collect_middles
                full, _ = collect_middles(page)
                smaller = []
            else:
                full, smaller = collect_with_smaller(page)
        finally:
            # The filter is shared app state — restore the board
            # even when the read failed midway.
            if filter_set:
                clear_shots_filter(page)
        if not full and not smaller:
            print("sog-middles: zero middles parsed — run with a "
                  "dump and send it back for a parser patch. No "
                  "state written.", file=sys.stderr)
            return 2
        deliverables = []
        lines = [f"## {now_stamp()} — sog-middles: {len(full)} "
                 f"full-size + {len(smaller)} smaller-bets rows "
                 f"parsed (filter={'on' if filter_set else 'off — '
                 f'{FILTER_FAIL or 'in-code check only'}'}) "
                 f"(compact: qualifying only below)"]
        for mid in full + smaller:
            ok, recovered = qualifies(mid)
            if not ok:
                continue
            lo, hi, both_win = recovered
            key = middle_key(mid)
            entry = {"player": mid.get("player", ""),
                     "market": row_identity(mid),
                     "game": mid.get("game", ""),
                     "window": hi - lo,
                     "lines": [lo, hi],
                     "both_win": both_win,
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
            bw = e.get("both_win")
            bw_txt = f" both-win {bw}" if bw is not None else ""
            lines.append(
                f"{kind} MIDDLE: {'CLEAN ' if e['clean_tier'] else ''}"
                f"{key} w{e['window']:g}{bw_txt} :: "
                f"{leg_line(h['leg1'])} / {leg_line(h['leg2'])}"
                f"{' [SMALLER BETS]' if e['smaller_bets'] else ''}")
            lines.append(
                f"- {tag} {'CLEAN ' if e['clean_tier'] else ''}"
                f"W{e['window']:g}{bw_txt} {e['player']} "
                f"({e['game']}): "
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
