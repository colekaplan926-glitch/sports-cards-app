"""
Three-tier pricing provider chain:
  1. SportsCardsProProvider  — structured market API (set SPORTSCARDSPRO_API_KEY)
  2. ApifyEbayProvider       — eBay sold comps via Apify (set APIFY_TOKEN)
  3. MockPricingProvider     — multiplier estimates, always available as last resort

Priority: SportsCardsPro → Apify → Mock
Per-grade fallback: if a live provider returns < MIN_LIVE_COMPS for a grade,
the next provider is tried for that grade only.

To swap in a different data source, subclass PricingProvider and insert it
into PROVIDER_CHAIN at the bottom of this file.
"""

import os
import re
import logging
import statistics
import urllib.parse
from dataclasses import dataclass, field
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logger = logging.getLogger(__name__)

MIN_LIVE_COMPS = 3   # fewer than this → "low confidence"
FETCH_TIMEOUT  = 90  # seconds — Apify actor runs can be slow


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class PriceComp:
    price: float
    title: str
    url:   str = ""
    date:  str = ""


@dataclass
class GradeResult:
    median_price: float
    avg_price:    float
    low_price:    float
    high_price:   float
    comp_count:   int
    date_range:   str
    confidence:   str   # "high" | "medium" | "low"
    source_name:  str
    is_live:      bool
    comps: List[PriceComp] = field(default_factory=list)

    def to_dict(self):
        return {
            "median_price": self.median_price,
            "avg_price":    self.avg_price,
            "low_price":    self.low_price,
            "high_price":   self.high_price,
            "comp_count":   self.comp_count,
            "date_range":   self.date_range,
            "confidence":   self.confidence,
            "source_name":  self.source_name,
            "is_live":      self.is_live,
            "comps": [
                {"price": c.price, "title": c.title, "url": c.url, "date": c.date}
                for c in self.comps
            ],
        }


@dataclass
class CardPrices:
    raw:   GradeResult
    psa8:  GradeResult
    psa9:  GradeResult
    psa10: GradeResult
    provider_name: str

    def to_dict(self):
        return {
            "raw":   self.raw.to_dict(),
            "psa8":  self.psa8.to_dict(),
            "psa9":  self.psa9.to_dict(),
            "psa10": self.psa10.to_dict(),
            "provider_name": self.provider_name,
        }


# ── Filtering & query helpers ─────────────────────────────────────────────────

_EXCLUDE_KW  = ["lot ", " lot,", "lot of ", "reprint", "custom card",
                 "fake", "proxy", "fantasy"]
_AUTO_KW     = ["auto ", "autograph", "auto/", "/auto", "signed "]
_GRADER_RE   = re.compile(r'\b(psa|bgs|sgc|cgc|beckett|hga)\b', re.I)
_PARALLEL_RE = re.compile(r'/\s*\d{1,4}\b')   # /25  /50  /99  /100
_GRADE_RE    = {
    "psa10": re.compile(r'\bpsa\s*10\b',          re.I),
    "psa9":  re.compile(r'\bpsa\s*9(?!\d)\b',     re.I),
    "psa8":  re.compile(r'\bpsa\s*8(?!\d)\b',     re.I),
}


def _confidence(n: int) -> str:
    if n >= 6: return "high"
    if n >= 3: return "medium"
    return "low"


def _compute_stats(comps: List[PriceComp]):
    """Return (median, avg, low, high, date_range)."""
    prices = [c.price for c in comps]
    dates  = sorted(
        [c.date for c in comps if c.date and c.date not in ("—", "", "None")],
        reverse=True,
    )
    if len(dates) >= 2:
        date_range = f"{dates[-1][:10]} – {dates[0][:10]}"
    elif dates:
        date_range = dates[0][:10]
    else:
        date_range = "—"
    return (
        round(statistics.median(prices), 2),
        round(statistics.mean(prices),   2),
        round(min(prices), 2),
        round(max(prices), 2),
        date_range,
    )


def _make_result(comps: List[PriceComp], source_name: str, is_live: bool) -> GradeResult:
    if not comps:
        return GradeResult(0, 0, 0, 0, 0, "—", "low", source_name, is_live)
    med, avg, lo, hi, dr = _compute_stats(comps)
    return GradeResult(
        median_price=med, avg_price=avg, low_price=lo, high_price=hi,
        comp_count=len(comps), date_range=dr,
        confidence=_confidence(len(comps)),
        source_name=source_name, is_live=is_live,
        comps=comps[:10],   # cap stored comps; median already uses all
    )


def _is_excluded(title: str, grade: str, include_autos: bool = False) -> bool:
    """Return True if this listing should be filtered out."""
    t = title.lower()

    if any(kw in t for kw in _EXCLUDE_KW):
        return True
    # Word-boundary "lot" check
    if re.search(r'\blot\b', t):
        return True
    # Auto filter
    if not include_autos and any(kw in t for kw in _AUTO_KW):
        return True
    # Numbered parallel filter  (/25, /50, etc.)
    if _PARALLEL_RE.search(title):
        return True

    if grade == "raw":
        # Raw search must not contain a grading company name
        if _GRADER_RE.search(title):
            return True
    else:
        # Graded search: must match the exact grade requested
        pat = _GRADE_RE.get(grade)
        if pat and not pat.search(title):
            return True

    return False


def _build_query(player_name: str, year: str, set_name: str,
                 card_number: str, grade: str) -> str:
    """Build the search string for a card + grade combo."""
    parts = [p for p in [year, player_name, set_name] if p]
    if card_number:
        parts.append(f"#{card_number}")
    if grade != "raw":
        label = {"psa8": "PSA 8", "psa9": "PSA 9", "psa10": "PSA 10"}[grade]
        parts.append(label)
    return " ".join(parts)


def _parse_price(item: dict) -> float:
    """Extract a float price from a dict that may use many field shapes."""
    for key in ("price", "soldPrice", "priceValue", "currentPrice",
                "priceAmount", "sold_price", "sale_price"):
        val = item.get(key)
        if val is None:
            continue
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, dict):
            for sub in ("value", "amount", "minValue", "min"):
                if sub in val:
                    try:
                        return float(val[sub])
                    except (TypeError, ValueError):
                        pass
        if isinstance(val, str):
            cleaned = re.sub(r"[^\d.]", "", val.split("–")[0].split("-")[0].strip())
            try:
                return float(cleaned)
            except ValueError:
                pass
    return 0.0


# ── Base class ────────────────────────────────────────────────────────────────

class PricingProvider:
    name = "unknown"

    def is_available(self) -> bool:
        raise NotImplementedError

    def fetch_all_grades(
        self,
        player_name: str,
        year: str,
        set_name: str,
        card_number: str,
        sport: str,
        raw_buy_price: float = 0,
        include_autos: bool = False,
    ) -> CardPrices:
        raise NotImplementedError


# ── Provider 1: SportsCardsPro ────────────────────────────────────────────────

class SportsCardsProProvider(PricingProvider):
    """
    SportsCardsPro market data API.

    Obtain your API key at https://sportscardspro.com — log in and look for
    a developer / API section, or contact their support.

    Set env var: SPORTSCARDSPRO_API_KEY

    The endpoint path and response schema below are based on standard REST
    conventions. Adjust _parse_items() if the actual schema differs.
    To override the base URL: SPORTSCARDSPRO_BASE_URL env var.
    """
    name     = "SportsCardsPro"
    BASE_URL = os.getenv("SPORTSCARDSPRO_BASE_URL",
                         "https://sportscardspro.com/api")

    def __init__(self):
        self.api_key = os.getenv("SPORTSCARDSPRO_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _grade_param(self, grade: str) -> str:
        return {"raw": "raw", "psa8": "PSA 8",
                "psa9": "PSA 9", "psa10": "PSA 10"}[grade]

    def _fetch_grade(self, player_name: str, year: str, set_name: str,
                     card_number: str, grade: str,
                     include_autos: bool) -> List[PriceComp]:
        query = _build_query(player_name, year, set_name, card_number, grade)
        resp = requests.get(
            f"{self.BASE_URL}/cards/search",
            params={
                "key":   self.api_key,
                "q":     query,
                "grade": self._grade_param(grade),
                "sold":  "true",
                "limit": 30,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        # Accept both {"results": [...]} and bare list
        items = data.get("results",
                data.get("data",
                data.get("items",
                data if isinstance(data, list) else [])))

        comps = []
        for item in items:
            title = (item.get("title") or item.get("name") or
                     item.get("description") or "")
            price = _parse_price(item)
            url   = item.get("url",  item.get("link",      ""))
            date  = str(item.get("sold_date",
                        item.get("date",
                        item.get("sale_date", ""))))
            if price > 0 and title and not _is_excluded(title, grade, include_autos):
                comps.append(PriceComp(price=price, title=title,
                                       url=url, date=date))
        return comps

    def fetch_all_grades(self, player_name, year, set_name, card_number,
                         sport, raw_buy_price=0, include_autos=False) -> CardPrices:
        grades = ["raw", "psa8", "psa9", "psa10"]
        results = {}
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {
                ex.submit(self._fetch_grade, player_name, year, set_name,
                          card_number, g, include_autos): g
                for g in grades
            }
            for fut in as_completed(futures, timeout=25):
                g = futures[fut]
                try:
                    comps = fut.result()
                except Exception as exc:
                    logger.warning("SportsCardsPro %s: %s", g, exc)
                    comps = []
                results[g] = _make_result(comps, self.name, is_live=True)
        return CardPrices(
            raw=results["raw"], psa8=results["psa8"],
            psa9=results["psa9"], psa10=results["psa10"],
            provider_name=self.name,
        )


# ── Provider 2: Apify eBay Sold Listings ─────────────────────────────────────

class ApifyEbayProvider(PricingProvider):
    """
    Scrapes eBay completed/sold listings via Apify.

    Set env var: APIFY_TOKEN
    Optional:    APIFY_ACTOR_ID  (default: junglee/ebay-items-scraper)

    The actor receives a pre-built eBay "completed listings" search URL so
    that eBay's own filters guarantee only sold items are returned.
    Adjust _parse_item() if you switch to a different actor with a different
    output schema.

    Apify actor marketplace: https://apify.com/store
    Documentation for the default actor:
      https://apify.com/junglee/ebay-items-scraper
    """
    name     = "eBay Sold Comps"
    ACTOR_ID = os.getenv("APIFY_ACTOR_ID", "junglee/ebay-items-scraper")
    API_BASE = "https://api.apify.com/v2"

    def __init__(self):
        self.token = os.getenv("APIFY_TOKEN", "")

    def is_available(self) -> bool:
        return bool(self.token)

    def _ebay_sold_url(self, query: str) -> str:
        """
        Build an eBay completed/sold-listings search URL.
        LH_Sold=1   → sold items only
        LH_Complete=1 → completed listings
        _sop=13     → sort: recently sold first
        """
        q = urllib.parse.quote_plus(query)
        return (
            f"https://www.ebay.com/sch/i.html"
            f"?_nkw={q}&LH_Sold=1&LH_Complete=1&_sop=13&_sacat=0"
        )

    def _run_actor(self, ebay_url: str) -> list:
        """
        Run the Apify actor synchronously and return the dataset items.
        Sync endpoint blocks until the run finishes or timeout is hit.
        """
        endpoint = (
            f"{self.API_BASE}/acts/{self.ACTOR_ID}"
            f"/run-sync-get-dataset-items"
        )
        resp = requests.post(
            endpoint,
            params={"token": self.token, "timeout": 60, "memory": 256},
            json={
                "startUrls": [{"url": ebay_url}],
                "maxItems":  30,
            },
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def _fetch_grade(self, player_name: str, year: str, set_name: str,
                     card_number: str, grade: str,
                     include_autos: bool) -> List[PriceComp]:
        query    = _build_query(player_name, year, set_name, card_number, grade)
        ebay_url = self._ebay_sold_url(query)
        items    = self._run_actor(ebay_url)

        comps = []
        for item in items:
            title = (item.get("title") or item.get("name") or "")
            price = _parse_price(item)
            url   = (item.get("url") or item.get("itemUrl") or "")
            date  = str(
                item.get("soldDate") or item.get("date") or
                item.get("endDate")  or item.get("lastUpdated") or ""
            )
            if price > 0 and title and not _is_excluded(title, grade, include_autos):
                comps.append(PriceComp(price=price, title=title,
                                       url=url, date=date))
        return comps

    def fetch_all_grades(self, player_name, year, set_name, card_number,
                         sport, raw_buy_price=0, include_autos=False) -> CardPrices:
        grades  = ["raw", "psa8", "psa9", "psa10"]
        results = {}
        # Run all four grade queries in parallel to minimise wall-clock time
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {
                ex.submit(self._fetch_grade, player_name, year, set_name,
                          card_number, g, include_autos): g
                for g in grades
            }
            for fut in as_completed(futures, timeout=FETCH_TIMEOUT + 10):
                g = futures[fut]
                try:
                    comps = fut.result()
                except Exception as exc:
                    logger.warning("Apify eBay %s: %s", g, exc)
                    comps = []
                results[g] = _make_result(comps, self.name, is_live=True)
        return CardPrices(
            raw=results.get("raw",  _make_result([], self.name, True)),
            psa8=results.get("psa8", _make_result([], self.name, True)),
            psa9=results.get("psa9", _make_result([], self.name, True)),
            psa10=results.get("psa10",_make_result([], self.name, True)),
            provider_name=self.name,
        )


# ── Provider 3: Mock (always-available fallback) ──────────────────────────────

class MockPricingProvider(PricingProvider):
    """
    Estimates prices using sport-specific PSA multipliers over raw buy price.
    Returns is_live=False and comp_count=0 so the UI shows "Estimated placeholder."
    This is NEVER shown as real pricing — it exists only so the calculator stays
    functional when no API keys are configured.
    """
    name = "Estimated (mock)"

    _MULT = {
        "Football":   (4.5, 2.0, 1.30),
        "Basketball": (5.0, 2.2, 1.35),
        "Baseball":   (4.0, 1.8, 1.20),
        "Soccer":     (3.5, 1.7, 1.15),
        "Hockey":     (3.8, 1.8, 1.20),
        "Golf":       (3.5, 1.7, 1.15),
        "Tennis":     (3.5, 1.7, 1.15),
    }
    _DEFAULT = (4.0, 1.9, 1.25)

    def is_available(self) -> bool:
        return True

    def _mock_grade(self, price: float, label: str) -> GradeResult:
        if price <= 0:
            return GradeResult(0, 0, 0, 0, 0, "—", "low", self.name, False)
        return GradeResult(
            median_price=price, avg_price=price,
            low_price=price,    high_price=price,
            comp_count=0, date_range="—",
            confidence="low", source_name=self.name,
            is_live=False,
            comps=[PriceComp(price=price, title=f"[estimated] {label}")],
        )

    def fetch_all_grades(self, player_name, year, set_name, card_number,
                         sport, raw_buy_price=0, include_autos=False) -> CardPrices:
        m10, m9, m8 = self._MULT.get(sport, self._DEFAULT)
        base = float(raw_buy_price or 0)
        return CardPrices(
            raw=self._mock_grade(base,                    "raw"),
            psa8=self._mock_grade(round(base * m8,  2),  "PSA 8"),
            psa9=self._mock_grade(round(base * m9,  2),  "PSA 9"),
            psa10=self._mock_grade(round(base * m10, 2), "PSA 10"),
            provider_name=self.name,
        )


# ── Orchestrator ──────────────────────────────────────────────────────────────

class PricingOrchestrator:
    """
    Tries providers in priority order.
    Falls back on a per-grade basis: if the primary returns < MIN_LIVE_COMPS
    for a grade, the next provider is tried for that specific grade.
    Mock is always the final fallback.
    """

    def __init__(self, providers: List[PricingProvider]):
        self.providers = providers
        self._mock = MockPricingProvider()

    def _needs_fallback(self, cp: CardPrices) -> List[str]:
        return [
            g for g in ("raw", "psa8", "psa9", "psa10")
            if getattr(cp, g).comp_count < MIN_LIVE_COMPS
        ]

    def _patch(self, base: CardPrices, patch: CardPrices,
               grades: List[str]) -> CardPrices:
        """Replace specific grades in base with results from patch."""
        d = {
            "raw":   base.raw,
            "psa8":  base.psa8,
            "psa9":  base.psa9,
            "psa10": base.psa10,
        }
        for g in grades:
            candidate = getattr(patch, g)
            if candidate.comp_count > getattr(base, g).comp_count:
                d[g] = candidate
        return CardPrices(**d, provider_name=base.provider_name)

    def fetch(
        self,
        player_name: str,
        year: str,
        set_name: str,
        card_number: str,
        sport: str,
        raw_buy_price: float = 0,
        include_autos: bool = False,
    ) -> CardPrices:
        kwargs = dict(
            player_name=player_name, year=year, set_name=set_name,
            card_number=card_number, sport=sport,
            raw_buy_price=raw_buy_price, include_autos=include_autos,
        )

        result: Optional[CardPrices] = None

        for provider in self.providers:
            if not provider.is_available():
                continue

            # Mock is always last; only reach it if all live providers failed
            if isinstance(provider, MockPricingProvider):
                if result is None:
                    logger.info("All live providers failed — using mock estimates")
                    result = provider.fetch_all_grades(**kwargs)
                else:
                    # Fill any remaining zero-comp grades with mock estimates
                    mock_result = provider.fetch_all_grades(**kwargs)
                    low = self._needs_fallback(result)
                    if low:
                        result = self._patch(result, mock_result, low)
                break

            try:
                candidate = provider.fetch_all_grades(**kwargs)
            except Exception as exc:
                logger.error("Provider %s failed: %s", provider.name, exc)
                continue

            if result is None:
                result = candidate
            else:
                low = self._needs_fallback(result)
                result = self._patch(result, candidate, low)

            if not self._needs_fallback(result):
                break   # All grades have enough comps — stop here

        if result is None:
            result = self._mock.fetch_all_grades(**kwargs)

        return result


# ── Active chain ──────────────────────────────────────────────────────────────
# Change order or add providers here to adjust the fallback chain.

PROVIDER_CHAIN = PricingOrchestrator([
    SportsCardsProProvider(),
    ApifyEbayProvider(),
    MockPricingProvider(),
])
