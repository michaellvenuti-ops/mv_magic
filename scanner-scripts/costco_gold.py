"""Costco gold-bar check (PC port, added 2026-11-05 at Michael's tap).

Runs on the scan PC under scheduler.py in Michael's interactive session,
using a DEDICATED Playwright persistent profile for Costco
(%LOCALAPPDATA%\\pc_scans\\costco_profile) — separate from the DarkHorse
profile and from his regular Chrome. He signs in ONCE himself:

    python costco_gold.py --signin    # headed: sign in yourself, window polls
    python costco_gold.py --check     # report whether the profile is signed in
    python costco_gold.py             # the daily check (scheduler entry)

Daily check: opens each tracked gold-bar product page, captures the price
as shown (or "Sign In for Price" if the session lapsed), the delivery
availability text for ZIP 08002, and — for item 1943308 only, the one with
a warehouse option — the Cherry Hill warehouse panel text. Writes:
    data/costco-gold/costco-gold-YYYY-MM-DD.json   (schema per spec)
    data/costco-gold/costco-gold-YYYY-MM-DD.md     (one-screen summary)
    data/costco-gold/dump-YYYY-MM-DD.txt           (raw page excerpts,
                                                    calibration surface)
which sync_to_github.py pushes to pc-data/costco-gold/.

READ-ONLY: never buys, never changes account settings, no password is
stored in this script — the session lives in the local profile only.
Prices are copied from the page, never invented; if extraction is
ambiguous the raw snippet is banked and price stays null.

Gift cards (added 2026-11-07 at Michael's tap): after the gold items,
the same run also reads Costco's gift-card category pages and reports
every gift card whose shown price is BELOW its face value (brand, face
value, price, discount %), in a "gift_cards" section of the same JSON
plus a section of the same .md summary. Face value is parsed from the
product title only; a card whose face value cannot be parsed is listed
with discount null rather than guessed.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from config import DATA_DIR, PAGE_TIMEOUT_MS

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")

_local_appdata = Path.home()  # fallback, replaced below when env present
import os  # noqa: E402

_lad = os.environ.get("LOCALAPPDATA")
if _lad:
    COSTCO_PROFILE_DIR = Path(_lad) / "pc_scans" / "costco_profile"
else:
    COSTCO_PROFILE_DIR = Path.home() / ".pc_scans" / "costco_profile"

ANCHOR_ZIP = "08002"
WAREHOUSE = "Cherry Hill"

ITEMS = [
    ("1943308", "1 oz Gold Bar PAMP Suisse Lady Fortuna Veriscan"),
    ("2095085", "1 oz Sunshine Minting Breakaway Gold Bar"),
    ("1801206", "100 Gram Gold Bar PAMP Suisse Lady Fortuna Veriscan"),
    ("2053301", "25 Gram PAMP Diwali Lakshmi and Sacred Cow Multigram Gold Bar"),
    ("1768080", "1 oz Gold Bar Rand Refinery"),
    ("2053310", "1/2 oz Gold Bar PAMP Good Luck Maneki Neko"),
    ("1811661", "25 Gram PAMP Suisse Lady Fortuna Multigram Gold Bar Veriscan"),
    ("2053312", "1 oz Gold Bar PAMP American Icons Buffalo"),
]
WAREHOUSE_ITEM = "1943308"  # only item with a warehouse option

PRICE_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*\.\d{2})")
OUT_DIR = DATA_DIR / "costco-gold"

PRICE_SELECTORS = [
    "[automation-id='productPriceOutput']",
    ".final-price",
    "#final_price",
    "[class*='final-price']",
    ".price",
]


def product_url(item: str) -> str:
    return f"https://www.costco.com/.product.{item}.html"


def snippet(text: str, needle: str, width: int = 220) -> str:
    """A short excerpt of `text` around the first occurrence of `needle`."""
    i = text.lower().find(needle.lower())
    if i < 0:
        return ""
    start = max(0, i - 40)
    return " ".join(text[start:i + width].split())


def extract_item(page, item: str) -> dict:
    """Read one product page; return the per-item record."""
    rec = {"item": item, "price": None, "price_note": "", "online": "",
           "warehouse": None, "error": ""}
    resp = page.goto(product_url(item), timeout=PAGE_TIMEOUT_MS,
                     wait_until="domcontentloaded")
    status = resp.status if resp else 0
    page.wait_for_timeout(6000)
    if status == 429:
        rec["error"] = "HTTP 429 rate-limited"
        return rec
    data = page.evaluate("""() => {
        const pick = (sel) => {
            const el = document.querySelector(sel);
            return el ? el.innerText.trim() : null;
        };
        const sels = %s;
        let price = null;
        for (const s of sels) { price = pick(s); if (price) break; }
        return { title: document.title, price_el: price,
                 body: document.body ? document.body.innerText : '' };
    }""" % json.dumps(PRICE_SELECTORS))
    body = data.get("body") or ""
    rec["_body"] = body
    if "sign in for price" in body.lower():
        rec["price_note"] = "Sign In for Price"
        return rec
    # Price: prefer the dedicated price element; else the first $-amount
    # in the body (banked with its snippet either way for calibration).
    price_text = data.get("price_el") or ""
    m = PRICE_RE.search(price_text) or PRICE_RE.search(body)
    if m:
        rec["price"] = m.group(0).replace(" ", "")
        rec["price_snippet"] = snippet(body, m.group(0))
    else:
        rec["price_note"] = "no price found on page"
    # Delivery availability (ZIP context is whatever the profile holds).
    for needle in ("unable to determine shipping availability",
                   "Delivery Unavailable", "Get it by", "Delivery"):
        s = snippet(body, needle)
        if s:
            rec["online"] = s
            if needle != "Delivery":
                break
    return rec


def extract_warehouse(page) -> str:
    """For 1943308: the Cherry Hill 'How To Get It' panel text, as shown."""
    body = page.evaluate("() => document.body ? document.body.innerText : ''")
    for needle in ("Not Sold In This Warehouse", "Out of Stock",
                   "In Stock", "In-Warehouse"):
        s = snippet(body, needle, 260)
        if s:
            return s
    s = snippet(body, WAREHOUSE, 260)
    return s or "warehouse panel not found on page"


GIFT_CARD_URLS = [
    "https://www.costco.com/gift-cards.html",
    "https://www.costco.com/CatalogSearch?keyword=egift+card&dept=All",
]

FACE_MULTI_RE = re.compile(
    r"(\d+)\s*(?:x|pack of|count|ct\.?)\s*\$?\s?(\d+(?:\.\d{2})?)",
    re.IGNORECASE)
FACE_SINGLE_RE = re.compile(r"\$\s?(\d+(?:\.\d{2})?)")


def parse_face_value(title: str):
    """Face value from a gift-card title, or None if not determinable.

    Handles "$100 Uber eGift Card" and multipacks like
    "2 x $50 ... Gift Cards" / "Four $25 ..." is NOT handled (returns
    None) — never guess a face value.
    """
    m = FACE_MULTI_RE.search(title)
    if m:
        try:
            return int(m.group(1)) * float(m.group(2))
        except ValueError:
            return None
    if re.search(r"\b(two|three|four|five|six)\s+\$", title, re.IGNORECASE):
        return None  # word-number multipack — total face not determinable
    m = FACE_SINGLE_RE.search(title)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def extract_gift_cards(page) -> list:
    """Read Costco's gift-card pages; return all cards found, flagged.

    A card counts as "on sale" when its shown price is below the face
    value parsed from its title. Cards with an unparseable face value
    are returned with face/discount null so nothing is hidden, but the
    summary only headlines ones with a computed discount.
    """
    seen = {}
    for url in GIFT_CARD_URLS:
        try:
            resp = page.goto(url, timeout=PAGE_TIMEOUT_MS,
                             wait_until="domcontentloaded")
            if resp and resp.status == 429:
                time.sleep(60)
                page.goto(url, timeout=PAGE_TIMEOUT_MS,
                          wait_until="domcontentloaded")
            page.wait_for_timeout(7000)
        except Exception:  # noqa: BLE001 - one bad page must not kill run
            continue
        tiles = page.evaluate("""() => {
            const out = [];
            const anchors = document.querySelectorAll(
                "a[href*='.product.']");
            const seenHrefs = new Set();
            for (const a of anchors) {
                const href = a.href || '';
                if (seenHrefs.has(href)) continue;
                seenHrefs.add(href);
                let tile = a;
                for (let i = 0; i < 6 && tile.parentElement; i++) {
                    tile = tile.parentElement;
                    if ((tile.innerText || '').includes('$')) break;
                }
                const priceEl = tile.querySelector(
                    "[automation-id='productPriceOutput'], .final-price,"
                    + " #final_price, [class*='final-price'], .price");
                out.push({ href: href,
                           text: (tile.innerText || '').trim(),
                           price_el: priceEl ? priceEl.innerText.trim()
                                             : null });
            }
            return out;
        }""")
        for t in tiles or []:
            href = t.get("href") or ""
            m = re.search(r"\.product\.(\d+)\.html", href)
            item = m.group(1) if m else href
            if item in seen:
                continue
            text = " ".join((t.get("text") or "").split())
            if "gift card" not in text.lower():
                continue
            title = text[:180]
            price = None
            pm = PRICE_RE.search(t.get("price_el") or "") \
                or PRICE_RE.search(text)
            if pm:
                price = float(pm.group(1).replace(",", ""))
            face = parse_face_value(title)
            discount = None
            if price is not None and face:
                discount = round((1 - price / face) * 100, 1)
            seen[item] = {
                "item": item, "title": title, "price": price,
                "face_value": face, "discount_pct": discount,
                "on_sale": bool(discount and discount > 0),
                "snippet": text[:300],
            }
        time.sleep(8)  # same gentle pacing as the gold items
    cards = list(seen.values())
    # On-sale first, biggest discount first; unpriced/unparsed last.
    cards.sort(key=lambda c: (-(c["discount_pct"] or -1),
                              c["title"]))
    return cards


def signed_in_probe(page) -> bool:
    """Signed-in signal: a product page shows a price, not the sign-in wall."""
    page.goto(product_url(WAREHOUSE_ITEM), timeout=PAGE_TIMEOUT_MS,
              wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    body = page.locator("body").inner_text(timeout=15000)
    return "sign in for price" not in body.lower() and bool(
        PRICE_RE.search(body))


def run_daily() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(ET).strftime("%Y-%m-%d")
    now_pt = datetime.now(PT).strftime("%Y-%m-%d %H:%M PT")
    result = {"date": today, "run_time_pt": now_pt, "anchor_zip": ANCHOR_ZIP,
              "warehouse": WAREHOUSE, "signed_in": None, "items": [],
              "gift_cards": [], "gift_cards_error": "",
              "rate_limited": [], "errors": []}
    dump_lines = [f"costco gold dump {today} run {now_pt}"]
    COSTCO_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    loaded = 0
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(COSTCO_PROFILE_DIR), headless=False,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.new_page()
        for idx, (item, name) in enumerate(ITEMS):
            if idx:
                time.sleep(8)  # gentle pacing; Costco 429s when rushed
            try:
                rec = extract_item(page, item)
                if rec.get("error") == "HTTP 429 rate-limited":
                    result["rate_limited"].append(item)
                    time.sleep(60)
                    rec = extract_item(page, item)  # one retry
                    if rec.get("error"):
                        result["errors"].append(f"{item}: {rec['error']}")
                if item == WAREHOUSE_ITEM and not rec.get("error"):
                    try:
                        rec["warehouse"] = extract_warehouse(page)
                    except Exception as e:  # noqa: BLE001
                        rec["warehouse"] = f"panel read failed: {e}"
                body = rec.pop("_body", "")
                if body:
                    loaded += 1
                    dump_lines.append(f"\n===== {item} {name} =====\n"
                                      + body[:3000])
                rec["name"] = name
                result["items"].append(rec)
                print(f"{item}: price={rec['price']} note={rec['price_note']}"
                      f" err={rec['error']}", flush=True)
            except Exception as e:  # noqa: BLE001 - isolate per-item failures
                result["errors"].append(f"{item}: {e}")
                result["items"].append(
                    {"item": item, "name": name, "price": None,
                     "price_note": "", "online": "", "warehouse": None,
                     "error": str(e)})
        # Gift cards on sale (added 2026-11-07 at Michael's tap) — same
        # session, after the gold items; a failure here never fails the
        # gold run.
        try:
            time.sleep(8)
            cards = extract_gift_cards(page)
            result["gift_cards"] = cards
            on_sale = [c for c in cards if c["on_sale"]]
            dump_lines.append(
                f"\n===== GIFT CARDS: {len(cards)} listed, "
                f"{len(on_sale)} on sale =====")
            for c in cards:
                dump_lines.append(
                    f"{c['item']} | {c['title']} | price {c['price']} | "
                    f"face {c['face_value']} | discount "
                    f"{c['discount_pct']} | {c['snippet']}")
            print(f"gift cards: {len(cards)} listed, "
                  f"{len(on_sale)} on sale", flush=True)
        except Exception as e:  # noqa: BLE001 - isolate gift-card failures
            result["gift_cards_error"] = str(e)
            print(f"gift cards: FAILED {e}", flush=True)
        ctx.close()
    if loaded == 0:
        print("SUMMARY: FAILED - no product pages loaded", flush=True)
        return 2
    signed = any(r["price"] for r in result["items"])
    result["signed_in"] = bool(signed)
    in_stock = [r["item"] for r in result["items"]
                if r.get("warehouse") and "in stock" in r["warehouse"].lower()
                and "out of stock" not in r["warehouse"].lower()
                and "not sold" not in r["warehouse"].lower()]
    (OUT_DIR / f"costco-gold-{today}.json").write_text(
        json.dumps(result, indent=1), encoding="utf-8")
    (OUT_DIR / f"dump-{today}.txt").write_text(
        "\n".join(dump_lines), encoding="utf-8")
    md = [f"# Costco gold (PC) — {today}",
          f"Run: {now_pt} | anchor {ANCHOR_ZIP} ({WAREHOUSE}) | "
          f"signed_in: {result['signed_in']}",
          f"In-stock flags: {len(in_stock)}", ""]
    for r in result["items"]:
        md.append(f"- {r['item']} {r['name']}: price {r['price'] or '—'} "
                  f"({r['price_note'] or 'shown'}) | online: "
                  f"{r['online'][:120] or '—'} | warehouse: "
                  f"{(r.get('warehouse') or '—')[:160]}")
    gc = result.get("gift_cards") or []
    on_sale = [c for c in gc if c.get("on_sale")]
    md.append("")
    md.append(f"## Gift cards on sale — {len(on_sale)} of {len(gc)} "
              f"listed below face value")
    for c in on_sale:
        md.append(f"- {c['title']}: ${c['price']:.2f} for "
                  f"${c['face_value']:.2f} face "
                  f"({c['discount_pct']}% off) [item {c['item']}]")
    unparsed = [c for c in gc
                if c.get("price") is not None and not c.get("on_sale")
                and c.get("discount_pct") is None]
    for c in unparsed:
        md.append(f"- (face value not parsed) {c['title']}: "
                  f"${c['price']:.2f} [item {c['item']}]")
    if result.get("gift_cards_error"):
        md.append(f"Gift-card check failed: {result['gift_cards_error']}")
    if result["rate_limited"]:
        md.append(f"\nRate-limited: {', '.join(result['rate_limited'])}")
    if result["errors"]:
        md.append("\nErrors: " + "; ".join(result["errors"]))
    (OUT_DIR / f"costco-gold-{today}.md").write_text(
        "\n".join(md), encoding="utf-8")
    priced = sum(1 for r in result["items"] if r["price"])
    print(f"SUMMARY: {len(result['items'])} items, prices {priced}/"
          f"{len(ITEMS)}, in-stock flags: {len(in_stock)}, "
          f"rate_limited: {result['rate_limited']}, "
          f"errors: {len(result['errors'])}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signin", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    COSTCO_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    if args.signin:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(COSTCO_PROFILE_DIR), headless=False,
                args=["--disable-blink-features=AutomationControlled"])
            page = ctx.new_page()
            page.goto("https://www.costco.com/", timeout=PAGE_TIMEOUT_MS,
                      wait_until="domcontentloaded")
            print("A browser window is open on costco.com. Sign in "
                  "YOURSELF in that window (and set My Warehouse to "
                  "Cherry Hill if it asks), then leave the window open.")
            print("Waiting up to 10 minutes for a signed-in session...")
            ok = False
            for _ in range(40):
                time.sleep(15)
                try:
                    ok = signed_in_probe(page)
                except Exception:  # noqa: BLE001 - keep waiting
                    ok = False
                if ok:
                    break
            verdict = ("signed in - product page shows a price"
                       if ok else "not signed in after 10 minutes")
            print(f"Costco sign-in result: {verdict}")
            try:
                DATA_DIR.mkdir(exist_ok=True)
                (DATA_DIR / "costco-signin-result.txt").write_text(
                    f"{datetime.now(ET).isoformat()} {verdict}",
                    encoding="utf-8")
            except OSError:
                pass
            ctx.close()
            return 0 if ok else 1
    if args.check:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(COSTCO_PROFILE_DIR), headless=True,
                args=["--disable-blink-features=AutomationControlled"])
            page = ctx.new_page()
            try:
                ok = signed_in_probe(page)
            except Exception as e:  # noqa: BLE001
                print(f"Costco session: check failed: {e}")
                ctx.close()
                return 1
            print(f"Costco session: {'signed in' if ok else 'NOT signed in'}")
            ctx.close()
            return 0 if ok else 1
    return run_daily()


if __name__ == "__main__":
    sys.exit(main())
