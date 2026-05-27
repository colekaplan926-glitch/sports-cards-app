"""Tests for the bulk sold-listing text parser (_parse_bulk_text in app.py)."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import _parse_bulk_text


class TestHerbertCardDetailsOnly:
    """Card-detail lines with no $ prices must produce zero comps."""

    def test_card_details_block_returns_no_comps(self):
        text = (
            "Player: Justin Herbert\n"
            "Year: 2020\n"
            "Set: Prizm\n"
            "Card #: 325\n"
            "Sport: Football"
        )
        r = _parse_bulk_text(text)
        assert r["raw"]   == [], f"raw should be empty, got {r['raw']}"
        assert r["psa8"]  == [], f"psa8 should be empty, got {r['psa8']}"
        assert r["psa9"]  == [], f"psa9 should be empty, got {r['psa9']}"
        assert r["psa10"] == [], f"psa10 should be empty, got {r['psa10']}"

    def test_year_not_treated_as_price(self):
        text = "2020 Justin Herbert Prizm PSA 9 Sold $430"
        r = _parse_bulk_text(text)
        assert 2020.0 not in r["raw"], "2020 (year) must not be parsed as a price"
        assert r["psa9"] == [430.0]

    def test_card_number_not_treated_as_price(self):
        text = "2020 Justin Herbert Prizm #325 PSA 10 Sold $1,245"
        r = _parse_bulk_text(text)
        assert 325.0 not in r["raw"],  "#325 must not be parsed as a price"
        assert r["psa10"] == [1245.0]

    def test_serial_number_not_treated_as_price(self):
        text = "2020 Herbert Prizm /99 PSA 10 $450"
        r = _parse_bulk_text(text)
        assert 99.0 not in r["raw"],  "/99 (serial) must not be parsed as a price"
        assert r["psa10"] == [450.0]

    def test_psa_grade_number_not_treated_as_price(self):
        """The '10' in 'PSA 10' must never become a price."""
        text = "2020 Herbert Prizm PSA 10 Sold $1,199"
        r = _parse_bulk_text(text)
        assert 10.0 not in r["raw"]
        assert r["psa10"] == [1199.0]


class TestHerbertComps:
    """Full Herbert comp block should parse correctly."""

    def test_herbert_comp_block(self):
        text = (
            "2020 Panini Prizm Justin Herbert #325 PSA 10 Sold $1,245\n"
            "2020 Panini Prizm Justin Herbert #325 PSA 10 Sold $1,199\n"
            "2020 Panini Prizm Justin Herbert #325 PSA 9 Sold $430\n"
            "2020 Panini Prizm Justin Herbert #325 Raw Sold $185"
        )
        r = _parse_bulk_text(text)
        assert r["raw"]  == [185.0],            f"raw: {r['raw']}"
        assert r["psa9"] == [430.0],            f"psa9: {r['psa9']}"
        assert sorted(r["psa10"]) == [1199.0, 1245.0], f"psa10: {r['psa10']}"
        assert r["psa8"] == [],                 f"psa8 should be empty"

    def test_herbert_median_psa10(self):
        import statistics
        text = (
            "2020 Panini Prizm Justin Herbert #325 PSA 10 Sold $1,245\n"
            "2020 Panini Prizm Justin Herbert #325 PSA 10 Sold $1,199"
        )
        r = _parse_bulk_text(text)
        assert sorted(r["psa10"]) == [1199.0, 1245.0]
        assert statistics.median(r["psa10"]) == pytest_approx(1222.0)


class TestMahomesComps:
    """Original Mahomes example must still work."""

    def test_mahomes_comp_block(self):
        text = (
            "2017 Patrick Mahomes Donruss #327 PSA 10 Sold $1245\n"
            "2017 Patrick Mahomes Donruss #327 PSA 10 Sold $1210\n"
            "2017 Patrick Mahomes Donruss #327 PSA 9 Sold $430\n"
            "2017 Patrick Mahomes Donruss #327 Raw Sold $180"
        )
        r = _parse_bulk_text(text)
        assert r["raw"]  == [180.0]
        assert r["psa9"] == [430.0]
        assert sorted(r["psa10"]) == [1210.0, 1245.0]
        assert r["psa8"] == []

    def test_year_2017_not_a_price(self):
        text = "2017 Patrick Mahomes Donruss PSA 10 Sold $1245"
        r = _parse_bulk_text(text)
        assert 2017.0 not in r["raw"]
        assert r["psa10"] == [1245.0]


class TestFilteringRules:
    """Exclusion rules must hold for any card."""

    def test_lot_excluded(self):
        r = _parse_bulk_text("Lot of 10 cards PSA 10 $500")
        assert r["psa10"] == []

    def test_non_psa_grader_excluded(self):
        r = _parse_bulk_text("BGS 9.5 Herbert Prizm $999")
        for prices in r.values():
            assert prices == [], f"BGS card should be excluded"

    def test_sgc_excluded(self):
        r = _parse_bulk_text("SGC 10 Mahomes Prizm $800")
        for prices in r.values():
            assert prices == []

    def test_reprint_excluded(self):
        r = _parse_bulk_text("Reprint 2020 Herbert Prizm PSA 10 $50")
        for prices in r.values():
            assert prices == []

    def test_auto_excluded_when_not_auto_card(self):
        r = _parse_bulk_text("2020 Herbert Prizm Auto PSA 10 $500")
        for prices in r.values():
            assert prices == []

    def test_auto_included_when_auto_card(self):
        ctx = {"variation": "Auto"}
        r = _parse_bulk_text("2020 Herbert Prizm Auto PSA 10 $500", ctx)
        assert r["psa10"] == [500.0]

    def test_psa_gem_mint_10_detected(self):
        r = _parse_bulk_text("2020 Herbert Prizm PSA Gem Mint 10 $1,300")
        assert r["psa10"] == [1300.0]

    def test_us_dollar_prefix(self):
        r = _parse_bulk_text("2020 Herbert PSA 9 US $430")
        assert r["psa9"] == [430.0]

    def test_comma_formatted_price(self):
        r = _parse_bulk_text("2020 Herbert Prizm PSA 10 Sold $1,245.00")
        assert r["psa10"] == [1245.0]

    def test_sale_language_bare_number(self):
        """'Sold for 430' (no $) should be accepted."""
        r = _parse_bulk_text("2020 Herbert PSA 9 Sold for 430")
        assert r["psa9"] == [430.0]

    def test_price_colon_bare_number(self):
        """'Price: 1250' should be accepted."""
        r = _parse_bulk_text("2020 Herbert PSA 10 Price: 1250")
        assert r["psa10"] == [1250.0]

    def test_empty_text_returns_no_comps(self):
        r = _parse_bulk_text("")
        for prices in r.values():
            assert prices == []

    def test_blank_lines_ignored(self):
        text = "\n\n\n2020 Herbert PSA 9 $430\n\n"
        r = _parse_bulk_text(text)
        assert r["psa9"] == [430.0]


class TestLookbackWindow:
    """eBay multi-line format: grade on title, price on the next line."""

    def test_multiline_ebay_grade_then_price(self):
        text = "2017 Patrick Mahomes Donruss #327 PSA 9\n$430.50"
        r = _parse_bulk_text(text)
        assert r["psa9"] == [430.50], f"psa9: {r['psa9']}"
        assert r["raw"]  == [],       f"raw should be empty: {r['raw']}"

    def test_bare_price_borrows_grade_within_two_lines(self):
        """Grade → trivial line → bare price (2 lines away) → should still borrow."""
        text = "2017 Mahomes Donruss PSA 10\nSold\n$1245"
        r = _parse_bulk_text(text)
        assert r["psa10"] == [1245.0], f"psa10: {r['psa10']}"
        assert r["raw"]   == [],       f"raw should be empty: {r['raw']}"

    def test_grade_context_reset_on_substantive_title(self):
        """After a PSA 9 comp, a non-trivial listing title resets context → $180 goes to raw."""
        text = (
            "2017 Mahomes Donruss PSA 9 Sold $430\n"
            "2017 Mahomes Donruss Raw Card Good condition\n"
            "$180"
        )
        r = _parse_bulk_text(text)
        assert r["psa9"] == [430.0], f"psa9: {r['psa9']}"
        assert r["raw"]  == [180.0], f"raw: {r['raw']}"

    def test_trivial_lines_dont_reset_context(self):
        """Dates and status words between grade line and price line don't break lookback."""
        text = "2017 Mahomes PSA 10\nNov 2024\n$1245"
        r = _parse_bulk_text(text)
        assert r["psa10"] == [1245.0], f"psa10: {r['psa10']}"
        assert r["raw"]   == [],       f"raw should be empty: {r['raw']}"

    def test_lookback_does_not_reach_beyond_two_lines(self):
        """3+ lines between grade and price → context lost → goes to raw."""
        text = "2017 Mahomes PSA 10\nSold\nOct 2024\n$1245"
        r = _parse_bulk_text(text)
        # 3 lines away — context should be reset, price goes raw
        assert r["psa10"] == [], f"psa10 should be empty: {r['psa10']}"
        assert r["raw"]   == [1245.0], f"raw: {r['raw']}"

    def test_multiline_psa9_then_psa10(self):
        """Two consecutive eBay-style multi-line entries, grades stay separate."""
        text = (
            "Mahomes #327 PSA 9\n"
            "$430\n"
            "Mahomes #327 PSA 10\n"
            "$1245"
        )
        r = _parse_bulk_text(text)
        assert r["psa9"]  == [430.0],  f"psa9: {r['psa9']}"
        assert r["psa10"] == [1245.0], f"psa10: {r['psa10']}"


def pytest_approx(val, rel=1e-3):
    """Tiny helper so we don't need to import pytest at module level."""
    import pytest
    return pytest.approx(val, rel=rel)
