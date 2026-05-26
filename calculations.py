"""
Shared profit calculation logic.
Imported by both app.py (calculator feature) and snipe.py (snipe finder).
"""

_GRADE_LABELS = {
    "psa8":  "Grade → PSA 8",
    "psa9":  "Grade → PSA 9",
    "psa10": "Grade → PSA 10",
    "raw":   "Sell Raw",
}

_BE_LABELS = {
    "psa8":  "PSA 8",
    "psa9":  "PSA 9",
    "psa10": "PSA 10",
}


def _break_even(results: dict) -> str:
    """Return the lowest PSA grade where grading profit >= 0, or 'none'."""
    for g in ("psa8", "psa9", "psa10"):
        if results[g]["profit"] >= 0:
            return g
    return "none"


def _has_real_data(prices: dict) -> bool:
    return any(
        prices[k].get("is_live") and prices[k].get("comp_count", 0) > 0
        for k in ("raw", "psa8", "psa9", "psa10")
    )


def calculate_profits(
    raw_price: float,
    grading_cost: float,
    shipping: float,
    selling_fee_pct: float,
    prices: dict,
) -> dict:
    """
    Two separated cost bases:

    Sell raw now:
        Cost basis  = raw_price
        Profit      = Raw Market − raw_price − Selling Fees

    Grade then sell:
        Cost basis  = raw_price + grading_cost + shipping
        Profit      = PSA Value − Cost basis − Selling Fees
    """
    fee = selling_fee_pct / 100

    raw_market = prices["raw"]["median_price"]
    psa8_val   = prices["psa8"]["median_price"]
    psa9_val   = prices["psa9"]["median_price"]
    psa10_val  = prices["psa10"]["median_price"]

    # ── Sell raw ──────────────────────────────────────────────────────────────
    raw_sell_fee = round(raw_market * fee, 2)
    raw_profit   = round(raw_market - raw_price - raw_sell_fee, 2)
    raw_roi      = round((raw_profit / raw_price * 100) if raw_price > 0 else 0, 2)

    # ── Grade then sell ───────────────────────────────────────────────────────
    graded_cost = round(raw_price + grading_cost + shipping, 2)

    def _graded(sale_price: float) -> dict:
        sell_fee = round(sale_price * fee, 2)
        profit   = round(sale_price - graded_cost - sell_fee, 2)
        roi      = round((profit / graded_cost * 100) if graded_cost > 0 else 0, 2)
        return {
            "sale_price": round(sale_price, 2),
            "cost_basis": graded_cost,
            "sell_fee":   sell_fee,
            "profit":     profit,
            "roi":        roi,
        }

    results = {
        "raw":   {
            "sale_price": round(raw_market, 2),
            "cost_basis": round(raw_price, 2),
            "sell_fee":   raw_sell_fee,
            "profit":     raw_profit,
            "roi":        raw_roi,
        },
        "psa8":  _graded(psa8_val),
        "psa9":  _graded(psa9_val),
        "psa10": _graded(psa10_val),
    }

    best_key = max(results, key=lambda k: results[k]["roi"])
    best_roi = results[best_key]["roi"]

    # ── Break-even grade ──────────────────────────────────────────────────────
    be_grade = _break_even(results)
    be_label = _BE_LABELS.get(be_grade)   # None when "none"

    # ── Data quality gate ─────────────────────────────────────────────────────
    real_data = _has_real_data(prices)

    # ── Recommendation + explanation ──────────────────────────────────────────
    if not real_data:
        recommendation = "Insufficient Data"
        why = (
            "No real sold comps were found for this card. "
            "Connect APIFY_TOKEN or SPORTSCARDSPRO_API_KEY to get live eBay "
            "pricing before making any investment decision."
        )
    elif best_roi >= 50:
        recommendation = "Strong Buy"
        why = (
            f"{_GRADE_LABELS[best_key]} yields {best_roi:.1f}% ROI "
            f"(${results[best_key]['profit']:.2f} profit) based on live sold comps."
        )
        if be_label:
            why += f" You break even at {be_label}."
        else:
            why += " No grading path breaks even — raw sale is the best exit."
    elif best_roi >= 20:
        recommendation = "Possible Buy"
        why = (
            f"{_GRADE_LABELS[best_key]} yields {best_roi:.1f}% ROI — "
            f"solid but not exceptional."
        )
        if be_label:
            why += f" Break-even grade: {be_label}."
    else:
        recommendation = "Avoid"
        if be_label is None:
            why = (
                "No grading strategy produces a profit at current market prices. "
                "The card would need to appreciate significantly before this makes sense."
            )
        else:
            why = (
                f"Thin margins. You must receive at least a {be_label} to break even. "
                f"The risk/reward doesn't justify the investment at this price."
            )

    return {
        "results": results,
        "best": {
            "key":    best_key,
            "label":  _GRADE_LABELS[best_key],
            "roi":    best_roi,
            "profit": results[best_key]["profit"],
        },
        "recommendation":   recommendation,
        "why":              why,
        "has_real_data":    real_data,
        "break_even_grade": be_grade,
        "break_even_label": be_label,
        "costs": {
            "raw_price":   raw_price,
            "grading":     grading_cost,
            "shipping":    shipping,
            "fee_pct":     selling_fee_pct,
            "graded_cost": graded_cost,
        },
    }
