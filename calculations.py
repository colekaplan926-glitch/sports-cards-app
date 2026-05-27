"""Shared profit calculation logic — imported by app.py and snipe.py."""

_GRADE_LABELS = {
    "psa8":  "Grade → PSA 8",
    "psa9":  "Grade → PSA 9",
    "psa10": "Grade → PSA 10",
    "raw":   "Sell Raw",
}
_BE_LABELS = {"psa8": "PSA 8", "psa9": "PSA 9", "psa10": "PSA 10"}

# Set-specific grading risk profiles
_SET_RISK = {
    "prizm":       ("Centering risk",        "yellow"),
    "optic":       ("Surface risk",          "yellow"),
    "chrome":      ("Scratch risk",          "yellow"),
    "donruss":     ("Easier gem rate",       "green"),
    "select":      ("Lower centering risk",  "green"),
    "mosaic":      ("Print lines risk",      "yellow"),
    "topps":       ("Centering risk",        "yellow"),
    "bowman":      ("Surface/moisture risk", "yellow"),
    "contenders":  ("Centering risk",        "yellow"),
    "upper deck":  ("Wear/surface risk",     "yellow"),
    "hoops":       ("Easier gem rate",       "green"),
}


def set_grading_risk(set_name: str) -> dict:
    s = (set_name or "").lower()
    for key, (label, color) in _SET_RISK.items():
        if key in s:
            return {"label": label, "color": color}
    return {"label": "Standard grading risk", "color": "green"}


def cardgrade_score(
    best_roi: float,
    confidence: str,
    comp_count: int,
    has_real_data: bool,
    break_even: str,
) -> dict:
    """
    CardGrade Score 0–100.
    Weights: ROI 40 | Confidence 25 | Break-even 25 | Liquidity 10
    """
    if not has_real_data:
        return {"score": 0, "label": "No Data", "cls": "cg-nodata"}

    roi_pts  = min(40, max(0, int(best_roi * 0.4)))
    conf_pts = {"high": 25, "medium": 15, "low": 5}.get(confidence, 0)
    be_pts   = {"psa8": 25, "psa9": 18, "psa10": 8, "none": 0}.get(break_even, 0)
    liq_pts  = 10 if comp_count >= 8 else (5 if comp_count >= 3 else 2 if comp_count > 0 else 0)
    score    = min(100, roi_pts + conf_pts + be_pts + liq_pts)

    if score >= 85: return {"score": score, "label": "Elite Flip",  "cls": "cg-elite"}
    if score >= 70: return {"score": score, "label": "Solid Buy",   "cls": "cg-solid"}
    if score >= 55: return {"score": score, "label": "Possible",    "cls": "cg-possible"}
    if score >= 40: return {"score": score, "label": "Risky",       "cls": "cg-risky"}
    return          {"score": score, "label": "Avoid",         "cls": "cg-avoid"}


def _short_signal(recommendation: str, best_roi: float,
                  confidence: str, break_even: str) -> str:
    if recommendation == "Insufficient Data":
        return "Connect APIFY_TOKEN or SPORTSCARDSPRO_API_KEY to get a real signal."
    if recommendation == "Strong Buy":
        if confidence == "high":
            return f"High-confidence — {best_roi:.0f}% ROI at PSA 9 or better."
        return f"{best_roi:.0f}% ROI at PSA 9 or better — verify comp count."
    if recommendation == "Possible Buy":
        return "Profitable only at PSA 10 — high-risk gem-grade submission."
    if recommendation == "Avoid":
        return "No verified profitable grading path at current prices."
    return ""


def _break_even(results: dict) -> str:
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
    set_name: str = "",
) -> dict:
    fee         = (selling_fee_pct or 0) / 100
    raw_price   = raw_price or 0
    raw_market  = float(prices["raw"]["median_price"]  or 0)
    psa8_val    = float(prices["psa8"]["median_price"] or 0)
    psa9_val    = float(prices["psa9"]["median_price"] or 0)
    psa10_val   = float(prices["psa10"]["median_price"] or 0)

    raw_sell_fee = round(raw_market * fee, 2)
    raw_profit   = round(raw_market - raw_price - raw_sell_fee, 2)
    raw_roi      = round((raw_profit / raw_price * 100) if raw_price > 0 else 0, 2)
    graded_cost  = round(raw_price + grading_cost + shipping, 2)

    def _graded(sp: float) -> dict:
        sf = round(sp * fee, 2)
        pr = round(sp - graded_cost - sf, 2)
        ri = round((pr / graded_cost * 100) if graded_cost > 0 else 0, 2)
        return {"sale_price": round(sp, 2), "cost_basis": graded_cost,
                "sell_fee": sf, "profit": pr, "roi": ri}

    results = {
        "raw":   {"sale_price": round(raw_market, 2), "cost_basis": round(raw_price, 2),
                  "sell_fee": raw_sell_fee, "profit": raw_profit, "roi": raw_roi},
        "psa8":  _graded(psa8_val),
        "psa9":  _graded(psa9_val),
        "psa10": _graded(psa10_val),
    }

    best_key = max(results, key=lambda k: results[k]["roi"])
    best_roi = results[best_key]["roi"]
    be_grade = _break_even(results)
    be_label = _BE_LABELS.get(be_grade)
    real_data = _has_real_data(prices)

    conf_order  = {"low": 0, "medium": 1, "high": 2}
    best_conf   = max(
        (prices[k].get("confidence", "low") for k in ("raw","psa8","psa9","psa10")),
        key=lambda c: conf_order.get(c, 0),
    )
    total_comps = sum(prices[k].get("comp_count", 0) for k in ("raw","psa8","psa9","psa10"))
    avg_comps   = total_comps // 4

    if not real_data:
        recommendation = "Insufficient Data"
        why = ("No verified comps found. Connect APIFY_TOKEN or "
               "SPORTSCARDSPRO_API_KEY to fetch real sold data.")
    else:
        # BUY = profitable at PSA 9 or PSA 8 with real comps and meaningful ROI
        # WATCH = profitable only at PSA 10 (gem-grade requirement)
        # Avoid = no profitable grading path
        psa9_ok = results["psa9"]["profit"] >= 0 and results["psa9"]["roi"] >= 20
        psa8_ok = results["psa8"]["profit"] >= 0 and results["psa8"]["roi"] >= 20
        psa10_ok = results["psa10"]["profit"] >= 0

        if psa9_ok or psa8_ok:
            target    = "PSA 9" if psa9_ok else "PSA 8"
            target_roi = results["psa9"]["roi"] if psa9_ok else results["psa8"]["roi"]
            recommendation = "Strong Buy"
            why = (f"Profitable at {target} — {target_roi:.1f}% ROI "
                   f"from real sold comps.")
            if be_label: why += f" Break-even at {be_label}."
        elif psa10_ok:
            recommendation = "Possible Buy"
            why = "Profitable only at PSA 10 — gem-grade submission required."
            if be_label: why += f" Break-even at {be_label}."
        else:
            recommendation = "Avoid"
            why = ("No grading strategy is profitable at current verified prices."
                   if be_label is None else
                   f"Must reach at least {be_label} to break even.")

    cg = cardgrade_score(best_roi, best_conf, avg_comps, real_data, be_grade)

    return {
        "results":          results,
        "best":             {"key": best_key, "label": _GRADE_LABELS[best_key],
                             "roi": best_roi, "profit": results[best_key]["profit"]},
        "recommendation":   recommendation,
        "why":              why,
        "signal":           _short_signal(recommendation, best_roi, best_conf, be_grade),
        "has_real_data":    real_data,
        "break_even_grade": be_grade,
        "break_even_label": be_label,
        "cardgrade_score":  cg,
        "set_risk":         set_grading_risk(set_name),
        "costs": {
            "raw_price":   raw_price,
            "grading":     grading_cost,
            "shipping":    shipping,
            "fee_pct":     selling_fee_pct,
            "graded_cost": graded_cost,
        },
    }
