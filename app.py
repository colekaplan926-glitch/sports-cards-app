import sqlite3
import os
import logging
from flask import Flask, request, jsonify, render_template, g
from pricing import PROVIDER_CHAIN

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_data_dir = ".data" if os.path.isdir(".data") else "."
DATABASE  = os.path.join(_data_dir, "sports_cards.db")


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    with app.app_context():
        db = get_db()
        db.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_name TEXT NOT NULL,
                year TEXT,
                set_name TEXT,
                card_number TEXT,
                sport TEXT,
                raw_price REAL,
                psa10_price REAL,
                best_profit REAL,
                best_roi REAL,
                best_option TEXT,
                recommendation TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        existing = {row[1] for row in db.execute("PRAGMA table_info(watchlist)")}
        for col, defn in [
            ("best_profit", "REAL"),
            ("best_roi",    "REAL"),
            ("best_option", "TEXT"),
        ]:
            if col not in existing:
                db.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {defn}")
        db.commit()


def calculate_profits(data: dict, prices: dict) -> dict:
    """
    Separated profit logic:

    Sell raw now:
        Profit = Raw Market Value − Raw Purchase Price − Selling Fees

    Grade then sell:
        Total Cost = Raw Purchase Price + Grading Fee + Shipping to/from Grader
        Profit = PSA Sale Price − Total Cost − Selling Fees
    """
    raw_buy  = float(data.get("raw_price", 0))
    grading  = float(data.get("grading_cost", 0))
    shipping = float(data.get("shipping_fees", 0))
    fee_pct  = float(data.get("selling_fee_pct", 0)) / 100

    raw_market = prices["raw"]["median_price"]
    psa8_val   = prices["psa8"]["median_price"]
    psa9_val   = prices["psa9"]["median_price"]
    psa10_val  = prices["psa10"]["median_price"]

    # ── Sell raw ──────────────────────────────────────────────────────────────
    raw_sell_fee = round(raw_market * fee_pct, 2)
    raw_cost     = round(raw_buy, 2)
    raw_profit   = round(raw_market - raw_cost - raw_sell_fee, 2)
    raw_roi      = round((raw_profit / raw_cost * 100) if raw_cost > 0 else 0, 2)

    # ── Grade then sell ───────────────────────────────────────────────────────
    graded_cost = round(raw_buy + grading + shipping, 2)

    def graded(sale_price: float) -> dict:
        sell_fee = round(sale_price * fee_pct, 2)
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
            "cost_basis": raw_cost,
            "sell_fee":   raw_sell_fee,
            "profit":     raw_profit,
            "roi":        raw_roi,
        },
        "psa8":  graded(psa8_val),
        "psa9":  graded(psa9_val),
        "psa10": graded(psa10_val),
    }

    best_key = max(results, key=lambda k: results[k]["roi"])
    best_labels = {
        "raw":   "Sell Raw",
        "psa8":  "Grade → PSA 8",
        "psa9":  "Grade → PSA 9",
        "psa10": "Grade → PSA 10",
    }
    best_roi = results[best_key]["roi"]

    if best_roi >= 50:
        recommendation = "Strong Buy"
    elif best_roi >= 20:
        recommendation = "Possible Buy"
    else:
        recommendation = "Avoid"

    return {
        "results": results,
        "best": {
            "key":    best_key,
            "label":  best_labels[best_key],
            "roi":    best_roi,
            "profit": results[best_key]["profit"],
        },
        "recommendation": recommendation,
        "costs": {
            "raw_buy":     raw_buy,
            "grading":     grading,
            "shipping":    shipping,
            "fee_pct":     float(data.get("selling_fee_pct", 0)),
            "graded_cost": graded_cost,
        },
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/calculate", methods=["POST"])
def calculate():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    try:
        card_prices = PROVIDER_CHAIN.fetch(
            player_name   = data.get("player_name", ""),
            year          = data.get("year", ""),
            set_name      = data.get("set_name", ""),
            card_number   = data.get("card_number", ""),
            sport         = data.get("sport", ""),
            raw_buy_price = float(data.get("raw_price", 0)),
            include_autos = data.get("include_autos", False),
        )
    except Exception as exc:
        logger.error("Pricing fetch failed: %s", exc)
        return jsonify({"error": "Pricing lookup failed. Try again."}), 500

    prices_dict = card_prices.to_dict()
    calc        = calculate_profits(data, prices_dict)
    calc["prices"] = prices_dict
    return jsonify(calc)


@app.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    db    = get_db()
    cards = db.execute(
        "SELECT * FROM watchlist ORDER BY created_at DESC"
    ).fetchall()
    return jsonify([dict(row) for row in cards])


@app.route("/api/watchlist", methods=["POST"])
def add_to_watchlist():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    try:
        card_prices = PROVIDER_CHAIN.fetch(
            player_name   = data.get("player_name", ""),
            year          = data.get("year", ""),
            set_name      = data.get("set_name", ""),
            card_number   = data.get("card_number", ""),
            sport         = data.get("sport", ""),
            raw_buy_price = float(data.get("raw_price", 0)),
        )
    except Exception as exc:
        logger.error("Pricing fetch failed for watchlist: %s", exc)
        return jsonify({"error": "Pricing lookup failed"}), 500

    prices_dict = card_prices.to_dict()
    calc        = calculate_profits(data, prices_dict)
    best        = calc["best"]

    db = get_db()
    db.execute(
        """INSERT INTO watchlist
           (player_name, year, set_name, card_number, sport,
            raw_price, psa10_price, best_profit, best_roi, best_option, recommendation)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.get("player_name", ""),
            data.get("year", ""),
            data.get("set_name", ""),
            data.get("card_number", ""),
            data.get("sport", ""),
            float(data.get("raw_price", 0)),
            prices_dict["psa10"]["median_price"],
            best["profit"],
            best["roi"],
            best["label"],
            calc["recommendation"],
        ),
    )
    db.commit()
    return jsonify({"success": True, "message": "Card added to watchlist"})


@app.route("/api/watchlist/<int:card_id>", methods=["DELETE"])
def delete_from_watchlist(card_id):
    db = get_db()
    db.execute("DELETE FROM watchlist WHERE id = ?", (card_id,))
    db.commit()
    return jsonify({"success": True})


if __name__ == "__main__":
    os.makedirs(".data", exist_ok=True)
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
