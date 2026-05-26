import sqlite3
import os
from flask import Flask, request, jsonify, render_template, g

app = Flask(__name__)

# On Glitch, .data/ persists across restarts and is excluded from git.
# Locally, fall back to the project root.
_data_dir = ".data" if os.path.isdir(".data") else "."
DATABASE = os.path.join(_data_dir, "sports_cards.db")


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
                expected_profit REAL,
                expected_roi REAL,
                recommendation TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()


def calculate_profits(data):
    raw_price = float(data.get("raw_price", 0))
    grading_cost = float(data.get("grading_cost", 0))
    shipping_fees = float(data.get("shipping_fees", 0))
    selling_fee_pct = float(data.get("selling_fee_pct", 0)) / 100

    raw_market = float(data.get("raw_market", 0))
    psa10_market = float(data.get("psa10_market", 0))
    psa9_market = float(data.get("psa9_market", 0))
    psa8_market = float(data.get("psa8_market", 0))

    prob_10 = float(data.get("prob_10", 0)) / 100
    prob_9 = float(data.get("prob_9", 0)) / 100
    prob_8 = float(data.get("prob_8", 0)) / 100
    prob_lower = float(data.get("prob_lower", 0)) / 100

    graded_total_cost = raw_price + grading_cost + shipping_fees
    raw_total_cost = raw_price

    def grade_result(sale_price, total_cost):
        selling_fees = sale_price * selling_fee_pct
        profit = sale_price - total_cost - selling_fees
        roi = (profit / total_cost * 100) if total_cost > 0 else 0
        return {
            "sale_price": round(sale_price, 2),
            "total_cost": round(total_cost, 2),
            "selling_fees": round(selling_fees, 2),
            "profit": round(profit, 2),
            "roi": round(roi, 2),
        }

    results = {
        "psa10": grade_result(psa10_market, graded_total_cost),
        "psa9": grade_result(psa9_market, graded_total_cost),
        "psa8": grade_result(psa8_market, graded_total_cost),
        "raw": grade_result(raw_market, raw_total_cost),
        "lower": grade_result(raw_market, graded_total_cost),
    }

    # Expected value using lower grade = sells at raw but paid grading costs
    ev_profit = (
        prob_10 * results["psa10"]["profit"]
        + prob_9 * results["psa9"]["profit"]
        + prob_8 * results["psa8"]["profit"]
        + prob_lower * results["lower"]["profit"]
    )
    ev_cost = graded_total_cost
    ev_roi = (ev_profit / ev_cost * 100) if ev_cost > 0 else 0

    if ev_roi >= 50:
        recommendation = "Strong Buy"
    elif ev_roi >= 20:
        recommendation = "Possible Buy"
    else:
        recommendation = "Avoid"

    return {
        "grades": results,
        "expected": {
            "profit": round(ev_profit, 2),
            "roi": round(ev_roi, 2),
            "recommendation": recommendation,
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
    results = calculate_profits(data)
    return jsonify(results)


@app.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    db = get_db()
    cards = db.execute(
        "SELECT * FROM watchlist ORDER BY created_at DESC"
    ).fetchall()
    return jsonify([dict(row) for row in cards])


@app.route("/api/watchlist", methods=["POST"])
def add_to_watchlist():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    calc = calculate_profits(data)
    expected = calc["expected"]

    db = get_db()
    db.execute(
        """INSERT INTO watchlist
           (player_name, year, set_name, card_number, sport,
            raw_price, psa10_price, expected_profit, expected_roi, recommendation)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.get("player_name", ""),
            data.get("year", ""),
            data.get("set_name", ""),
            data.get("card_number", ""),
            data.get("sport", ""),
            float(data.get("raw_price", 0)),
            float(data.get("psa10_market", 0)),
            expected["profit"],
            expected["roi"],
            expected["recommendation"],
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
    # Glitch injects PORT; fall back to 5000 locally.
    os.makedirs(".data", exist_ok=True)
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
