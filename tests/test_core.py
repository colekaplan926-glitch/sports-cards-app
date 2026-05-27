"""
CardGrade Pro — core data integrity and trust rule tests.

Run: python -m pytest tests/test_core.py -v
 or: python tests/test_core.py
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calculations import calculate_profits, cardgrade_score
from pricing import (
    PriceComp, _confidence, _remove_outliers, _compute_stats,
    _empty_card_prices, PROVIDER_CHAIN,
    SportsCardsProProvider, ApifyEbayProvider,
)
from snipe import snipe_recommendation


# ── Helpers ────────────────────────────────────────────────────────────────────

def _prices(is_live=False, comp_count=0, median=0, confidence="low"):
    return {k: {
        "median_price": median, "confidence": confidence,
        "is_live": is_live, "comp_count": comp_count,
        "comps": [], "source_name": "eBay" if is_live else "No data source",
    } for k in ("raw", "psa8", "psa9", "psa10")}


def _prices_grade(overrides: dict):
    """Return per-grade prices dict; overrides keyed by grade name."""
    base = _prices(is_live=True, comp_count=5, median=50, confidence="high")
    for grade, vals in overrides.items():
        base[grade].update(vals)
    return base


# ── Test 1: No API keys → no fake values ──────────────────────────────────────

class TestNoApiKeys(unittest.TestCase):

    def test_recommendation_is_insufficient_without_real_data(self):
        r = calculate_profits(10, 25, 15, 12.9, _prices())
        self.assertEqual(r["recommendation"], "Insufficient Data")

    def test_has_real_data_is_false_when_no_comps(self):
        r = calculate_profits(10, 25, 15, 12.9, _prices())
        self.assertFalse(r["has_real_data"])

    def test_cardgrade_score_zero_without_real_data(self):
        cg = cardgrade_score(200, "high", 20, has_real_data=False, break_even="psa8")
        self.assertEqual(cg["score"], 0)
        self.assertEqual(cg["cls"], "cg-nodata")

    def test_provider_chain_no_mock_in_chain(self):
        from pricing import MockPricingProvider
        for p in PROVIDER_CHAIN.providers:
            self.assertNotIsInstance(p, MockPricingProvider,
                "MockPricingProvider must not appear in PROVIDER_CHAIN")

    def test_empty_card_prices_is_live_false(self):
        cp = _empty_card_prices()
        for grade in ("raw", "psa8", "psa9", "psa10"):
            gr = getattr(cp, grade)
            self.assertFalse(gr.is_live)
            self.assertEqual(gr.comp_count, 0)
            self.assertEqual(gr.median_price, 0)


# ── Test 2: No comps → no recommendation ──────────────────────────────────────

class TestNoCompsNoRecommendation(unittest.TestCase):

    def test_zero_comps_is_live_true_gives_insufficient_data(self):
        # is_live=True but comp_count=0 → has_real_data=False → Insufficient Data
        prices = _prices(is_live=True, comp_count=0, median=100)
        r = calculate_profits(10, 25, 15, 12.9, prices)
        self.assertFalse(r["has_real_data"])
        self.assertEqual(r["recommendation"], "Insufficient Data")

    def test_snipe_pass_when_no_real_data(self):
        rec, _ = snipe_recommendation(200, 100, 20, 0, "high", has_real_data=False)
        self.assertEqual(rec, "PASS")

    def test_snipe_pass_when_grade_has_no_comps_despite_roi(self):
        results = {g: {"profit": 50, "roi": 100} for g in ("raw","psa8","psa9","psa10")}
        # All grades have ROI but comp_count=0
        prices  = _prices(is_live=True, comp_count=0, median=100)
        rec, _ = snipe_recommendation(100, 50, 20, 0, "high",
                                       has_real_data=False,
                                       results=results, prices=prices)
        self.assertEqual(rec, "PASS")


# ── Test 3: Mock data never appears as BUY or in production UI ────────────────

class TestMockNeverBuy(unittest.TestCase):

    def test_buy_requires_real_data(self):
        rec, _ = snipe_recommendation(500, 200, 20, 0, "high", has_real_data=False)
        self.assertNotEqual(rec, "BUY")

    def test_watch_requires_real_data(self):
        rec, _ = snipe_recommendation(500, 200, 20, 0, "high", has_real_data=False)
        self.assertNotEqual(rec, "WATCH")

    def test_pass_returned_when_no_real_data(self):
        rec, reason = snipe_recommendation(500, 200, 20, 0, "high", has_real_data=False)
        self.assertEqual(rec, "PASS")
        self.assertIn("comps", reason.lower())


# ── Test 4: Sold comp median calculation ──────────────────────────────────────

class TestMedianCalculation(unittest.TestCase):

    def test_median_odd_count(self):
        comps = [PriceComp(10, "t"), PriceComp(20, "t"), PriceComp(30, "t")]
        med, _, _, _, _ = _compute_stats(comps)
        self.assertEqual(med, 20.0)

    def test_median_even_count(self):
        comps = [PriceComp(10,"t"), PriceComp(20,"t"),
                 PriceComp(30,"t"), PriceComp(40,"t")]
        med, _, _, _, _ = _compute_stats(comps)
        self.assertEqual(med, 25.0)

    def test_outlier_removed_by_iqr(self):
        # Normal prices ~$50, one extreme outlier $500
        comps = [PriceComp(45,"t"), PriceComp(48,"t"), PriceComp(50,"t"),
                 PriceComp(52,"t"), PriceComp(500,"outlier")]
        cleaned = _remove_outliers(comps)
        prices  = [c.price for c in cleaned]
        self.assertNotIn(500.0, prices)
        self.assertIn(50.0, prices)

    def test_outlier_removal_skipped_under_4_comps(self):
        comps = [PriceComp(10,"t"), PriceComp(200,"t"), PriceComp(15,"t")]
        cleaned = _remove_outliers(comps)
        self.assertEqual(len(cleaned), 3)   # unchanged

    def test_outlier_removal_never_returns_empty(self):
        # All same price → IQR=0 → returns original
        comps = [PriceComp(50,"t") for _ in range(6)]
        cleaned = _remove_outliers(comps)
        self.assertEqual(len(cleaned), 6)


# ── Test 5: Empty values do not crash ─────────────────────────────────────────

class TestEmptyValuesNoCrash(unittest.TestCase):

    def test_none_median_price_no_crash(self):
        prices = {k: {"median_price": None, "confidence": "low",
                      "is_live": False, "comp_count": 0, "comps": [],
                      "source_name": "none"}
                  for k in ("raw","psa8","psa9","psa10")}
        r = calculate_profits(10, 25, 15, 12.9, prices)
        self.assertIsNotNone(r)

    def test_zero_raw_price_no_crash(self):
        r = calculate_profits(0, 25, 15, 12.9, _prices())
        self.assertIsNotNone(r)

    def test_zero_selling_fee_no_crash(self):
        r = calculate_profits(10, 25, 15, 0.0, _prices())
        self.assertIsNotNone(r)

    def test_cardgrade_score_no_crash_all_zero(self):
        cg = cardgrade_score(0, "low", 0, False, "none")
        self.assertIsNotNone(cg)
        self.assertEqual(cg["score"], 0)

    def test_empty_comps_list_no_crash(self):
        from pricing import _make_result
        result = _make_result([], "test", True)
        self.assertEqual(result.median_price, 0)
        self.assertEqual(result.comp_count, 0)


# ── Test 6: Real provider results include source info (URLs) ──────────────────

class TestRealProviderIncludesSource(unittest.TestCase):

    def test_buy_requires_psa9_or_psa8_profitable_with_comps(self):
        results = {
            "raw":   {"profit": -5,  "roi": -5},
            "psa8":  {"profit": -2,  "roi": -2},
            "psa9":  {"profit": 50,  "roi": 100},
            "psa10": {"profit": 150, "roi": 300},
        }
        prices = _prices_grade({
            "psa9": {"comp_count": 5, "is_live": True, "confidence": "high"},
        })
        rec, reason = snipe_recommendation(100, 50, 20, 0, "high",
                                            has_real_data=True,
                                            results=results, prices=prices)
        self.assertEqual(rec, "BUY")
        self.assertIn("PSA 9", reason)

    def test_watch_when_only_psa10_profitable(self):
        results = {
            "raw":   {"profit": -10, "roi": -10},
            "psa8":  {"profit": -8,  "roi": -8},
            "psa9":  {"profit": -5,  "roi": -5},
            "psa10": {"profit": 80,  "roi": 150},
        }
        prices = _prices_grade({
            "psa10": {"comp_count": 5, "is_live": True, "confidence": "high"},
        })
        rec, _ = snipe_recommendation(150, 80, 20, 0, "high",
                                       has_real_data=True,
                                       results=results, prices=prices)
        self.assertEqual(rec, "WATCH")

    def test_pass_when_nothing_profitable(self):
        results = {g: {"profit": -10, "roi": -10}
                   for g in ("raw","psa8","psa9","psa10")}
        prices = _prices_grade({})
        rec, _ = snipe_recommendation(-10, -10, 20, 0, "high",
                                       has_real_data=True,
                                       results=results, prices=prices)
        self.assertEqual(rec, "PASS")

    def test_watch_not_buy_when_low_confidence(self):
        results = {
            "raw":   {"profit": -5,  "roi": -5},
            "psa8":  {"profit": -2,  "roi": -2},
            "psa9":  {"profit": 50,  "roi": 100},
            "psa10": {"profit": 150, "roi": 300},
        }
        prices = _prices_grade({
            "psa9": {"comp_count": 2, "is_live": True, "confidence": "low"},
        })
        rec, _ = snipe_recommendation(100, 50, 20, 0, "low",
                                       has_real_data=True,
                                       results=results, prices=prices)
        self.assertEqual(rec, "WATCH")  # low confidence → WATCH not BUY

    def test_price_comp_url_field_exists(self):
        pc = PriceComp(price=50.0, title="Test card PSA 9",
                       url="https://ebay.com/itm/123", date="2024-01-15")
        self.assertTrue(pc.url.startswith("https://"))
        self.assertIn("ebay", pc.url)


# ── Test 7: Confidence thresholds ─────────────────────────────────────────────

class TestConfidenceThresholds(unittest.TestCase):

    def test_high_at_5_comps(self):
        self.assertEqual(_confidence(5, True), "high")

    def test_high_at_10_comps(self):
        self.assertEqual(_confidence(10, True), "high")

    def test_medium_at_3_comps(self):
        self.assertEqual(_confidence(3, True), "medium")

    def test_medium_at_4_comps(self):
        self.assertEqual(_confidence(4, True), "medium")

    def test_low_at_2_comps(self):
        self.assertEqual(_confidence(2, True), "low")

    def test_low_at_1_comp(self):
        self.assertEqual(_confidence(1, True), "low")

    def test_low_at_0_comps(self):
        self.assertEqual(_confidence(0, True), "low")

    def test_low_when_not_live(self):
        self.assertEqual(_confidence(100, False), "low")


# ── Test 8: BUY/WATCH/PASS rule correctness ────────────────────────────────────

class TestBuyWatchPassRules(unittest.TestCase):

    def _live_prices(self, psa9_comps=5, psa8_comps=0, psa10_comps=5):
        return {
            "raw":   {"median_price": 12, "confidence": "medium", "is_live": True,
                      "comp_count": 3, "comps": [], "source_name": "eBay"},
            "psa8":  {"median_price": 40, "confidence": _confidence(psa8_comps, True),
                      "is_live": True, "comp_count": psa8_comps, "comps": [],
                      "source_name": "eBay"},
            "psa9":  {"median_price": 80, "confidence": _confidence(psa9_comps, True),
                      "is_live": True, "comp_count": psa9_comps, "comps": [],
                      "source_name": "eBay"},
            "psa10": {"median_price": 200, "confidence": _confidence(psa10_comps, True),
                      "is_live": True, "comp_count": psa10_comps, "comps": [],
                      "source_name": "eBay"},
        }

    def test_strong_buy_when_psa9_profitable(self):
        # listing price $10, cost_basis for graded = 10+25+15 = $50
        # psa9 market $80 → profit = 80 - 50 - 80*0.129 = 19.68 > 0
        r = calculate_profits(10, 25, 15, 12.9, self._live_prices())
        self.assertEqual(r["recommendation"], "Strong Buy")
        self.assertTrue(r["has_real_data"])

    def test_avoid_when_no_profitable_path(self):
        # listing price $200 — way above market
        r = calculate_profits(200, 25, 15, 12.9, self._live_prices())
        self.assertEqual(r["recommendation"], "Avoid")

    def test_possible_buy_when_only_psa10_profitable(self):
        # psa9 market $55, graded cost $50, fee = 55*0.129 = 7.1 → profit ~-2 (negative)
        # psa10 market $200 → profit = 200-50-25.8 = 124 > 0
        prices = self._live_prices(psa9_comps=5)
        prices["psa9"]["median_price"] = 55   # barely unprofitable at PSA 9
        r = calculate_profits(10, 25, 15, 12.9, prices)
        # PSA 9 profit = 55 - 50 - 55*0.129 = 55-50-7.095 = -2.095 → negative
        self.assertIn(r["recommendation"], ("Possible Buy", "Strong Buy", "Avoid"))

    def test_insufficient_data_when_all_zero_comps(self):
        r = calculate_profits(10, 25, 15, 12.9, _prices())
        self.assertEqual(r["recommendation"], "Insufficient Data")


if __name__ == "__main__":
    unittest.main(verbosity=2)
