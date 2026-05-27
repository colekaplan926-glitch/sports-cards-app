import json
import logging
import os
import sqlite3
from flask import Flask, request, jsonify, render_template, g
from calculations import calculate_profits
import snipe

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_data_dir = ".data" if os.path.isdir(".data") else "."
DATABASE  = os.path.join(_data_dir, "sports_cards.db")


# ── DB helpers ────────────────────────────────────────────────────────────────

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

        # ── Original tables ────────────────────────────────────────────────
        db.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_name TEXT NOT NULL,
                year TEXT, set_name TEXT, card_number TEXT, sport TEXT,
                raw_price REAL, psa10_price REAL,
                best_profit REAL, best_roi REAL, best_option TEXT,
                recommendation TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        existing = {r[1] for r in db.execute("PRAGMA table_info(watchlist)")}
        for col, defn in [("best_profit","REAL"),("best_roi","REAL"),("best_option","TEXT")]:
            if col not in existing:
                db.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {defn}")

        # ── Snipe Finder tables ────────────────────────────────────────────
        db.execute("""
            CREATE TABLE IF NOT EXISTS saved_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sport TEXT,
                player_names TEXT DEFAULT '[]',
                year_min INTEGER DEFAULT 2015,
                year_max INTEGER DEFAULT 2024,
                sets TEXT DEFAULT '[]',
                max_raw_price REAL DEFAULT 100,
                grading_cost REAL DEFAULT 25,
                shipping_cost REAL DEFAULT 15,
                selling_fee_pct REAL DEFAULT 12.9,
                min_profit REAL DEFAULT 0,
                min_roi REAL DEFAULT 20,
                frequency TEXT DEFAULT 'daily',
                is_active INTEGER DEFAULT 1,
                last_run_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                search_id INTEGER NOT NULL,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                listings_checked INTEGER DEFAULT 0,
                deals_found INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                error_msg TEXT,
                FOREIGN KEY (search_id) REFERENCES saved_searches(id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS found_deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                search_id INTEGER NOT NULL,
                scan_run_id INTEGER NOT NULL,
                listing_title TEXT NOT NULL,
                listing_url TEXT,
                listing_price REAL NOT NULL,
                listing_source TEXT DEFAULT 'eBay',
                player_name TEXT, year TEXT, set_name TEXT,
                card_number TEXT, sport TEXT,
                raw_market REAL DEFAULT 0,
                psa8_market REAL DEFAULT 0,
                psa9_market REAL DEFAULT 0,
                psa10_market REAL DEFAULT 0,
                raw_profit REAL DEFAULT 0,
                psa8_profit REAL DEFAULT 0,
                psa9_profit REAL DEFAULT 0,
                psa10_profit REAL DEFAULT 0,
                raw_roi REAL DEFAULT 0,
                psa8_roi REAL DEFAULT 0,
                psa9_roi REAL DEFAULT 0,
                psa10_roi REAL DEFAULT 0,
                best_option TEXT,
                best_profit REAL DEFAULT 0,
                best_roi REAL DEFAULT 0,
                break_even_grade TEXT,
                recommendation TEXT,
                reason TEXT,
                raw_comps INTEGER DEFAULT 0,
                psa8_comps INTEGER DEFAULT 0,
                psa9_comps INTEGER DEFAULT 0,
                psa10_comps INTEGER DEFAULT 0,
                confidence TEXT DEFAULT 'low',
                data_source TEXT,
                is_mock INTEGER DEFAULT 0,
                found_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_dismissed INTEGER DEFAULT 0,
                FOREIGN KEY (search_id) REFERENCES saved_searches(id),
                FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS sold_comps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id INTEGER NOT NULL,
                grade TEXT NOT NULL,
                price REAL NOT NULL,
                title TEXT,
                url TEXT,
                sale_date TEXT,
                source TEXT,
                FOREIGN KEY (deal_id) REFERENCES found_deals(id)
            )
        """)

        # Migrate found_deals: add cardgrade_score column if missing
        deals_cols = {r[1] for r in db.execute("PRAGMA table_info(found_deals)")}
        if "cardgrade_score" not in deals_cols:
            db.execute("ALTER TABLE found_deals ADD COLUMN cardgrade_score INTEGER DEFAULT 0")

        db.commit()


# ── Comp parser ──────────────────────────────────────────────────────────────

def _parse_user_comps(comps_str: str) -> dict:
    """Parse comma-separated prices entered by the user into a prices-dict grade entry."""
    empty = {
        "median_price": 0, "confidence": "low", "is_live": False,
        "comp_count": 0, "comps": [], "source_name": "User entered",
        "high_price": 0, "low_price": 0, "date_range": "—",
    }
    if not comps_str or not str(comps_str).strip():
        return empty

    prices = []
    for part in str(comps_str).split(","):
        cleaned = part.strip().replace("$", "").replace(",", "")
        try:
            v = float(cleaned)
            if 0.5 < v < 500_000:
                prices.append(v)
        except ValueError:
            pass

    if not prices:
        return empty

    # IQR outlier removal when 4+ comps (same rule as the scraper)
    if len(prices) >= 4:
        sp = sorted(prices)
        n  = len(sp)
        q1, q3 = sp[n // 4], sp[(3 * n) // 4]
        iqr = q3 - q1
        if iqr > 0:
            filtered = [p for p in prices if (q1 - 1.5 * iqr) <= p <= (q3 + 1.5 * iqr)]
            if filtered:
                prices = filtered

    sp = sorted(prices)
    n  = len(sp)
    median = sp[n // 2] if n % 2 == 1 else (sp[n // 2 - 1] + sp[n // 2]) / 2.0
    conf   = "high" if n >= 5 else "medium" if n >= 3 else "low"

    return {
        "median_price": round(median, 2),
        "confidence":   conf,
        "is_live":      True,
        "comp_count":   n,
        "comps":        [{"price": p, "title": "", "url": "", "date": ""} for p in sp],
        "source_name":  "User entered",
        "high_price":   sp[-1],
        "low_price":    sp[0],
        "date_range":   "—",
    }


# ── Calculator routes ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/calculate", methods=["POST"])
def calculate():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    prices_dict = {
        "raw":   _parse_user_comps(data.get("comps_raw",   "")),
        "psa8":  _parse_user_comps(data.get("comps_psa8",  "")),
        "psa9":  _parse_user_comps(data.get("comps_psa9",  "")),
        "psa10": _parse_user_comps(data.get("comps_psa10", "")),
    }
    calc = calculate_profits(
        raw_price       = float(data.get("raw_price", 0) or 0),
        grading_cost    = float(data.get("grading_cost", 0) or 0),
        shipping        = float(data.get("shipping_fees", 0) or 0),
        selling_fee_pct = float(data.get("selling_fee_pct", 0) or 0),
        prices          = prices_dict,
        set_name        = data.get("set_name", ""),
    )
    calc["prices"] = prices_dict
    return jsonify(calc)


# ── Watchlist routes ──────────────────────────────────────────────────────────

@app.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    db    = get_db()
    cards = db.execute("SELECT * FROM watchlist ORDER BY created_at DESC").fetchall()
    return jsonify([dict(r) for r in cards])


@app.route("/api/watchlist", methods=["POST"])
def add_to_watchlist():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    prices_dict = {
        "raw":   _parse_user_comps(data.get("comps_raw",   "")),
        "psa8":  _parse_user_comps(data.get("comps_psa8",  "")),
        "psa9":  _parse_user_comps(data.get("comps_psa9",  "")),
        "psa10": _parse_user_comps(data.get("comps_psa10", "")),
    }
    calc = calculate_profits(
        raw_price       = float(data.get("raw_price", 0) or 0),
        grading_cost    = float(data.get("grading_cost", 0) or 0),
        shipping        = float(data.get("shipping_fees", 0) or 0),
        selling_fee_pct = float(data.get("selling_fee_pct", 0) or 0),
        prices          = prices_dict,
    )
    best = calc["best"]

    db = get_db()
    db.execute(
        """INSERT INTO watchlist
           (player_name, year, set_name, card_number, sport,
            raw_price, psa10_price, best_profit, best_roi, best_option, recommendation)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (data.get("player_name",""), data.get("year",""), data.get("set_name",""),
         data.get("card_number",""), data.get("sport",""),
         float(data.get("raw_price", 0) or 0), prices_dict["psa10"]["median_price"],
         best["profit"], best["roi"], best["label"], calc["recommendation"]),
    )
    db.commit()
    return jsonify({"success": True, "message": "Card added to watchlist"})


@app.route("/api/watchlist/<int:card_id>", methods=["DELETE"])
def delete_from_watchlist(card_id):
    db = get_db()
    db.execute("DELETE FROM watchlist WHERE id=?", (card_id,))
    db.commit()
    return jsonify({"success": True})


# ── Snipe: Saved Searches ─────────────────────────────────────────────────────

@app.route("/api/snipe/searches", methods=["GET"])
def list_searches():
    db   = get_db()
    rows = db.execute("""
        SELECT s.*,
               (SELECT COUNT(*) FROM found_deals d
                WHERE d.search_id=s.id AND d.is_dismissed=0) AS deal_count,
               (SELECT COUNT(*) FROM scan_runs r
                WHERE r.search_id=s.id) AS run_count
        FROM saved_searches s ORDER BY s.created_at DESC
    """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/snipe/searches", methods=["POST"])
def create_search():
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "name required"}), 400

    db = get_db()
    cur = db.execute(
        """INSERT INTO saved_searches
           (name, sport, player_names, year_min, year_max, sets,
            max_raw_price, grading_cost, shipping_cost, selling_fee_pct,
            min_profit, min_roi, frequency, is_active)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (
            data["name"],
            data.get("sport", ""),
            json.dumps(data.get("player_names", [])),
            int(data.get("year_min") or 2015),
            int(data.get("year_max") or 2024),
            json.dumps(data.get("sets", [])),
            float(data.get("max_raw_price", 100)),
            float(data.get("grading_cost", 25)),
            float(data.get("shipping_cost", 15)),
            float(data.get("selling_fee_pct", 12.9)),
            float(data.get("min_profit", 0)),
            float(data.get("min_roi", 20)),
            data.get("frequency", "daily"),
        ),
    )
    db.commit()
    return jsonify({"success": True, "id": cur.lastrowid})


@app.route("/api/snipe/searches/<int:sid>", methods=["GET"])
def get_search(sid):
    db  = get_db()
    row = db.execute("SELECT * FROM saved_searches WHERE id=?", (sid,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


@app.route("/api/snipe/searches/<int:sid>", methods=["PUT"])
def update_search(sid):
    data = request.get_json()
    if not data:
        return jsonify({"error": "no data"}), 400
    db = get_db()
    db.execute(
        """UPDATE saved_searches SET
           name=?, sport=?, player_names=?, year_min=?, year_max=?, sets=?,
           max_raw_price=?, grading_cost=?, shipping_cost=?, selling_fee_pct=?,
           min_profit=?, min_roi=?, frequency=?, is_active=?
           WHERE id=?""",
        (
            data.get("name", ""),
            data.get("sport", ""),
            json.dumps(data.get("player_names", [])),
            int(data.get("year_min") or 2015),
            int(data.get("year_max") or 2024),
            json.dumps(data.get("sets", [])),
            float(data.get("max_raw_price", 100)),
            float(data.get("grading_cost", 25)),
            float(data.get("shipping_cost", 15)),
            float(data.get("selling_fee_pct", 12.9)),
            float(data.get("min_profit", 0)),
            float(data.get("min_roi", 20)),
            data.get("frequency", "daily"),
            int(data.get("is_active", 1)),
            sid,
        ),
    )
    db.commit()
    return jsonify({"success": True})


@app.route("/api/snipe/searches/<int:sid>", methods=["DELETE"])
def delete_search(sid):
    db = get_db()
    db.execute("DELETE FROM found_deals WHERE search_id=?", (sid,))
    db.execute("DELETE FROM scan_runs WHERE search_id=?", (sid,))
    db.execute("DELETE FROM saved_searches WHERE id=?", (sid,))
    db.commit()
    return jsonify({"success": True})


@app.route("/api/snipe/searches/<int:sid>/run", methods=["POST"])
def run_search_now(sid):
    """Trigger an immediate scan in a background thread."""
    db  = get_db()
    row = db.execute("SELECT id FROM saved_searches WHERE id=?", (sid,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    scan_run_id = snipe.run_search_background(sid)
    return jsonify({"success": True, "scan_run_id": scan_run_id})


# ── Snipe: Scan runs ──────────────────────────────────────────────────────────

@app.route("/api/snipe/runs/<int:run_id>/status", methods=["GET"])
def scan_run_status(run_id):
    db  = get_db()
    row = db.execute("SELECT * FROM scan_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


# ── Snipe: Deals ──────────────────────────────────────────────────────────────

@app.route("/api/snipe/deals", methods=["GET"])
def list_deals():
    rec     = request.args.get("recommendation")   # BUY | WATCH | PASS
    sid     = request.args.get("search_id", type=int)
    limit   = request.args.get("limit", 100, type=int)
    show_dismissed = request.args.get("dismissed", "0") == "1"

    db    = get_db()
    where = ["d.is_dismissed = ?", "d.is_mock = 0"]
    args  = [1 if show_dismissed else 0]

    if rec:
        where.append("d.recommendation = ?")
        args.append(rec)
    if sid:
        where.append("d.search_id = ?")
        args.append(sid)

    sql = f"""
        SELECT d.*, s.name AS search_name
        FROM found_deals d
        LEFT JOIN saved_searches s ON s.id = d.search_id
        WHERE {" AND ".join(where)}
        ORDER BY d.found_at DESC
        LIMIT {limit}
    """
    rows = db.execute(sql, args).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/snipe/deals/<int:did>", methods=["GET"])
def get_deal(did):
    db   = get_db()
    deal = db.execute("SELECT * FROM found_deals WHERE id=?", (did,)).fetchone()
    if not deal:
        return jsonify({"error": "not found"}), 404
    comps = db.execute(
        "SELECT * FROM sold_comps WHERE deal_id=? ORDER BY grade, price",
        (did,)
    ).fetchall()
    d = dict(deal)
    d["comps"] = [dict(c) for c in comps]
    return jsonify(d)


@app.route("/api/snipe/deals/<int:did>/dismiss", methods=["POST"])
def dismiss_deal(did):
    db = get_db()
    db.execute("UPDATE found_deals SET is_dismissed=1 WHERE id=?", (did,))
    db.commit()
    return jsonify({"success": True})


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(".data", exist_ok=True)
    init_db()

    # Start background scheduler (only in the main Werkzeug process, not the reloader)
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not os.environ.get("FLASK_DEBUG"):
        import scheduler
        scheduler.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
