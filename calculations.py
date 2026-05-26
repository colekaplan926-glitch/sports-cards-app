"""
Shared profit calculation logic.
Imported by both app.py (calculator feature) and snipe.py (snipe finder).
"""


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
    _labels  = {
        "raw":   "Sell Raw",
        "psa8":  "Grade → PSA 8",
        "psa9":  "Grade → PSA 9",
        "psa10": "Grade → PSA 10",
    }
    best_roi = results[best_key]["roi"]

    if best_roi >= 50:
        calc_recommendation = "Strong Buy"
    elif best_roi >= 20:
        calc_recommendation = "Possible Buy"
    else:
        calc_recommendation = "Avoid"

    return {
        "results": results,
        "best": {
            "key":    best_key,
            "label":  _labels[best_key],
            "roi":    best_roi,
            "profit": results[best_key]["profit"],
        },
        "recommendation": calc_recommendation,
        "costs": {
            "raw_price":   raw_price,
            "grading":     grading_cost,
            "shipping":    shipping,
            "fee_pct":     selling_fee_pct,
            "graded_cost": graded_cost,
        },
    }
