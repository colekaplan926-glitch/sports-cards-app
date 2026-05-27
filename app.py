import json
import logging
import os
import sqlite3
import statistics as _stats
import threading as _threading
import re
import time as _time
import urllib.parse as _urllib_parse
from flask import Flask, request, jsonify, render_template, g
from calculations import calculate_profits
import snipe

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Bookmarklet / comp-collector shared state ─────────────────────────────────
_comp_store: dict = {}          # grade → {prices_csv, count, median, source, ts}
_comp_lock  = _threading.Lock()
_card_ctx:  dict = {}           # current card context (set by openSoldSearch)
_ctx_lock   = _threading.Lock()

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


# ── Comp filtering ───────────────────────────────────────────────────────────

_EXCLUDE_KW = [
    "lot of", " lot ", " lot,", "lots of", "reprint", "custom card",
    "fake ", " fake,", "proxy", "redemption", "commemorative", "buyback",
    "artist proof", "blank back", "sample ",
]
_GRADER_PAT   = re.compile(r'\b(bgs|sgc|cgc|beckett|hga|gma)\b', re.I)
_PSA_PAT      = re.compile(r'\bpsa\b', re.I)
_NUMBERED_PAT = re.compile(r'/\s*\d{1,4}\b')
_AUTO_PAT     = re.compile(r'\b(auto|autograph|autographed|signed)\b', re.I)


def _should_keep(comp: dict, grade: str, ctx: dict) -> tuple:
    """Return (keep: bool, reason: str) for a single comp dict."""
    title = (comp.get("title") or "").lower().strip()
    price = float(comp.get("price") or 0)
    if price <= 0 or price >= 500_000:
        return False, "invalid price"
    for kw in _EXCLUDE_KW:
        if kw in title:
            return False, f"excluded: {kw.strip()!r}"
    variation        = (ctx.get("variation") or "").lower()
    is_auto_card     = "auto" in variation
    is_numbered_card = "numbered" in variation
    if grade == "raw":
        if _PSA_PAT.search(title) or _GRADER_PAT.search(title):
            return False, "graded card (collecting raw)"
    else:
        if _GRADER_PAT.search(title) and not _PSA_PAT.search(title):
            return False, "different grader"
        grade_num = {"psa8": "8", "psa9": "9", "psa10": "10"}.get(grade, "")
        if grade_num:
            other = re.search(r'\bpsa\s*(\d+)\b', title, re.I)
            if other and other.group(1) != grade_num:
                return False, f"wrong PSA grade (title says PSA {other.group(1)})"
    if not is_auto_card and _AUTO_PAT.search(title):
        return False, "auto (card is not an auto)"
    if not is_numbered_card and _NUMBERED_PAT.search(title):
        return False, "numbered parallel"
    card_number = (ctx.get("card_number") or "").strip()
    if card_number:
        nums_in_title = re.findall(r'(?<![/\d])#(\d+)\b', title)
        if nums_in_title and card_number not in nums_in_title:
            return False, f"wrong card number (#{', #'.join(nums_in_title)})"
    return True, ""


# ── Bookmarklet JS ────────────────────────────────────────────────────────────

_BOOKMARKLET_BODY = r"""
var h=location.hostname,isE=h.includes('ebay.com'),is1=h.includes('130point.com');
if(!isE&&!is1){alert('CardGrade Pro: Open this on an eBay Sold Listings or 130point.com page.');return;}
var u=decodeURIComponent(location.href).toLowerCase();
var g='raw';
if(/psa[\s+]*(?:gem[\s+]*mint[\s+]*)?10/.test(u))g='psa10';
else if(/psa[\s+]*9(?!\d)/.test(u))g='psa9';
else if(/psa[\s+]*8(?!\d)/.test(u))g='psa8';
var gl={raw:'Raw',psa8:'PSA 8',psa9:'PSA 9',psa10:'PSA 10'}[g];
var cs=[];
if(isE){document.querySelectorAll('li.s-item').forEach(function(el){
  var t=el.querySelector('.s-item__title')||el.querySelector('span[role="heading"]');
  var p=el.querySelector('.s-item__price');
  var a=el.querySelector('a.s-item__link');
  var d=el.querySelector('.s-item__caption--end')||el.querySelector('span.POSITIVE');
  if(!t||!p)return;
  var title=(t.textContent||'').trim();
  if(!title||/shop on ebay/i.test(title))return;
  var price=parseFloat((p.textContent||'').replace(/[^0-9.]/g,''));
  if(!(price>0.5&&price<500000))return;
  cs.push({title:title,price:price,url:a?a.href.split('?')[0]:'',date:d?(d.textContent||'').trim():''});
});}else{document.querySelectorAll('table tbody tr').forEach(function(r){
  var c=r.querySelectorAll('td');if(c.length<3)return;
  var a=c[1]&&c[1].querySelector('a');
  var title=a?(a.textContent||'').trim():(c[1]?(c[1].textContent||'').trim():'');
  var price=parseFloat((c[c.length-1].textContent||'').replace(/[^0-9.]/g,''));
  if(!(price>0.5&&price<500000))return;
  cs.push({title:title,price:price,url:a?a.href:'',date:(c[0].textContent||'').trim()});
});}
if(!cs.length){alert('No sold listings found on this page.\n\nMake sure you are on a Sold/Completed listings page.');return;}
if(!confirm('Send '+cs.length+' '+gl+' sold prices to CardGrade Pro?'))return;
var xhr=new XMLHttpRequest();
xhr.open('POST',APP+'/api/import-comps',true);
xhr.setRequestHeader('Content-Type','application/json');
xhr.onreadystatechange=function(){if(xhr.readyState===4){if(xhr.status===200){
  var r=JSON.parse(xhr.responseText);
  alert('✓ '+r.imported+' '+gl+' comp'+(r.imported===1?'':'s')+' sent!\n('+r.filtered+' filtered)\nMedian: $'+r.median+'\n\nSwitch back to CardGrade Pro and click Calculate.');
}else{alert('Error: '+(xhr.responseText||'could not send comps'));}}};
xhr.onerror=function(){alert('Could not connect to CardGrade Pro.\nMake sure the app is open in another tab.');};
xhr.send(JSON.stringify({grade:g,comps:cs,source:isE?'ebay':'130point',page_url:location.href}));
""".replace("\n", "")


def _build_bookmarklet_js(app_url: str) -> str:
    return "javascript:(function(){var APP='" + app_url + "';" + _BOOKMARKLET_BODY + "})();"


# ── Search URL builder (shared by auto-comps endpoint) ───────────────────────

def _search_urls_for_card(player, year, set_name, card_number, variation):
    from pricing import _build_query
    urls = {}
    for grade in ("raw", "psa8", "psa9", "psa10"):
        q = _build_query(player, year, set_name, card_number, grade, variation)
        urls[grade] = {
            "ebay":   ("https://www.ebay.com/sch/i.html?" +
                       _urllib_parse.urlencode({"_nkw": q, "LH_Sold": "1",
                                                "LH_Complete": "1", "_sop": "13"})),
            "p130":   "https://www.130point.com/sales/?" + _urllib_parse.urlencode({"q": q}),
            "google": "https://www.google.com/search?" + _urllib_parse.urlencode({"q": q + " sold"}),
        }
    return urls


# ── Auto-comps (free scrape attempt) ─────────────────────────────────────────

@app.route("/api/auto-comps", methods=["POST"])
def auto_comps():
    data      = request.get_json() or {}
    player    = (data.get("player_name") or "").strip()
    year      = (data.get("year")        or "").strip()
    set_name  = (data.get("set_name")    or "").strip()
    card_num  = (data.get("card_number") or "").strip()
    variation = (data.get("variation")   or "").strip()

    if not (player or set_name):
        return jsonify({"error": "Enter card details first"}), 400

    search_urls = _search_urls_for_card(player, year, set_name, card_num, variation)

    from pricing import EbayScraperProvider, Point130Provider

    grade_results = {}
    _lock = _threading.Lock()

    def _run():
        scraper = EbayScraperProvider()
        backup  = Point130Provider()
        if not scraper.is_available() and not backup.is_available():
            for g in ("raw", "psa8", "psa9", "psa10"):
                with _lock:
                    grade_results[g] = {"found": False, "count": 0, "median": 0,
                                        "prices_csv": "", "error": "bs4 not installed"}
            return

        for grade in ("psa10", "psa9", "psa8", "raw"):
            comps, err = [], ""
            if scraper.is_available():
                try:
                    comps, err = scraper._fetch_grade(
                        player, year, set_name, card_num, grade, variation)
                except Exception as exc:
                    err = str(exc)

            if (err or not comps) and backup.is_available():
                try:
                    bc, be = backup._fetch_grade(
                        player, year, set_name, card_num, grade, variation)
                    if bc:
                        comps, err = bc, ""
                    elif not err:
                        err = be or "No comps found"
                except Exception as exc2:
                    if not err:
                        err = str(exc2)

            if comps:
                prices = sorted(c.price for c in comps)
                median = _stats.median(prices)
                csv_s  = ", ".join(
                    str(int(p)) if p == int(p) else f"{p:.2f}" for p in prices
                )
                with _lock:
                    grade_results[grade] = {
                        "found": True, "count": len(comps),
                        "median": round(median, 2),
                        "prices_csv": csv_s, "error": None,
                    }
            else:
                with _lock:
                    grade_results[grade] = {
                        "found": False, "count": 0, "median": 0,
                        "prices_csv": "", "error": err or "No matching comps found",
                    }

    t = _threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=25)

    for grade in ("raw", "psa8", "psa9", "psa10"):
        if grade not in grade_results:
            grade_results[grade] = {
                "found": False, "count": 0, "median": 0, "prices_csv": "",
                "error": "Timed out — try the search links",
            }

    any_found = any(v["found"] for v in grade_results.values())
    return jsonify({
        "success":     any_found,
        "grades":      grade_results,
        "search_urls": search_urls,
        "message":     ("Comps found! Click Calculate." if any_found
                        else "Could not auto-fetch comps. Use the search links."),
    })


# ── Bookmarklet page ─────────────────────────────────────────────────────────

@app.route("/bookmarklet")
def bookmarklet_page():
    app_url = request.url_root.rstrip("/")
    return render_template("bookmarklet.html", app_url=app_url,
                           bookmarklet_js=_build_bookmarklet_js(app_url))


# ── Card context (set when user opens a sold search) ─────────────────────────

@app.route("/api/set-card-context", methods=["POST"])
def set_card_context():
    data  = request.get_json() or {}
    grade = (data.get("grade") or "").strip()
    with _ctx_lock:
        _card_ctx.clear()
        _card_ctx.update({k: (data.get(k) or "").strip()
                          for k in ("player_name", "year", "set_name",
                                    "card_number", "variation", "grade")})
    if grade:
        with _comp_lock:
            _comp_store.pop(grade, None)   # clear stale comps for this grade
    return jsonify({"ok": True})


# ── Bookmarklet comp import (called cross-origin from eBay / 130point) ────────

@app.route("/api/import-comps", methods=["POST", "OPTIONS"])
def import_comps():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    data   = request.get_json() or {}
    grade  = data.get("grade", "raw")
    comps  = data.get("comps", [])
    source = data.get("source", "unknown")

    if grade not in ("raw", "psa8", "psa9", "psa10"):
        resp = jsonify({"error": "Invalid grade"})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, 400

    with _ctx_lock:
        ctx = dict(_card_ctx)

    kept, filtered_out = [], 0
    for comp in comps:
        keep, _ = _should_keep(comp, grade, ctx)
        if keep:
            kept.append(comp)
        else:
            filtered_out += 1

    prices = sorted(c["price"] for c in kept)
    if len(prices) >= 4:
        n = len(prices)
        q1, q3 = prices[n // 4], prices[(3 * n) // 4]
        iqr = q3 - q1
        if iqr > 0:
            clean = [p for p in prices if (q1 - 1.5 * iqr) <= p <= (q3 + 1.5 * iqr)]
            if clean:
                prices = clean

    grade_labels = {"raw": "Raw", "psa8": "PSA 8", "psa9": "PSA 9", "psa10": "PSA 10"}
    n = len(prices)
    if n == 0:
        resp = jsonify({"imported": 0, "filtered": filtered_out, "grade": grade,
                        "grade_label": grade_labels[grade], "median": 0,
                        "prices_csv": "", "count": 0,
                        "message": "All comps were filtered out."})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    median = _stats.median(prices)
    csv_s  = ", ".join(str(int(p)) if p == int(p) else f"{p:.2f}" for p in sorted(prices))
    with _comp_lock:
        _comp_store[grade] = {"prices_csv": csv_s, "count": n,
                              "median": round(median, 2), "source": source,
                              "ts": _time.time()}

    resp = jsonify({"imported": n, "filtered": filtered_out, "grade": grade,
                    "grade_label": grade_labels[grade], "median": round(median, 2),
                    "prices_csv": csv_s, "count": n,
                    "message": f"{n} {grade_labels[grade]} comps imported."})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


# ── Polling — frontend checks this every 2 s after opening a sold search ─────

@app.route("/api/comp-poll", methods=["GET"])
def comp_poll():
    with _comp_lock:
        result = {
            grade: {"count": d["count"], "median": d["median"],
                    "prices_csv": d["prices_csv"], "source": d["source"], "ts": d["ts"]}
            for grade, d in _comp_store.items()
        }
    return jsonify(result)


@app.route("/api/reset-comps", methods=["POST"])
def reset_comps():
    with _comp_lock:
        _comp_store.clear()
    return jsonify({"ok": True})


# ── Bulk text parser — Quick Import ──────────────────────────────────────────

_PSA10_PAT = re.compile(r'\bpsa\s*(?:gem\s*mint\s*)?10\b', re.I)
_PSA9_PAT  = re.compile(r'\bpsa\s*9(?!\d)',                re.I)
_PSA8_PAT  = re.compile(r'\bpsa\s*8(?!\d)',                re.I)
_OTHER_GRADER = re.compile(r'\b(bgs|sgc|cgc|beckett|hga|gma)\b', re.I)
_OTHER_PSA    = re.compile(r'\bpsa\s*\d+',                 re.I)
_BULK_EXCLUDE = ["lot of", " lot ", "lots of", "reprint", "custom card",
                 "fake ", "proxy", "redemption", "blank back", "commemorative"]


def _parse_bulk_text(text: str, ctx: dict = None) -> dict:
    """Parse a block of pasted sold-listing text → {grade: [prices]}."""
    ctx = ctx or {}
    variation        = (ctx.get("variation") or "").lower()
    is_auto_card     = "auto" in variation
    result           = {g: [] for g in ("raw", "psa8", "psa9", "psa10")}
    lines            = text.splitlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        ll = line.lower()

        # Skip known-bad listings
        if any(kw in ll for kw in _BULK_EXCLUDE):
            continue
        if not is_auto_card and re.search(r'\b(auto|autograph|signed)\b', ll):
            continue

        # Extract price — $ sign required, OR explicit sale language before a bare number.
        # Bare numbers (years, card #s, jersey #s, serial #s) are never treated as prices.
        m = re.search(r'(?:US\s*)?\$\s*([0-9,]+(?:\.\d{1,2})?)', line, re.I)
        if m:
            price_str = m.group(1).replace(",", "")
        else:
            m2 = re.search(
                r'(?:sold(?:\s+for)?|price\s*:|accepted(?:\s+for)?|final\s+price\s*:?)'
                r'\s+([0-9][0-9,]*(?:\.\d{2})?)\b',
                line, re.I,
            )
            if not m2:
                continue
            price_str = m2.group(1).replace(",", "")
        try:
            price = float(price_str)
        except (ValueError, AttributeError):
            continue
        if not (0.5 < price < 500_000):
            continue

        # Detect grade
        if _OTHER_GRADER.search(ll):
            continue                          # non-PSA graders → skip
        if _PSA10_PAT.search(ll):
            grade = "psa10"
        elif _PSA9_PAT.search(ll):
            grade = "psa9"
        elif _PSA8_PAT.search(ll):
            grade = "psa8"
        elif _OTHER_PSA.search(ll):
            continue                          # PSA 7 / PSA 6 etc → skip
        else:
            grade = "raw"                     # no grader mention → raw

        result[grade].append(price)

    return result


@app.route("/api/parse-bulk-comps", methods=["POST"])
def parse_bulk_comps():
    data = request.get_json() or {}
    text = data.get("text", "")
    if not text.strip():
        return jsonify({"error": "No text provided"}), 400
    with _ctx_lock:
        ctx = dict(_card_ctx)
    parsed  = _parse_bulk_text(text, ctx)
    summary = {}
    for grade, prices in parsed.items():
        if not prices:
            summary[grade] = {"count": 0, "median": 0, "prices_csv": ""}
            continue
        # IQR outlier removal
        sp = sorted(prices)
        n  = len(sp)
        if n >= 4:
            q1, q3 = sp[n // 4], sp[(3 * n) // 4]
            iqr    = q3 - q1
            if iqr > 0:
                clean = [p for p in sp if (q1 - 1.5 * iqr) <= p <= (q3 + 1.5 * iqr)]
                if clean:
                    sp = clean
        med    = _stats.median(sp)
        csv_s  = ", ".join(str(int(p)) if p == int(p) else f"{p:.2f}" for p in sp)
        summary[grade] = {"count": len(sp), "median": round(med, 2), "prices_csv": csv_s}
    return jsonify(summary)


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
