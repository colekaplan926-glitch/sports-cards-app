"""
Live pricing provider chain (free-first, no paid API required):

  1. SportsCardsProProvider  — structured API (set SPORTSCARDSPRO_API_KEY)
  2. ApifyEbayProvider       — Apify eBay actor  (set APIFY_TOKEN)
  3. EbayScraperProvider     — FREE direct eBay sold-listing scrape (always on)
  4. Point130Provider        — FREE 130point.com backup              (always on)

All providers share the same GradeResult / CardPrices models.
No mock / estimated data is ever returned — when 0 comps are found the
UI shows "No verified comps found" with the specific reason why.
"""

import os
import re
import time
import random
import logging
import threading
import statistics
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

try:
    from bs4 import BeautifulSoup as _BS
    _HAS_BS4 = True
except ImportError:                         # pragma: no cover
    _HAS_BS4 = False

logger = logging.getLogger(__name__)

MIN_LIVE_COMPS = 3    # below this → "low" confidence
FETCH_TIMEOUT  = 120  # outer wall-clock limit per provider call


# ── Thread-safe comp cache (60-min TTL) ────────────────────────────────────────

class _CompsCache:
    _lock  = threading.Lock()
    _store: dict = {}
    TTL    = timedelta(minutes=60)

    @classmethod
    def get(cls, key: str) -> Optional[list]:
        with cls._lock:
            if key in cls._store:
                val, ts = cls._store[key]
                if datetime.utcnow() - ts < cls.TTL:
                    return val
                del cls._store[key]
        return None

    @classmethod
    def set(cls, key: str, val: list):
        with cls._lock:
            cls._store[key] = (val, datetime.utcnow())


# ── Data models ────────────────────────────────────────────────────────────────

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
    fetch_note:   str = ""          # why we got 0 comps (shown in UI)
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
            "fetch_note":   self.fetch_note,
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
    warnings: List[str] = field(default_factory=list)   # surfaced in API response

    def to_dict(self):
        return {
            "raw":           self.raw.to_dict(),
            "psa8":          self.psa8.to_dict(),
            "psa9":          self.psa9.to_dict(),
            "psa10":         self.psa10.to_dict(),
            "provider_name": self.provider_name,
            "warnings":      self.warnings,
        }


# ── Filtering & query helpers ──────────────────────────────────────────────────

_EXCLUDE_KW = [
    "lot ", " lot,", "lot of ", "reprint", "custom card", "fake", "proxy",
    "fantasy", "sticker auto", "redemption", "commemorative", "buyback",
    "artist proof", "blank back", "sample ",
]
_AUTO_KW = ["auto ", "autograph", "auto/", "/auto", "signed ", " auto#"]

_GRADER_RE   = re.compile(r'\b(psa|bgs|sgc|cgc|beckett|hga)\b', re.I)
_PARALLEL_RE = re.compile(r'/\s*\d{1,4}\b')   # /25  /50  /99  etc.
_GRADE_RE    = {
    "psa10": re.compile(r'\bpsa\s*(?:gem\s*mint\s*)?10\b', re.I),
    "psa9":  re.compile(r'\bpsa\s*9(?!\d)\b',              re.I),
    "psa8":  re.compile(r'\bpsa\s*8(?!\d)\b',              re.I),
}


def _confidence(n: int, is_live: bool = True) -> str:
    if not is_live or n == 0: return "low"
    if n >= 5:  return "high"
    if n >= 3:  return "medium"
    return "low"


def _percentile(data: list, pct: float) -> float:
    if not data: return 0.0
    n   = len(data)
    idx = (n - 1) * pct / 100.0
    lo  = int(idx)
    hi  = min(lo + 1, n - 1)
    return data[lo] + (data[hi] - data[lo]) * (idx - lo)


def _remove_outliers(comps: List[PriceComp]) -> List[PriceComp]:
    """Tukey IQR fence — needs ≥4 comps to activate."""
    if len(comps) < 4:
        return comps
    prices = sorted(c.price for c in comps)
    q1, q3 = _percentile(prices, 25), _percentile(prices, 75)
    iqr    = q3 - q1
    if iqr == 0:
        return comps
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    cleaned = [c for c in comps if lo <= c.price <= hi]
    return cleaned if cleaned else comps


def _compute_stats(comps: List[PriceComp]):
    prices = [c.price for c in comps]
    dates  = sorted(
        [c.date for c in comps if c.date and c.date not in ("—", "", "None")],
        reverse=True,
    )
    dr = (f"{dates[-1][:10]} – {dates[0][:10]}" if len(dates) >= 2
          else dates[0][:10] if dates else "—")
    return (
        round(statistics.median(prices), 2),
        round(statistics.mean(prices),   2),
        round(min(prices), 2),
        round(max(prices), 2),
        dr,
    )


def _make_result(comps: List[PriceComp], source_name: str, is_live: bool,
                 note: str = "") -> GradeResult:
    if not comps:
        return GradeResult(0, 0, 0, 0, 0, "—", "low", source_name, is_live,
                           fetch_note=note)
    cleaned = _remove_outliers(comps)
    med, avg, lo, hi, dr = _compute_stats(cleaned)
    return GradeResult(
        median_price=med, avg_price=avg, low_price=lo, high_price=hi,
        comp_count=len(cleaned), date_range=dr,
        confidence=_confidence(len(cleaned), is_live),
        source_name=source_name, is_live=is_live,
        fetch_note=note,
        comps=cleaned[:10],
    )


def _is_excluded(title: str, grade: str, include_autos: bool = False) -> bool:
    t = title.lower()
    if any(kw in t for kw in _EXCLUDE_KW):             return True
    if re.search(r'\blot\b', t):                        return True
    if not include_autos and any(kw in t for kw in _AUTO_KW): return True
    if _PARALLEL_RE.search(title):                      return True
    if grade == "raw":
        if _GRADER_RE.search(title):                    return True
    else:
        pat = _GRADE_RE.get(grade)
        if pat and not pat.search(title):               return True
    return False


def _build_query(player_name: str, year: str, set_name: str,
                 card_number: str, grade: str, variation: str = "") -> str:
    parts = [p for p in [year, player_name, set_name, variation] if p]
    if card_number:
        parts.append(f"#{card_number}")
    if grade != "raw":
        parts.append({"psa8": "PSA 8", "psa9": "PSA 9", "psa10": "PSA 10"}[grade])
    return " ".join(parts)


def _parse_price(item: dict) -> float:
    """Extract float price from a dict (used by SCP and Apify providers)."""
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


def _parse_price_text(text: str) -> float:
    """Parse price from a plain HTML string like '$350.00' or 'US $350.00'."""
    if not text:
        return 0.0
    # Take lower end of "X to Y" price ranges
    text    = re.split(r'\bto\b', text, maxsplit=1)[0]
    cleaned = re.sub(r"[^\d.]", "", text.strip())
    try:
        v = float(cleaned)
        return v if 0.5 < v < 500_000 else 0.0   # sanity bounds
    except ValueError:
        return 0.0


def _parse_date_text(text: str) -> str:
    """Extract ISO date from 'Sold  Nov 15, 2024' or similar eBay date strings."""
    if not text:
        return ""
    m = re.search(r'([A-Za-z]{3}\.?\s+\d{1,2},?\s+\d{4})', text)
    if not m:
        m = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', text)
    if not m:
        return ""
    try:
        raw = re.sub(r'[,.]', '', m.group(1)).strip()
        for fmt in ('%b %d %Y', '%m/%d/%Y', '%m/%d/%y', '%B %d %Y'):
            try:
                return datetime.strptime(raw, fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue
    except Exception:
        pass
    return ""


def _empty_card_prices(note: str = "No data source configured") -> CardPrices:
    empty = GradeResult(0, 0, 0, 0, 0, "—", "low", note, False, fetch_note=note)
    return CardPrices(raw=empty, psa8=empty, psa9=empty, psa10=empty,
                      provider_name="none")


# ── Base provider class ───────────────────────────────────────────────────────

class PricingProvider:
    name = "unknown"

    def is_available(self) -> bool:
        raise NotImplementedError

    def fetch_all_grades(
        self, player_name, year, set_name, card_number,
        sport, raw_buy_price=0, include_autos=False, variation="",
    ) -> "CardPrices":
        raise NotImplementedError


# ── Provider 1: SportsCardsPro ────────────────────────────────────────────────

class SportsCardsProProvider(PricingProvider):
    name     = "SportsCardsPro"
    BASE_URL = os.getenv("SPORTSCARDSPRO_BASE_URL", "https://sportscardspro.com/api")

    def __init__(self):
        self.api_key = os.getenv("SPORTSCARDSPRO_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _grade_param(self, grade: str) -> str:
        return {"raw": "raw", "psa8": "PSA 8", "psa9": "PSA 9", "psa10": "PSA 10"}[grade]

    def _fetch_grade(self, player_name, year, set_name, card_number,
                     grade, include_autos, variation="") -> List[PriceComp]:
        query = _build_query(player_name, year, set_name, card_number, grade, variation)
        resp  = requests.get(
            f"{self.BASE_URL}/cards/search",
            params={"key": self.api_key, "q": query,
                    "grade": self._grade_param(grade), "sold": "true", "limit": 30},
            timeout=15,
        )
        resp.raise_for_status()
        data  = resp.json()
        items = data.get("results", data.get("data", data.get("items",
                data if isinstance(data, list) else [])))
        comps = []
        for item in items:
            title = (item.get("title") or item.get("name") or "")
            price = _parse_price(item)
            url   = item.get("url",  item.get("link",  ""))
            date  = str(item.get("sold_date", item.get("date", item.get("sale_date", ""))))
            if price > 0 and title and not _is_excluded(title, grade, include_autos):
                comps.append(PriceComp(price=price, title=title, url=url, date=date))
        return comps

    def fetch_all_grades(self, player_name, year, set_name, card_number,
                         sport, raw_buy_price=0, include_autos=False,
                         variation="") -> CardPrices:
        grades, results = ["raw","psa8","psa9","psa10"], {}
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {
                ex.submit(self._fetch_grade, player_name, year, set_name,
                          card_number, g, include_autos, variation): g
                for g in grades
            }
            for fut in as_completed(futures, timeout=25):
                g = futures[fut]
                try:
                    comps = fut.result()
                except Exception as exc:
                    logger.warning("SCP [%s]: %s", g, exc)
                    comps = []
                results[g] = _make_result(comps, self.name, is_live=True)
        return CardPrices(raw=results["raw"], psa8=results["psa8"],
                          psa9=results["psa9"], psa10=results["psa10"],
                          provider_name=self.name)


# ── Provider 2: Apify eBay (optional, paid) ───────────────────────────────────

class ApifyEbayProvider(PricingProvider):
    """
    eBay sold listings via Apify actor. Requires APIFY_TOKEN env var.
    Uses async run → poll → dataset approach to avoid sync-endpoint timeouts.
    """
    name     = "eBay (Apify)"
    ACTOR_ID = os.getenv("APIFY_ACTOR_ID", "junglee/ebay-items-scraper")
    API_BASE = "https://api.apify.com/v2"

    def __init__(self):
        self.token = os.getenv("APIFY_TOKEN", "")

    def is_available(self) -> bool:
        return bool(self.token)

    def _ebay_sold_url(self, query: str) -> str:
        q = urllib.parse.quote_plus(query)
        return (f"https://www.ebay.com/sch/i.html"
                f"?_nkw={q}&LH_Sold=1&LH_Complete=1&_sop=13&_sacat=0")

    def _run_actor(self, ebay_url: str) -> list:
        logger.info("Apify: starting actor for %r", ebay_url[:80])
        # Start
        r = requests.post(
            f"{self.API_BASE}/acts/{self.ACTOR_ID}/runs",
            params={"token": self.token},
            json={"startUrls": [{"url": ebay_url}], "maxItems": 30},
            timeout=30,
        )
        if r.status_code == 401:
            raise PermissionError("APIFY_TOKEN invalid or expired (HTTP 401)")
        if r.status_code == 404:
            raise ValueError(f"Apify actor not found: {self.ACTOR_ID}")
        r.raise_for_status()
        run_id = (r.json().get("data") or {}).get("id") or r.json().get("id")
        if not run_id:
            raise ValueError(f"No run ID in Apify response: {str(r.json())[:200]}")
        logger.info("Apify: run %s started", run_id)
        # Poll
        run_data, status = {}, None
        for attempt in range(24):          # 24 × 5s = 120s max
            time.sleep(5 if attempt else 3)
            sr = requests.get(f"{self.API_BASE}/actor-runs/{run_id}",
                              params={"token": self.token}, timeout=15)
            sr.raise_for_status()
            run_data = sr.json().get("data") or sr.json()
            status   = run_data.get("status", "UNKNOWN")
            logger.debug("Apify run %s → %s (poll %d)", run_id, status, attempt + 1)
            if status == "SUCCEEDED": break
            if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                raise RuntimeError(f"Apify run {status}: {run_id}")
        else:
            raise TimeoutError(f"Apify run {run_id} did not finish in 120s (status={status})")
        # Fetch dataset
        dataset_id = run_data.get("defaultDatasetId")
        if not dataset_id:
            raise ValueError(f"No defaultDatasetId in run {run_id}")
        dr = requests.get(f"{self.API_BASE}/datasets/{dataset_id}/items",
                          params={"token": self.token, "limit": 50}, timeout=30)
        dr.raise_for_status()
        items = dr.json()
        logger.info("Apify: dataset %s → %d items", dataset_id,
                    len(items) if isinstance(items, list) else 0)
        if items and isinstance(items, list):
            logger.debug("Apify first item: %s", str(items[0])[:300])
        return items if isinstance(items, list) else []

    def _fetch_grade(self, player_name, year, set_name, card_number, grade,
                     include_autos, variation="") -> Tuple[List[PriceComp], str]:
        query    = _build_query(player_name, year, set_name, card_number, grade, variation)
        ebay_url = self._ebay_sold_url(query)
        logger.info("Apify [%s] query=%r", grade, query)
        try:
            items = self._run_actor(ebay_url)
        except Exception as exc:
            logger.error("Apify [%s] actor failed: %s", grade, exc)
            return [], str(exc)
        comps, skipped = [], {}
        for item in items:
            title = item.get("title") or item.get("name") or ""
            price = _parse_price(item)
            url   = item.get("url") or item.get("itemUrl") or ""
            date  = str(item.get("soldDate") or item.get("endDate") or
                        item.get("date") or item.get("lastUpdated") or "")
            if not title:
                skipped["no_title"] = skipped.get("no_title", 0) + 1; continue
            if price <= 0:
                skipped["no_price"] = skipped.get("no_price", 0) + 1; continue
            if _is_excluded(title, grade, include_autos):
                skipped["filtered"] = skipped.get("filtered", 0) + 1
                logger.debug("Apify [%s] excluded: %r ($%.2f)", grade, title[:60], price)
                continue
            comps.append(PriceComp(price=price, title=title, url=url, date=date))
        if comps:
            logger.info("Apify [%s] %d comps, prices=%s",
                        grade, len(comps), sorted(c.price for c in comps))
        else:
            logger.warning("Apify [%s] 0 comps (raw=%d skipped=%s)", grade, len(items), skipped)
        return comps, ""

    def fetch_all_grades(self, player_name, year, set_name, card_number,
                         sport, raw_buy_price=0, include_autos=False,
                         variation="") -> CardPrices:
        grades, results, warnings = ["raw","psa8","psa9","psa10"], {}, []
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {
                ex.submit(self._fetch_grade, player_name, year, set_name,
                          card_number, g, include_autos, variation): g
                for g in grades
            }
            for fut in as_completed(futures, timeout=FETCH_TIMEOUT + 30):
                g = futures[fut]
                try:
                    comps, err = fut.result()
                except Exception as exc:
                    comps, err = [], str(exc)
                if err:
                    warnings.append(f"Apify [{g}]: {err}")
                results[g] = _make_result(comps, self.name, is_live=True,
                                          note=err or "")
        cp = CardPrices(raw=results.get("raw",   _make_result([], self.name, True)),
                        psa8=results.get("psa8", _make_result([], self.name, True)),
                        psa9=results.get("psa9", _make_result([], self.name, True)),
                        psa10=results.get("psa10",_make_result([], self.name, True)),
                        provider_name=self.name)
        cp.warnings.extend(warnings)
        return cp


# ── Provider 3: eBay free scraper ─────────────────────────────────────────────

class EbayScraperProvider(PricingProvider):
    """
    FREE provider: scrapes eBay completed/sold listings via requests + BeautifulSoup.
    No API key required. Results are cached for 60 min per (query, grade) pair.
    Runs grades SEQUENTIALLY (not parallel) to stay under eBay's bot threshold.
    """
    name     = "eBay (free)"
    BASE_URL = "https://www.ebay.com/sch/i.html"

    _UA_POOL = [
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        ("Mozilla/5.0 (X11; Linux x86_64) "
         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
         "Gecko/20100101 Firefox/121.0"),
    ]

    def is_available(self) -> bool:
        return _HAS_BS4

    def _headers(self) -> dict:
        return {
            "User-Agent":       random.choice(self._UA_POOL),
            "Accept":           ("text/html,application/xhtml+xml,application/xml;"
                                 "q=0.9,image/webp,*/*;q=0.8"),
            "Accept-Language":  "en-US,en;q=0.9",
            "Accept-Encoding":  "gzip, deflate, br",
            "Connection":       "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Referer":          "https://www.ebay.com/",
            "Cache-Control":    "no-cache",
            "Pragma":           "no-cache",
        }

    def _search_url(self, query: str) -> str:
        return (
            self.BASE_URL + "?" +
            urllib.parse.urlencode({"_nkw": query}) +
            "&LH_Sold=1&LH_Complete=1&_sop=13&_sacat=0"
        )

    def _scrape(self, query: str, grade: str) -> Tuple[List[PriceComp], str]:
        """
        Returns (comps, error_message).
        error_message is "" on success (even if 0 comps).
        """
        url = self._search_url(query)
        logger.info("eBay scrape [%s] GET %s", grade, url)

        try:
            resp = requests.get(url, headers=self._headers(), timeout=15,
                                allow_redirects=True)
        except requests.Timeout:
            return [], "Request timed out after 15s"
        except requests.ConnectionError as exc:
            return [], f"Connection error: {exc}"

        if resp.status_code == 403:
            return [], ("eBay blocked this server's IP (HTTP 403). "
                        "This is common on cloud hosts. "
                        "Configure APIFY_TOKEN or SPORTSCARDSPRO_API_KEY for reliable pricing.")
        if resp.status_code == 429:
            return [], "eBay rate limited (HTTP 429) — too many requests"
        if resp.status_code != 200:
            return [], f"eBay HTTP {resp.status_code}"

        html = resp.text
        if any(kw in html.lower() for kw in ("captcha", "are you a robot",
                                              "access denied", "security check")):
            return [], "eBay bot/CAPTCHA check triggered"

        soup  = _BS(html, "lxml")
        items = soup.select("li.s-item")
        if not items:
            items = soup.select("div.s-item__wrapper")
        logger.info("eBay scrape [%s] %d raw <li.s-item> elements", grade, len(items))

        if not items:
            # Try to detect "0 results" page
            no_res = soup.select_one("h1.srp-controls__count-heading")
            count_text = no_res.get_text(strip=True) if no_res else ""
            note = f"eBay returned 0 results (page count: {count_text!r})" if count_text else \
                   "eBay returned no items — possible bot block or 0 results"
            logger.warning("eBay scrape [%s] %s", grade, note)
            return [], ""   # not an error, just no data

        comps, skipped = [], {}
        for item in items:
            # ── title ──
            t_el = (item.select_one("span[role='heading']") or
                    item.select_one("div.s-item__title span") or
                    item.select_one("div.s-item__title") or
                    item.select_one("h3.s-item__title"))
            title = t_el.get_text(" ", strip=True) if t_el else ""
            if not title or "shop on ebay" in title.lower():
                continue

            # ── price ──
            p_el  = item.select_one("span.s-item__price")
            price = _parse_price_text(p_el.get_text(strip=True) if p_el else "")

            # ── URL ──
            a_el = item.select_one("a.s-item__link")
            link = a_el["href"].split("?")[0] if (a_el and a_el.get("href")) else ""

            # ── date ──
            d_el = (item.select_one("span.POSITIVE") or
                    item.select_one("span.s-item__caption--end") or
                    item.select_one(".s-item__caption"))
            date = _parse_date_text(d_el.get_text(" ", strip=True) if d_el else "")

            if price <= 0:
                skipped["no_price"] = skipped.get("no_price", 0) + 1
                continue
            if _is_excluded(title, grade, False):
                skipped["filtered"] = skipped.get("filtered", 0) + 1
                logger.debug("eBay [%s] excluded: %r ($%.2f)", grade, title[:60], price)
                continue
            comps.append(PriceComp(price=price, title=title, url=link, date=date))

        if comps:
            prices_list = sorted(c.price for c in comps)
            logger.info("eBay scrape [%s] kept %d comps %s → median≈$%.2f",
                        grade, len(comps), prices_list, statistics.median(prices_list))
        else:
            logger.warning("eBay scrape [%s] 0 comps kept. raw_items=%d skipped=%s",
                           grade, len(items), skipped)
            if skipped.get("filtered", 0) == len(items) - skipped.get("no_price", 0):
                return [], ""  # all results were filtered out — common (wrong-grade listings)

        return comps, ""

    def _fetch_grade(self, player_name, year, set_name, card_number,
                     grade, variation="") -> Tuple[List[PriceComp], str]:
        query     = _build_query(player_name, year, set_name, card_number, grade, variation)
        cache_key = f"ebay|{query}|{grade}"
        cached    = _CompsCache.get(cache_key)
        if cached is not None:
            logger.info("eBay [%s] cache hit: %d comps", grade, len(cached))
            return cached, ""
        comps, err = self._scrape(query, grade)
        if not err:   # only cache successful (even 0-result) fetches
            _CompsCache.set(cache_key, comps)
        return comps, err

    def fetch_all_grades(self, player_name, year, set_name, card_number,
                         sport, raw_buy_price=0, include_autos=False,
                         variation="") -> CardPrices:
        grades   = ["psa10", "psa9", "psa8", "raw"]   # most valuable first
        results  = {}
        warnings = []

        for i, g in enumerate(grades):
            if i > 0:
                time.sleep(random.uniform(0.8, 1.5))  # gentle rate limiting
            try:
                comps, err = self._fetch_grade(
                    player_name, year, set_name, card_number, g, variation
                )
            except Exception as exc:
                comps, err = [], str(exc)
                logger.error("eBay fetch_all_grades [%s] unexpected: %s", g, exc)
            if err:
                warnings.append(f"eBay [{g}]: {err}")
            results[g] = _make_result(comps, self.name, is_live=True,
                                      note=err if err else
                                           ("0 results from eBay search" if not comps else ""))

        cp = CardPrices(
            raw=results["raw"], psa8=results["psa8"],
            psa9=results["psa9"], psa10=results["psa10"],
            provider_name=self.name,
        )
        cp.warnings.extend(warnings)
        return cp


# ── Provider 4: 130point.com backup ──────────────────────────────────────────

class Point130Provider(PricingProvider):
    """
    FREE backup: scrapes 130point.com which aggregates eBay sold history.
    Used only when eBay scrape returns < MIN_LIVE_COMPS for a grade.
    """
    name     = "130point.com"
    BASE_URL = "https://www.130point.com/sales/"

    def is_available(self) -> bool:
        return _HAS_BS4

    def _search_url(self, query: str) -> str:
        return self.BASE_URL + "?" + urllib.parse.urlencode({"q": query})

    def _scrape(self, query: str, grade: str) -> Tuple[List[PriceComp], str]:
        url = self._search_url(query)
        logger.info("130pt [%s] GET %s", grade, url)
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": random.choice(EbayScraperProvider._UA_POOL),
                         "Accept": "text/html,*/*;q=0.9",
                         "Accept-Language": "en-US,en;q=0.9"},
                timeout=15,
            )
        except Exception as exc:
            return [], str(exc)

        if resp.status_code != 200:
            return [], f"HTTP {resp.status_code}"

        soup  = _BS(resp.text, "lxml")
        # 130point uses a DataTable; rows are in tbody > tr
        table = (soup.select_one("table#mytable") or
                 soup.select_one("table.dataTable") or
                 soup.select_one("table"))
        if not table:
            return [], "No data table found on 130point page"

        comps, skipped = [], {}
        for row in table.select("tbody tr"):
            cells = row.select("td")
            if len(cells) < 3:
                continue
            # 130point columns: Date | Title (link) | Price
            date_raw  = cells[0].get_text(strip=True)
            link_el   = cells[1].select_one("a")
            title     = link_el.get_text(strip=True) if link_el else cells[1].get_text(strip=True)
            url       = link_el.get("href", "") if link_el else ""
            price_raw = cells[-1].get_text(strip=True)

            price = _parse_price_text(price_raw)
            date  = date_raw[:10] if re.match(r'\d{4}-\d{2}-\d{2}', date_raw) \
                    else _parse_date_text(date_raw)

            if not title or price <= 0:
                skipped["no_data"] = skipped.get("no_data", 0) + 1
                continue
            if _is_excluded(title, grade, False):
                skipped["filtered"] = skipped.get("filtered", 0) + 1
                continue
            comps.append(PriceComp(price=price, title=title, url=url, date=date))

        logger.info("130pt [%s] %d comps (skipped=%s)", grade, len(comps), skipped)
        return comps, ""

    def _fetch_grade(self, player_name, year, set_name, card_number,
                     grade, variation="") -> Tuple[List[PriceComp], str]:
        query     = _build_query(player_name, year, set_name, card_number, grade, variation)
        cache_key = f"130pt|{query}|{grade}"
        cached    = _CompsCache.get(cache_key)
        if cached is not None:
            return cached, ""
        comps, err = self._scrape(query, grade)
        if not err:
            _CompsCache.set(cache_key, comps)
        return comps, err

    def fetch_all_grades(self, player_name, year, set_name, card_number,
                         sport, raw_buy_price=0, include_autos=False,
                         variation="") -> CardPrices:
        grades, results, warnings = ["psa10","psa9","psa8","raw"], {}, []
        for i, g in enumerate(grades):
            if i > 0:
                time.sleep(0.5)
            try:
                comps, err = self._fetch_grade(
                    player_name, year, set_name, card_number, g, variation
                )
            except Exception as exc:
                comps, err = [], str(exc)
            if err:
                warnings.append(f"130pt [{g}]: {err}")
            results[g] = _make_result(comps, self.name, is_live=True,
                                      note=err or "")
        cp = CardPrices(
            raw=results["raw"], psa8=results["psa8"],
            psa9=results["psa9"], psa10=results["psa10"],
            provider_name=self.name,
        )
        cp.warnings.extend(warnings)
        return cp


# ── Mock provider (kept for test isolation only, NOT in PROVIDER_CHAIN) ───────

class MockPricingProvider(PricingProvider):
    """Internal test helper — NEVER included in live PROVIDER_CHAIN."""
    name = "Estimated (mock)"
    _MULT = {
        "Football": (4.5, 2.0, 1.30), "Basketball": (5.0, 2.2, 1.35),
        "Baseball": (4.0, 1.8, 1.20), "Soccer":     (3.5, 1.7, 1.15),
        "Hockey":   (3.8, 1.8, 1.20),
    }
    _DEFAULT = (4.0, 1.9, 1.25)

    def is_available(self) -> bool: return True

    def _mock_grade(self, price, label):
        if price <= 0:
            return GradeResult(0, 0, 0, 0, 0, "—", "low", self.name, False)
        return GradeResult(
            median_price=price, avg_price=price, low_price=price, high_price=price,
            comp_count=0, date_range="—", confidence="low",
            source_name=self.name, is_live=False,
            comps=[PriceComp(price=price, title=f"[estimated] {label}")],
        )

    def fetch_all_grades(self, player_name, year, set_name, card_number,
                         sport, raw_buy_price=0, include_autos=False,
                         variation="") -> CardPrices:
        m10, m9, m8 = self._MULT.get(sport, self._DEFAULT)
        b = float(raw_buy_price or 0)
        return CardPrices(
            raw=self._mock_grade(b,                  "raw"),
            psa8=self._mock_grade(round(b * m8, 2),  "PSA 8"),
            psa9=self._mock_grade(round(b * m9, 2),  "PSA 9"),
            psa10=self._mock_grade(round(b * m10, 2),"PSA 10"),
            provider_name=self.name,
        )


# ── Orchestrator ──────────────────────────────────────────────────────────────

class PricingOrchestrator:
    """
    Tries each provider in order; falls back per-grade when comp_count < MIN_LIVE_COMPS.
    Accumulates warnings from every provider for surface-level error display.
    """

    def __init__(self, providers: List[PricingProvider]):
        self.providers = providers

    def _needs_fallback(self, cp: CardPrices) -> List[str]:
        return [g for g in ("raw","psa8","psa9","psa10")
                if getattr(cp, g).comp_count < MIN_LIVE_COMPS]

    def _patch(self, base: CardPrices, patch: CardPrices,
               grades: List[str]) -> CardPrices:
        d = {"raw": base.raw, "psa8": base.psa8, "psa9": base.psa9, "psa10": base.psa10}
        for g in grades:
            cand = getattr(patch, g)
            if cand.comp_count > getattr(base, g).comp_count:
                d[g] = cand
        return CardPrices(**d, provider_name=base.provider_name,
                          warnings=base.warnings[:])

    def fetch(
        self, player_name, year, set_name, card_number,
        sport, raw_buy_price=0, include_autos=False, variation="",
    ) -> CardPrices:
        kwargs = dict(
            player_name=player_name, year=year, set_name=set_name,
            card_number=card_number, sport=sport,
            raw_buy_price=raw_buy_price, include_autos=include_autos,
            variation=variation,
        )

        result:   Optional[CardPrices] = None
        all_warn: List[str]            = []

        for provider in self.providers:
            if not provider.is_available():
                continue
            try:
                candidate = provider.fetch_all_grades(**kwargs)
            except Exception as exc:
                msg = f"{provider.name} failed: {exc}"
                logger.error(msg)
                all_warn.append(msg)
                continue

            all_warn.extend(candidate.warnings)

            if result is None:
                result = candidate
            else:
                low    = self._needs_fallback(result)
                result = self._patch(result, candidate, low)

            if not self._needs_fallback(result):
                break

        if result is None:
            logger.info("No providers returned data; returning empty results")
            result = _empty_card_prices("No data source available")

        result.warnings = all_warn
        return result


# ── Active chain ──────────────────────────────────────────────────────────────
# Priority: SCP (paid, fast) → Apify (paid, fast) → eBay scrape (free) → 130pt (free)
# Set SPORTSCARDSPRO_API_KEY or APIFY_TOKEN to activate paid providers.
# Free scraping always runs when beautifulsoup4 is installed.

PROVIDER_CHAIN = PricingOrchestrator([
    SportsCardsProProvider(),
    ApifyEbayProvider(),
    EbayScraperProvider(),
    Point130Provider(),
])
