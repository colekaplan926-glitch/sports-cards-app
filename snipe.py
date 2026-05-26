"""
PSA Snipe Finder — core logic.

Flow per scan run:
  1. ListingFetcher grabs ACTIVE (not sold) raw card listings from eBay.
  2. Each listing is filtered: skip if title suggests already graded or a lot.
  3. Sold comps are fetched via pricing.PROVIDER_CHAIN (real data if API keys
     are set, otherwise mock estimates).
  4. Profits are calculated via calculations.calculate_profits.
  5. A BUY / WATCH / PASS recommendation is generated.
  6. Qualifying deals are written to the found_deals + sold_comps tables.

IMPORTANT:
  Active listing prices are cost (what you pay) — NOT market value.
  Market value always comes from SOLD/COMPLETED comps via pricing.py.
  Median sold price is used, not average.
"""

import json
import logging
import os
import random
import re
import sqlite3
import threading
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import requests

from calculations import calculate_profits, cardgrade_score as _cg_score
from pricing import PROVIDER_CHAIN

logger = logging.getLogger(__name__)

_data_dir = ".data" if os.path.isdir(".data") else "."
DB_PATH   = os.path.join(_data_dir, "sports_cards.db")

_GRADER_RE   = re.compile(r"\b(psa|bgs|sgc|cgc|beckett|hga)\s*\d", re.I)
_YEAR_RE     = re.compile(r"\b(19|20)\d{2}\b")
_CARDNUM_RE  = re.compile(r"#(\d+)")

# Cards-only eBay category IDs (used to narrow search)
EBAY_SPORTS_CARDS_CAT = "261328"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class RawListing:
    title:   str
    price:   float
    url:     str     = ""
    source:  str     = "eBay"
    is_mock: bool    = False


@dataclass
class DealAnalysis:
    listing:         RawListing
    prices:          dict         # GradeResult dicts keyed by grade
    calc:            dict         # output of calculate_profits()
    recommendation:  str          = "PASS"
    reason:          str          = ""
    break_even:      str          = "none"
    confidence:      str          = "low"
    is_mock:         bool         = False
    cardgrade_score: int          = 0
    player_name:     str          = ""
    year:            str          = ""
    set_name:        str          = ""
    card_number:     str          = ""


# ── Title parsing (best-effort) ───────────────────────────────────────────────

def _parse_year(title: str) -> str:
    m = _YEAR_RE.search(title)
    return m.group(0) if m else ""


def _parse_card_number(title: str) -> str:
    m = _CARDNUM_RE.search(title)
    return m.group(1) if m else ""


def _parse_player(title: str, known_players: List[str]) -> str:
    """Return the first known player name found in the title, or ''."""
    t = title.lower()
    for p in known_players:
        if p.lower() in t:
            return p
    return ""


def _is_graded(title: str) -> bool:
    return bool(_GRADER_RE.search(title))


def _is_lot(title: str) -> bool:
    t = title.lower()
    return bool(re.search(r"\blot\b", t)) or "lot of" in t


# ── Break-even & recommendation helpers ──────────────────────────────────────

def break_even_grade(results: dict) -> str:
    """
    Lowest PSA grade (by ascending severity: psa8 → psa9 → psa10) where
    grading profit is >= 0.  Returns 'none' if no grade breaks even.
    """
    for g in ("psa8", "psa9", "psa10"):
        if results[g]["profit"] >= 0:
            return g
    return "none"


def _overall_confidence(prices: dict) -> str:
    """Worst confidence across all four grades."""
    order = {"low": 0, "medium": 1, "high": 2}
    worst = "high"
    for g in ("raw", "psa8", "psa9", "psa10"):
        c = prices[g].get("confidence", "low")
        if order[c] < order[worst]:
            worst = c
    return worst


def snipe_recommendation(
    best_roi: float,
    best_profit: float,
    min_roi: float,
    min_profit: float,
    confidence: str,
    has_real_data: bool = True,
) -> tuple[str, str]:
    """
    Returns (recommendation, reason).

    BUY   — meets thresholds AND confidence is medium or high AND real comps exist.
    WATCH — meets thresholds BUT confidence is low (< 3 real comps).
    PASS  — does not meet thresholds OR no real comp data.
    """
    if not has_real_data:
        return "PASS", (
            "No real sold comps available for this card. "
            "Cannot make a data-driven recommendation."
        )

    meets = best_roi >= min_roi and best_profit >= min_profit

    if not meets:
        if best_roi < min_roi:
            reason = f"Best ROI is {best_roi:.1f}%, below your {min_roi:.0f}% minimum."
        else:
            reason = f"Best profit is ${best_profit:.2f}, below your ${min_profit:.2f} minimum."
        return "PASS", reason

    if confidence == "low":
        reason = (
            f"Numbers look good ({best_roi:.0f}% ROI, ${best_profit:.2f} profit) "
            f"but fewer than 3 real sold comps found. Verify manually before buying."
        )
        return "WATCH", reason

    reason = (
        f"{best_roi:.0f}% ROI and ${best_profit:.2f} profit "
        f"with {confidence} confidence ({confidence} comp count)."
    )
    return "BUY", reason


# ── Listing fetchers ──────────────────────────────────────────────────────────

class ListingFetcher:
    def fetch(self, search: dict) -> List[RawListing]:
        raise NotImplementedError


class MockListingFetcher(ListingFetcher):
    """
    Generates fake eBay listings for testing.
    ⚠ ALL DATA IS MOCK — never use these prices as real market data.
    Replace with ApifyListingFetcher for real listings.
    """

    _SETS = {
        "Football":   ["Panini Prizm", "Topps Chrome", "Panini Mosaic", "Select"],
        "Basketball": ["Panini Prizm", "Topps Chrome", "Hoops", "Select"],
        "Baseball":   ["Topps Chrome", "Bowman Chrome", "Panini Prizm", "Stadium Club"],
        "Soccer":     ["Panini Prizm", "Topps", "Select"],
        "Hockey":     ["Upper Deck", "OPC", "Topps Chrome", "Artifacts"],
    }
    _TEMPLATES = [
        "{year} {player} {set} #{num} Raw",
        "{year} {player} {set} RC Rookie",
        "{year} {player} {set} Base #{num}",
        "{player} {year} {set} #{num} NM-MT",
        "{year} {player} {set} Base RC",
    ]

    def fetch(self, search: dict) -> List[RawListing]:
        players   = json.loads(search.get("player_names", '["Sample Player"]')) or ["Sample Player"]
        max_price = float(search.get("max_raw_price", 100))
        year_min  = int(search.get("year_min") or 2018)
        year_max  = int(search.get("year_max") or 2024)
        sport     = search.get("sport", "Football")
        sets      = self._SETS.get(sport, ["Topps"])

        listings = []
        n = random.randint(4, 7)
        for i in range(n):
            player = random.choice(players)
            year   = random.randint(year_min, min(year_max, year_min + 6))
            price  = round(random.uniform(max_price * 0.20, max_price * 0.88), 2)
            s      = random.choice(sets)
            num    = random.randint(100, 499)
            title  = random.choice(self._TEMPLATES).format(
                year=year, player=player, set=s, num=num
            )
            listings.append(RawListing(
                title   = title,
                price   = price,
                url     = f"https://www.ebay.com/itm/mock-{random.randint(100_000, 999_999)}",
                source  = "eBay (MOCK)",
                is_mock = True,
            ))
        return listings


class ApifyListingFetcher(ListingFetcher):
    """
    Fetches ACTIVE (unsold) eBay raw card listings via Apify.

    Key difference from the sold-comp fetcher: NO LH_Sold / LH_Complete flags.
    These are buying opportunities, not market values.

    Set APIFY_TOKEN env var to activate. Adjust ACTOR_ID as needed.
    Apify actor: https://apify.com/junglee/ebay-items-scraper
    """
    ACTOR_ID = os.getenv("APIFY_ACTOR_ID", "junglee/ebay-items-scraper")
    API_BASE = "https://api.apify.com/v2"

    def __init__(self):
        self.token = os.getenv("APIFY_TOKEN", "")

    def is_available(self) -> bool:
        return bool(self.token)

    def _ebay_listing_url(self, query: str, max_price: float) -> str:
        q = urllib.parse.quote_plus(query + " raw")
        return (
            f"https://www.ebay.com/sch/i.html"
            f"?_nkw={q}"
            f"&LH_BIN=1"              # Buy It Now
            f"&_sop=15"               # Sort: price low → high
            f"&_udhi={int(max_price)}"# Max price
            f"&_sacat={EBAY_SPORTS_CARDS_CAT}"
        )

    def fetch(self, search: dict) -> List[RawListing]:
        players   = json.loads(search.get("player_names", "[]"))
        player    = players[0] if players else ""
        year_min  = search.get("year_min", "")
        max_price = float(search.get("max_raw_price", 200))

        query     = " ".join(p for p in [str(year_min) if year_min else "", player] if p)
        ebay_url  = self._ebay_listing_url(query, max_price)

        resp = requests.post(
            f"{self.API_BASE}/acts/{self.ACTOR_ID}/run-sync-get-dataset-items",
            params={"token": self.token, "timeout": 60, "memory": 256},
            json={"startUrls": [{"url": ebay_url}], "maxItems": 20},
            timeout=90,
        )
        resp.raise_for_status()

        listings = []
        for item in resp.json():
            title = item.get("title") or item.get("name") or ""
            price = self._parse_price(item)
            url   = item.get("url") or item.get("itemUrl") or ""

            if not title or price <= 0 or price > max_price:
                continue
            if _is_graded(title) or _is_lot(title):
                continue

            listings.append(RawListing(title=title, price=price, url=url,
                                       source="eBay", is_mock=False))
        return listings

    def _parse_price(self, item: dict) -> float:
        for key in ("price", "priceValue", "currentPrice", "priceAmount"):
            val = item.get(key)
            if val is None:
                continue
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, dict):
                for sub in ("value", "amount"):
                    if sub in val:
                        return float(val[sub])
            if isinstance(val, str):
                cleaned = re.sub(r"[^\d.]", "", val.split("–")[0].strip())
                try:
                    return float(cleaned)
                except ValueError:
                    pass
        return 0.0


def _get_listing_fetcher() -> ListingFetcher:
    f = ApifyListingFetcher()
    if f.is_available():
        return f
    return MockListingFetcher()


# ── Per-listing analysis ──────────────────────────────────────────────────────

def analyze_listing(
    listing: RawListing,
    search: dict,
    prices: dict,
) -> DealAnalysis:
    """
    Given a raw listing and already-fetched sold comps (prices dict),
    calculate all profit scenarios and produce a snipe recommendation.
    """
    players = json.loads(search.get("player_names", "[]"))

    calc = calculate_profits(
        raw_price      = listing.price,
        grading_cost   = float(search.get("grading_cost", 25)),
        shipping       = float(search.get("shipping_cost", 15)),
        selling_fee_pct= float(search.get("selling_fee_pct", 12.9)),
        prices         = prices,
    )

    results    = calc["results"]
    best       = calc["best"]
    confidence = _overall_confidence(prices)
    be_grade   = break_even_grade(results)

    has_real_data = any(
        prices[g].get("is_live") and prices[g].get("comp_count", 0) > 0
        for g in ("raw", "psa8", "psa9", "psa10")
    )

    rec, reason = snipe_recommendation(
        best_roi      = best["roi"],
        best_profit   = best["profit"],
        min_roi       = float(search.get("min_roi", 20)),
        min_profit    = float(search.get("min_profit", 0)),
        confidence    = confidence,
        has_real_data = has_real_data,
    )

    if has_real_data:
        if be_grade != "none":
            be_label = {"psa8": "PSA 8", "psa9": "PSA 9", "psa10": "PSA 10"}[be_grade]
            reason += f" Must reach at least {be_label} to break even."
        else:
            reason += " No grading path breaks even at this price."

    avg_comps = sum(prices[g].get("comp_count", 0) for g in ("raw","psa8","psa9","psa10")) // 4
    cg = _cg_score(
        best_roi      = best["roi"],
        confidence    = confidence,
        comp_count    = avg_comps,
        has_real_data = has_real_data,
        break_even    = be_grade,
    )

    is_mock = listing.is_mock or not has_real_data

    return DealAnalysis(
        listing         = listing,
        prices          = prices,
        calc            = calc,
        recommendation  = rec,
        reason          = reason,
        break_even      = be_grade,
        confidence      = confidence,
        is_mock         = is_mock,
        cardgrade_score = cg["score"],
        player_name     = _parse_player(listing.title, players),
        year            = _parse_year(listing.title),
        set_name        = "",
        card_number     = _parse_card_number(listing.title),
    )


# ── DB helpers (direct connection, safe for background threads) ───────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _save_deal(conn: sqlite3.Connection, deal: DealAnalysis,
               search_id: int, scan_run_id: int) -> int:
    results = deal.calc["results"]
    prices  = deal.prices
    cur = conn.execute(
        """INSERT INTO found_deals (
            search_id, scan_run_id,
            listing_title, listing_url, listing_price, listing_source,
            player_name, year, set_name, card_number, sport,
            raw_market, psa8_market, psa9_market, psa10_market,
            raw_profit, psa8_profit, psa9_profit, psa10_profit,
            raw_roi,    psa8_roi,    psa9_roi,    psa10_roi,
            best_option, best_profit, best_roi,
            break_even_grade, recommendation, reason,
            raw_comps, psa8_comps, psa9_comps, psa10_comps,
            confidence, data_source, is_mock, cardgrade_score
        ) VALUES (
            ?,?,  ?,?,?,?,  ?,?,?,?,?,
            ?,?,?,?,  ?,?,?,?,  ?,?,?,?,
            ?,?,?,  ?,?,?,
            ?,?,?,?,  ?,?,?,?
        )""",
        (
            search_id, scan_run_id,
            deal.listing.title, deal.listing.url, deal.listing.price,
            deal.listing.source,
            deal.player_name, deal.year, deal.set_name,
            deal.card_number, deal.calc["costs"].get("sport", ""),
            prices["raw"]["median_price"],  prices["psa8"]["median_price"],
            prices["psa9"]["median_price"], prices["psa10"]["median_price"],
            results["raw"]["profit"],   results["psa8"]["profit"],
            results["psa9"]["profit"],  results["psa10"]["profit"],
            results["raw"]["roi"],      results["psa8"]["roi"],
            results["psa9"]["roi"],     results["psa10"]["roi"],
            deal.calc["best"]["label"], deal.calc["best"]["profit"],
            deal.calc["best"]["roi"],
            deal.break_even, deal.recommendation, deal.reason,
            prices["raw"]["comp_count"],  prices["psa8"]["comp_count"],
            prices["psa9"]["comp_count"], prices["psa10"]["comp_count"],
            deal.confidence,
            prices["raw"]["source_name"],
            1 if deal.is_mock else 0,
            deal.cardgrade_score,
        ),
    )
    conn.commit()
    return cur.lastrowid


def _save_comps(conn: sqlite3.Connection, deal_id: int, prices: dict):
    for grade in ("raw", "psa8", "psa9", "psa10"):
        for comp in prices[grade].get("comps", []):
            conn.execute(
                """INSERT INTO sold_comps (deal_id, grade, price, title, url, sale_date, source)
                   VALUES (?,?,?,?,?,?,?)""",
                (deal_id, grade, comp["price"], comp["title"],
                 comp.get("url", ""), comp.get("date", ""),
                 prices[grade]["source_name"]),
            )
    conn.commit()


# ── Main scan orchestration ───────────────────────────────────────────────────

_scan_lock = threading.Lock()


def run_search(search_id: int) -> int:
    """
    Run a complete scan for one saved search.
    Returns scan_run_id.  Thread-safe; can be called from scheduler or API.

    Steps:
      1. Load search from DB
      2. Create scan_run record (status = 'running')
      3. Fetch ACTIVE eBay raw listings
      4. Fetch SOLD comps ONCE for the representative card (fast — reused per listing)
      5. For each listing: calculate profits, generate recommendation
      6. Save qualifying deals (ROI > 0 and profit > 0)
      7. Mark scan_run as complete
    """
    conn = _db()
    try:
        search = dict(conn.execute(
            "SELECT * FROM saved_searches WHERE id = ?", (search_id,)
        ).fetchone() or {})
        if not search:
            logger.error("Search %s not found", search_id)
            return -1

        # Create scan_run
        cur = conn.execute(
            """INSERT INTO scan_runs (search_id, started_at, status)
               VALUES (?, ?, 'running')""",
            (search_id, datetime.utcnow().isoformat()),
        )
        conn.commit()
        scan_run_id = cur.lastrowid

        try:
            listings_checked, deals_found = _do_scan(
                conn, search, search_id, scan_run_id
            )
            conn.execute(
                """UPDATE scan_runs SET finished_at=?, status='complete',
                   listings_checked=?, deals_found=? WHERE id=?""",
                (datetime.utcnow().isoformat(),
                 listings_checked, deals_found, scan_run_id),
            )
        except Exception as exc:
            logger.exception("Scan error for search %s", search_id)
            conn.execute(
                "UPDATE scan_runs SET status='error', error_msg=? WHERE id=?",
                (str(exc), scan_run_id),
            )
        conn.commit()
        return scan_run_id

    finally:
        conn.close()


def _do_scan(conn, search, search_id, scan_run_id) -> tuple[int, int]:
    fetcher  = _get_listing_fetcher()
    listings = fetcher.fetch(search)
    logger.info("Search %s: %d listings fetched", search_id, len(listings))

    if not listings:
        return 0, 0

    # Fetch sold comps ONCE for the representative card.
    # This is the card the search is targeting — all listings are assumed to be
    # variants of the same card type.  Reusing comps avoids N × 4 API calls.
    players = json.loads(search.get("player_names", "[]"))
    rep_card = {
        "player_name": players[0] if players else "",
        "year":        str(search.get("year_min") or ""),
        "set_name":    (json.loads(search.get("sets", "[]")) or [""])[0],
        "card_number": "",
        "sport":       search.get("sport", ""),
    }
    prices_obj = PROVIDER_CHAIN.fetch(
        raw_buy_price=float(search.get("max_raw_price", 100)),
        **rep_card,
    )
    prices = prices_obj.to_dict()

    deals_found = 0
    for listing in listings:
        if _is_graded(listing.title) or _is_lot(listing.title):
            continue
        if listing.price > float(search.get("max_raw_price", 9999)):
            continue

        deal = analyze_listing(listing, search, prices)

        # Only save real (non-mock) deals with positive ROI
        if deal.calc["best"]["roi"] > 0 and not deal.is_mock:
            deal_id = _save_deal(conn, deal, search_id, scan_run_id)
            _save_comps(conn, deal_id, prices)
            deals_found += 1

    conn.execute(
        "UPDATE saved_searches SET last_run_at=? WHERE id=?",
        (datetime.utcnow().isoformat(), search_id),
    )
    conn.commit()
    return len(listings), deals_found


def run_search_background(search_id: int) -> int:
    """
    Start run_search in a daemon thread.
    Returns scan_run_id immediately (scan continues in background).
    """
    # Reserve a scan_run_id synchronously so the caller can poll status
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO scan_runs (search_id, started_at, status)
               VALUES (?, ?, 'running')""",
            (search_id, datetime.utcnow().isoformat()),
        )
        conn.commit()
        scan_run_id = cur.lastrowid
    finally:
        conn.close()

    def _worker():
        conn2 = _db()
        try:
            search = dict(conn2.execute(
                "SELECT * FROM saved_searches WHERE id=?", (search_id,)
            ).fetchone() or {})
            if not search:
                conn2.execute(
                    "UPDATE scan_runs SET status='error', error_msg='search not found' WHERE id=?",
                    (scan_run_id,)
                )
                conn2.commit()
                return
            try:
                lc, df = _do_scan(conn2, search, search_id, scan_run_id)
                conn2.execute(
                    """UPDATE scan_runs SET finished_at=?, status='complete',
                       listings_checked=?, deals_found=? WHERE id=?""",
                    (datetime.utcnow().isoformat(), lc, df, scan_run_id),
                )
            except Exception as exc:
                logger.exception("Background scan error")
                conn2.execute(
                    "UPDATE scan_runs SET status='error', error_msg=? WHERE id=?",
                    (str(exc), scan_run_id),
                )
            conn2.commit()
        finally:
            conn2.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return scan_run_id
