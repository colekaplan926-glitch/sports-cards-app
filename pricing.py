"""
Pricing engine for sports card market value estimation.

To connect a real data source, subclass PricingEngine, implement estimate(),
and set ACTIVE_ENGINE at the bottom of this file.

Example swap-in targets:
  - eBay Finding API  (searchCompletedItems, keywords="2017 Mahomes Prizm PSA 10")
  - 130point.com API  (card population + sale price data)
  - CardLadder API    (historical grade value charts)
  - PSA Population Report scraper (pop adjusts multipliers)
"""


class PricingEngine:
    """Interface all pricing engines must implement."""

    def estimate(
        self,
        player_name: str,
        year: str,
        set_name: str,
        card_number: str,
        sport: str,
        raw_buy_price: float,
    ) -> dict:
        """
        Return estimated market values for a card.

        Returns a dict with keys:
            raw_market  – estimated current raw sale price
            psa8        – estimated PSA 8 sale price
            psa9        – estimated PSA 9 sale price
            psa10       – estimated PSA 10 sale price
            source      – human-readable description of the data source
            confidence  – "low" | "medium" | "high"
        """
        raise NotImplementedError


class MockPricingEngine(PricingEngine):
    """
    Estimates PSA values using sport-specific multipliers over raw buy price.

    Multipliers are derived from typical PSA population / market trends.
    Raw market is assumed equal to raw purchase price (fair-market buy assumed).

    Confidence is always "low" — these are ballpark figures only.
    Replace with a real engine for live comps.
    """

    # (psa10_multiplier, psa9_multiplier, psa8_multiplier)
    _MULTIPLIERS = {
        "Football":   (4.5, 2.0, 1.30),
        "Basketball": (5.0, 2.2, 1.35),
        "Baseball":   (4.0, 1.8, 1.20),
        "Soccer":     (3.5, 1.7, 1.15),
        "Hockey":     (3.8, 1.8, 1.20),
        "Golf":       (3.5, 1.7, 1.15),
        "Tennis":     (3.5, 1.7, 1.15),
    }
    _DEFAULT = (4.0, 1.9, 1.25)

    def estimate(self, player_name, year, set_name, card_number, sport, raw_buy_price):
        m10, m9, m8 = self._MULTIPLIERS.get(sport, self._DEFAULT)
        base = max(float(raw_buy_price or 0), 0.0)
        return {
            "raw_market": round(base, 2),
            "psa8":       round(base * m8,  2),
            "psa9":       round(base * m9,  2),
            "psa10":      round(base * m10, 2),
            "source":     "Estimated — typical market multipliers (not live data)",
            "confidence": "low",
        }


# ── Swap this line to activate a real pricing engine ──────────────────────────
ACTIVE_ENGINE: PricingEngine = MockPricingEngine()
