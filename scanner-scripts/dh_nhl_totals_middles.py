#!/usr/bin/env python3
"""DarkHorse NHL totals middles (PC port; added 2026-11-24).

Rules: Middles finder, saved preset "NHL Totals" (League: NHL only,
Market Type: Total only — verified live Nov 24: 161-166 rows, all
NHL totals). Every row is a 0.5-goal window (Over X / Under X+0.5
or Over X.5 / Under X+1) — a 1-number win+push at the whole number,
NOT the 2-number Over X / Under X+1 shape. The scan banks the
rows; the wide-build miner assembles 2-number shapes across rows
of the same game.
Qualification: BOTH legs -170-or-better (manual cap — the tool's
Hedge Bet Odds max is disabled under Any Book target); the row
identity must carry the NHL league token and a Total market
(in-code check, fail-closed — the preset is a view setting, not a
guarantee). No window minimum (0.5 goals is the whole point).
Lines are recovered from the identity's Total segments (the bet
cells sometimes carry no line, in which case the parsed leg line
silently equals the price — the Nov 14 SOG lesson).
State: data/nhl-totals-middles-state.json ({"entries", "meta"}).
Banked: data/nhl-totals-middles-<date>.md. Alert-only.

SESSION LOCKOUT (found 2026-11-24): DarkHorse allows ONE active
browser session per account. When the account is open in another
browser, /middles shows "Your account is in use in another
browser. / Use it here" and the finder never renders — the old
signed_in gate passes transiently on shell text, so this script
detects the interstitial explicitly and SKIPS cleanly (exit 0, no
state written) instead of failing loud with zero rows. It does NOT
click "Use it here": that would kick the other browser out, and
which browser owns the session is Broham's call.

RESTORE (shared /middles board): the preset / filter is app state
shared by every middles scan, so the run restores "DarkHorse
Default" (and clears any filter) in a finally block, even on
failure — a stale NHL-only board would starve the next
game/prop-middles run. Prints stay in the cp1252 set (no arrows).
"""
import re
import sys

from dh_common import (FINDER_URLS, open_session, signed_in,
                       stable_row_identity)
from dh_middles import collect_middles, price_in
from dh_sog_middles import (_close_filters_panel, _expand_tree_node,
                            _check_leaf, _click_visible_text,
                            _filters_button_state, _open_filters_panel,
                            _panel_clear)
from odds_common import bank, now_stamp, today_str
from state import load_state, save_state

SCAN = "nhl-totals-middles"
PRESET = "NHL Totals"
DEFAULT_PRESET = "DarkHorse Default"
CAP = -170
# The other mat-mdc-selects on the board — never the preset control.
NON_PRESET_LABELS = {"Any Book", "Full Bet Size Only"}
FILTER_FAIL = ""

_TOTAL_SEG = re.compile(r"total\s+(over|under)\s+([+-]?\d+(?:\.\d+)?)",
                        re.I)


def session_taken_over(page):
    """True when DarkHorse shows the 'account in use in another
    browser' interstitial instead of the finder."""
    try:
        text = page.locator("body").inner_text(timeout=10000)
    except Exception:
        return False
    return "in use in another browser" in text


def select_preset(page, name):
    """Click the preset mat-select, then the named mat-option.
    Returns True only when the control afterwards reads the
    requested preset. The preset control is the first VISIBLE
    .mat-mdc-select that is not Sportsbook Target or Liquidity
    Mode (the Nov 24 probe: preset is DOM-first, id mat-select-2;
    the option list renders in an overlay only while open)."""
    selects = page.locator(".mat-mdc-select")
    target = None
    for i in range(selects.count()):
        el = selects.nth(i)
        try:
            if not el.is_visible():
                continue
            txt = (el.inner_text() or "").strip()
        except Exception:
            continue
        if txt in NON_PRESET_LABELS:
            continue
        target = el
        break
    if target is None:
        return False
    try:
        target.click(timeout=5000)
    except Exception:
        return False
    page.wait_for_timeout(2000)
    opt = page.locator("mat-option", has_text=name)
    if opt.count() == 0:
        opt = page.get_by_role("option", name=name)
    if opt.count() == 0:
        page.keyboard.press("Escape")
        return False
    try:
        opt.first.click(timeout=5000)
    except Exception:
        return False
    page.wait_for_timeout(8000)
    try:
        return name in (target.inner_text() or "")
    except Exception:
        return True


def board_count(page):
    """Rows the board reports ('1 - 100 of N'), or None when the
    count text is not on the page. 10M+ default board reads ~10M;
    the NHL Totals preset reads ~150-200."""
    try:
        body = page.locator("body").inner_text(timeout=15000)
    except Exception:
        return None
    m = re.search(r"1\s*[–-]\s*\d+\s+of\s+([\d,KM\+]+)", body)
    if not m:
        return None
    raw = m.group(1).replace(",", "").replace("+", "")
    try:
        if raw.endswith("M"):
            return int(float(raw[:-1]) * 1_000_000)
        if raw.endswith("K"):
            return int(float(raw[:-1]) * 1000)
        return int(raw)
    except ValueError:
        return None


def _turn_on_section(page, section, verify_label):
    """Flip a Filters-panel section switch on; verify via the
    named checkbox becoming enabled. Layered attempts — the DOM
    shape is unprobed — mirroring dh_sog_middles' Market Type
    flip (a header-text click only expands the section, it does
    NOT flip the switch)."""
    def _enabled():
        try:
            lbl = page.get_by_label(verify_label, exact=True)
            if lbl.count():
                return bool(lbl.first.is_enabled())
        except Exception:
            pass
        return None

    def _try(fn):
        try:
            if fn():
                page.wait_for_timeout(1200)
                return _enabled()
        except Exception:
            pass
        return None

    def _by_switch_role():
        sw = page.get_by_role("switch", name=re.compile(section, re.I))
        if sw.count():
            sw.first.click(timeout=5000)
            return True
        return False

    def _by_header_parent():
        hdr = page.get_by_text(section, exact=True)
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

    attempts = []
    for name, fn in (("switch-role", _by_switch_role),
                     ("header-parent", _by_header_parent)):
        enabled = _try(fn)
        attempts.append(
            f"{name}={'on' if enabled else ('?' if enabled is None else 'off')}")
        if enabled is True:
            return True
    print(f"nhl-totals-middles: {section} switch did not flip "
          f"({', '.join(attempts)}).")
    return False


def _uncheck_others(page, keep_labels):
    """Uncheck every visible + enabled checked checkbox except the
    named leaves. The switch flip checks ALL boxes by default, so
    without this the board stays unfiltered. Returns how many were
    unchecked."""
    unchecked = 0
    try:
        boxes = page.locator("mat-checkbox input[type='checkbox']")
        if boxes.count() == 0:
            boxes = page.locator("input[type='checkbox']")
        for i in range(boxes.count()):
            el = boxes.nth(i)
            try:
                if not (el.is_visible() and el.is_enabled()):
                    continue
                if not el.is_checked():
                    continue
                try:
                    label = el.evaluate(
                        "e => { const l = e.closest('label'); "
                        "return l ? l.innerText.trim() : "
                        "(e.getAttribute('aria-label') || ''); }") or ""
                except Exception:
                    label = ""
                if label in keep_labels:
                    continue
                el.click(timeout=3000)
                unchecked += 1
                page.wait_for_timeout(300)
            except Exception:
                continue
    except Exception:
        pass
    return unchecked


def set_nhl_totals_filter(page):
    """FALLBACK (best-effort, unprobed live — the preset path is
    primary): Filters panel -> League switch ON -> Hockey expanded
    -> NHL only; Market Type switch ON -> Total only. Returns True
    only when the board afterwards reads 'Filters: On'. On any
    failure the panel is cleared so no partial filter is left."""
    global FILTER_FAIL
    if not _open_filters_panel(page):
        FILTER_FAIL = "panel did not open"
        return False
    try:
        if _filters_button_state(page) == "On":
            _panel_clear(page)
        if not _turn_on_section(page, "League", "Hockey"):
            FILTER_FAIL = "League switch did not flip"
            return False
        if not _expand_tree_node(page, "Hockey"):
            FILTER_FAIL = "Hockey node did not expand"
            return False
        page.wait_for_timeout(1200)
        if not _check_leaf(page, "NHL"):
            FILTER_FAIL = "NHL leaf not clickable"
            return False
        _uncheck_others(page, {"NHL"})
        if not _turn_on_section(page, "Market Type", "Total"):
            FILTER_FAIL = "Market Type switch did not flip"
            return False
        page.wait_for_timeout(1200)
        if not _check_leaf(page, "Total"):
            FILTER_FAIL = "Total leaf not clickable"
            return False
        _uncheck_others(page, {"NHL", "Total"})
        if not _close_filters_panel(page):
            FILTER_FAIL = "panel did not close"
            return False
        if _filters_button_state(page) != "On":
            FILTER_FAIL = "button does not read Filters: On"
            return False
        print("nhl-totals-middles: filter set via switches "
              "(League NHL only, Market Type Total only).")
        return True
    finally:
        # A failure above must not leave a half-set panel open.
        # (After a successful close the panel is gone and the
        # exact "Market Type" text with it, so this is a no-op
        # on the success path.)
        try:
            if page.get_by_text("Market Type", exact=True).count() > 0:
                _panel_clear(page)
                _close_filters_panel(page)
        except Exception:
            pass


def clear_nhl_totals_filter(page):
    """Best-effort restore for the fallback path: open the panel,
    top Clear, Back, verify 'Filters: Off'. Never raises."""
    try:
        if _filters_button_state(page) != "On":
            return True
        if not _open_filters_panel(page):
            return False
        _panel_clear(page)
        if not _close_filters_panel(page):
            return False
        return _filters_button_state(page) == "Off"
    except Exception as exc:  # noqa: BLE001 - cleanup must not raise
        print(f"nhl-totals-middles: filter clear FAILED ({exc}) — "
              f"the Middles board may still be filtered; the next "
              f"middles scan self-heals at its start.")
        return False


def restore_default(page):
    """Leave the shared /middles board as found: preset back to
    DarkHorse Default (and filters off). Best-effort; never
    raises."""
    try:
        if select_preset(page, DEFAULT_PRESET):
            return True
    except Exception:
        pass
    return clear_nhl_totals_filter(page)


def row_identity(mid):
    return stable_row_identity(mid.get("cells")) or ""


def identity_total_lines(cells):
    """(over_line, under_line) from the identity's Total segments,
    or (None, None). The bet cells sometimes carry no line, so the
    identity is the source of truth."""
    ident = stable_row_identity(cells) or ""
    over = under = None
    for seg in ident.split(" / "):
        m = _TOTAL_SEG.search(seg)
        if not m:
            continue
        if m.group(1).lower() == "over":
            over = float(m.group(2))
        else:
            under = float(m.group(2))
    return over, under


def is_nhl_total_row(mid):
    """In-code gate, fail-closed: the identity must carry the NHL
    league token and a Total market."""
    ident = row_identity(mid).lower()
    return "nhl" in ident and "total" in ident


def qualifies(mid):
    """(lo, hi) total lines when the row is a qualifying NHL total,
    else None. No window minimum — 0.5 goals is the point — but
    the recovered lines must be sane (distinct, within 2.0)."""
    if not is_nhl_total_row(mid):
        return None
    over, under = identity_total_lines(mid.get("cells"))
    if over is None or under is None:
        return None
    lo, hi = min(over, under), max(over, under)
    if hi == lo or hi - lo > 2.0:
        return None
    if not (price_in(mid["leg1"]["price"], CAP, 100000)
            and price_in(mid["leg2"]["price"], CAP, 100000)):
        return None
    return (lo, hi)


def push_numbers(lo, hi):
    """Whole-number lines in [lo, hi]: the total lands there and
    the whole-number leg pushes while the other wins."""
    return [int(n) for n in (lo, hi) if float(n).is_integer()]


def fix_leg_lines(mid, over, under):
    """Overwrite parsed leg lines with the recovered identity
    lines, matched by side text. A bet cell without a line parses
    its line as the price — never bank that."""
    for key in ("leg1", "leg2"):
        side = (mid[key].get("side") or "").lower()
        if "over" in side:
            mid[key]["line"] = over
        elif "under" in side:
            mid[key]["line"] = under


def middle_key(mid):
    """Stable state key: row identity + the two leg books (same
    shape as dh_game_middles — price wiggles must not re-key a
    known middle as NEW)."""
    l1, l2 = mid["leg1"], mid["leg2"]
    ident = row_identity(mid)
    if not ident:
        ident = (f"{mid.get('game', '')} | {mid.get('market', '')} | "
                 f"{l1['side']} | {l2['side']}")
    return f"{ident} | {l1['book']} | {l2['book']}"


def main():
    state = load_state(SCAN)
    pw, ctx = open_session()
    preset_applied = False
    filter_applied = False
    try:
        page = ctx.new_page()
        if not signed_in(page, FINDER_URLS["middles"]):
            print("DarkHorse check unavailable (signed out) — "
                  "nhl-totals-middles skipped, no prices invented.")
            return 0
        if session_taken_over(page):
            print("DarkHorse check unavailable (account in use in "
                  "another browser) — nhl-totals-middles skipped, "
                  "no prices invented.")
            return 0
        # Self-heal a stale filter left by a crashed run.
        try:
            if _filters_button_state(page) == "On":
                if _open_filters_panel(page):
                    _panel_clear(page)
                    _close_filters_panel(page)
        except Exception:
            pass
        # Primary path: the saved preset.
        preset_ok = select_preset(page, PRESET)
        if preset_ok:
            count = board_count(page)
            if count is not None and count > 1000:
                print(f"nhl-totals-middles: preset selected but "
                      f"board shows {count} rows — preset did not "
                      f"apply; trying filter switches.")
                preset_ok = False
            else:
                preset_applied = True
        if not preset_ok:
            if session_taken_over(page):
                print("DarkHorse check unavailable (account in use "
                      "in another browser) — nhl-totals-middles "
                      "skipped, no prices invented.")
                return 0
            filter_applied = set_nhl_totals_filter(page)
            if not filter_applied:
                if session_taken_over(page):
                    print("DarkHorse check unavailable (account in "
                          "use in another browser) — "
                          "nhl-totals-middles skipped, no prices "
                          "invented.")
                    return 0
                print(f"nhl-totals-middles: preset AND filter "
                      f"switches failed ({FILTER_FAIL or 'no '
                      f'detail'}) — no rows read. No state written.",
                      file=sys.stderr)
                return 2
        try:
            full, _ = collect_middles(page)
        finally:
            # Restore the shared board before any banking.
            if preset_applied:
                restore_default(page)
            elif filter_applied:
                clear_nhl_totals_filter(page)
        if not full:
            if session_taken_over(page):
                print("DarkHorse check unavailable (account in use "
                      "in another browser) — nhl-totals-middles "
                      "skipped, no prices invented.")
                return 0
            print("nhl-totals-middles: zero middles parsed — send "
                  "a dump back for a parser patch. No state "
                  "written.", file=sys.stderr)
            return 2
        # The preset / filter is a view setting, not a guarantee:
        # verify at least one parsed row is really an NHL total.
        if not any(is_nhl_total_row(m) for m in full):
            print("nhl-totals-middles: rows parsed but none is an "
                  "NHL total — preset/filter did not apply. No "
                  "state written.", file=sys.stderr)
            return 2
        via = ("preset" if preset_applied else "filter-switches")
        deliverables = []
        lines = [f"## {now_stamp()} — nhl-totals-middles: "
                 f"{len(full)} rows parsed (qualifying only)"]
        for mid in full:
            rec = qualifies(mid)
            if rec is None:
                continue
            lo, hi = rec
            over, under = identity_total_lines(mid.get("cells"))
            fix_leg_lines(mid, over, under)
            push = push_numbers(lo, hi)
            key = middle_key(mid)
            entry = {"game": row_identity(mid).split(" / ")[0],
                     "market": row_identity(mid),
                     "window": hi - lo,
                     "lines": [lo, hi],
                     "push": push,
                     "conversion_pct": mid["conversion_pct"],
                     "leg1": mid["leg1"], "leg2": mid["leg2"],
                     "smaller_bets": False,
                     "first_reported": now_stamp(),
                     "last_seen": today_str()}
            if key not in state["entries"]:
                state["entries"][key] = entry
                deliverables.append((key, entry))
            else:
                state["entries"][key]["last_seen"] = today_str()
            push_txt = f" push {push}" if push else ""
            lines.append(f"{key} w{entry['window']:g}{push_txt} :: "
                         f"{mid['leg1']} / {mid['leg2']}")
        # Chat-delivery wiring (same pattern as dh_game_middles):
        # the header carries the NEW count and each deliverable
        # also banks as a NEW MIDDLE line directly under it — the
        # pattern the pc-data delivery watcher screens for.
        new_lines = []
        for key, e in deliverables:
            push_txt = f" push {e['push']}" if e["push"] else ""
            new_lines.append(
                f"NEW MIDDLE: {key} w{e['window']:g}{push_txt} :: "
                f"{e['leg1']} / {e['leg2']}")
        lines[0] += (f" — {len(deliverables)} NEW middles "
                     f"(via {via})")
        if new_lines:
            lines = (lines[:1] + new_lines
                     + ["(end of new middles — full qualifying list "
                         "below)"] + lines[1:])
        save_state(SCAN, state)
        bank(SCAN, lines)
        for key, e in deliverables[:10]:
            print(f"NHL TOTAL MIDDLE: {key} — window "
                  f"{e['window']:g} push {e['push']} :: "
                  f"{e['leg1']['side']} {e['leg1']['price']} "
                  f"{e['leg1']['book']} / {e['leg2']['side']} "
                  f"{e['leg2']['price']} {e['leg2']['book']}")
        if not deliverables:
            print("nhl-totals-middles: no new qualifying middles.")
        return 0
    finally:
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
