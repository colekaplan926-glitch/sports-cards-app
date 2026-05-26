// ── State ──────────────────────────────────────────────
let calcResults = null;
let calcData    = null;

// ── Tabs ───────────────────────────────────────────────
function showPage(name) {
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".nav-tab").forEach(t => t.classList.remove("active"));
  document.getElementById("page-" + name).classList.add("active");
  document.querySelector(`[data-tab="${name}"]`).classList.add("active");
  if (name === "watchlist") loadWatchlist();
}

// ── Format helpers ─────────────────────────────────────
const fmt$ = v => {
  const n = parseFloat(v);
  if (isNaN(n)) return "$0.00";
  return (n < 0 ? "-$" : "$") + Math.abs(n).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
};

const fmtPct = v => {
  const n = parseFloat(v);
  return isNaN(n) ? "0.0%" : n.toFixed(1) + "%";
};

const profitClass = v => parseFloat(v) >= 0 ? "text-green" : "text-red";

// ── Calculate ──────────────────────────────────────────
async function calculate() {
  const fields = [
    "player_name", "year", "set_name", "card_number", "sport",
    "raw_price", "grading_cost", "shipping_fees", "selling_fee_pct",
  ];

  const data = {};
  for (const f of fields) {
    const el = document.getElementById(f);
    if (el) data[f] = el.value;
  }

  if (!data.raw_price || parseFloat(data.raw_price) <= 0) {
    showToast("Enter a raw purchase price", "error");
    return;
  }

  const btn = document.getElementById("calc-btn");
  btn.disabled = true;
  btn.textContent = "Estimating…";

  try {
    const res = await fetch("/api/calculate", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(data),
    });
    const result = await res.json();
    calcResults = result;
    calcData    = data;
    renderResults(result, data);
  } catch (e) {
    showToast("Calculation failed. Check your inputs.", "error");
  } finally {
    btn.disabled    = false;
    btn.textContent = "⚡ Estimate & Calculate";
  }
}

// ── Render Results ─────────────────────────────────────
function renderResults(r, data) {
  document.getElementById("results-placeholder").style.display = "none";
  document.getElementById("results-content").style.display     = "flex";

  const { results, best, recommendation, prices, costs } = r;

  // Card title
  const parts = [data.year, data.player_name].filter(Boolean).join(" ");
  const setStr = data.set_name ? " – " + data.set_name : "";
  const numStr = data.card_number ? " #" + data.card_number : "";
  const cardTitle = parts + setStr + numStr || "Card";

  // ── Recommendation banner ──
  const recMap = {
    "Strong Buy":   { cls: "rec-strong-buy",  icon: "📈", color: "text-green"  },
    "Possible Buy": { cls: "rec-possible-buy", icon: "🤔", color: "text-yellow" },
    "Avoid":        { cls: "rec-avoid",        icon: "🚫", color: "text-red"    },
  };
  const rm  = recMap[recommendation] || recMap["Avoid"];
  const rec = document.getElementById("rec-banner");
  rec.className = "recommendation-banner " + rm.cls;
  rec.innerHTML = `
    <div class="rec-icon">${rm.icon}</div>
    <div>
      <div class="rec-label">Recommendation</div>
      <div class="rec-title ${rm.color}">${recommendation}</div>
      <div class="rec-sub">${cardTitle} · Best: ${best.label}</div>
    </div>
    <div style="margin-left:auto;text-align:right">
      <div class="stat-label">Best ROI</div>
      <div class="stat-value ${rm.color}">${fmtPct(best.roi)}</div>
      <div class="stat-sub">Profit: ${fmt$(best.profit)}</div>
    </div>`;

  // ── Estimated values strip ──
  document.getElementById("est-raw").textContent   = fmt$(prices.raw_market);
  document.getElementById("est-psa8").textContent  = fmt$(prices.psa8);
  document.getElementById("est-psa9").textContent  = fmt$(prices.psa9);
  document.getElementById("est-psa10").textContent = fmt$(prices.psa10);
  document.getElementById("est-source").textContent = prices.source;
  document.getElementById("est-confidence").className =
    "confidence-badge conf-" + prices.confidence;
  document.getElementById("est-confidence").textContent =
    prices.confidence.charAt(0).toUpperCase() + prices.confidence.slice(1) + " confidence";

  // ── Profit table ──
  const rows = [
    { label: "Sell Raw",      cls: "grade-raw", key: "raw"   },
    { label: "Grade → PSA 8", cls: "grade-8",   key: "psa8"  },
    { label: "Grade → PSA 9", cls: "grade-9",   key: "psa9"  },
    { label: "Grade → PSA 10",cls: "grade-10",  key: "psa10" },
  ];

  document.getElementById("results-tbody").innerHTML = rows.map(row => {
    const g        = results[row.key];
    const isBest   = row.key === best.key;
    const bestMark = isBest ? ' <span class="best-badge">★ Best</span>' : "";
    return `<tr class="${isBest ? "row-best" : ""}">
      <td><span class="grade-badge ${row.cls}">${row.label}</span>${bestMark}</td>
      <td class="fw-bold">${fmt$(g.sale_price)}</td>
      <td>${fmt$(g.cost_basis)}<div class="cell-sub">+${fmt$(g.sell_fee)} selling fees</div></td>
      <td class="${profitClass(g.profit)} fw-bold">${fmt$(g.profit)}</td>
      <td class="${profitClass(g.roi)} fw-bold">${fmtPct(g.roi)}</td>
    </tr>`;
  }).join("");

  // ── Formulas ──
  renderFormulas(results, costs, prices);
}

// ── Render Formulas ────────────────────────────────────
function renderFormulas(results, costs, prices) {
  const { raw_buy, grading, shipping, fee_pct, graded_cost } = costs;
  const raw = results.raw;
  const p10 = results.psa10;

  const feeLabel = `${fee_pct}%`;

  document.getElementById("formulas-section").innerHTML = `
    <div class="formula-grid">

      <div class="formula-block">
        <div class="formula-heading">Sell Raw Now</div>
        <div class="formula-line">
          <span class="fl-label">Cost basis</span>
          <span class="fl-expr">= Raw Purchase Price</span>
          <span class="fl-val">${fmt$(raw_buy)}</span>
        </div>
        <div class="formula-line">
          <span class="fl-label">Selling fees</span>
          <span class="fl-expr">= Raw Market × ${feeLabel}</span>
          <span class="fl-val">${fmt$(raw.sell_fee)}</span>
        </div>
        <div class="formula-line formula-result">
          <span class="fl-label">Profit</span>
          <span class="fl-expr">= ${fmt$(raw.sale_price)} − ${fmt$(raw_buy)} − ${fmt$(raw.sell_fee)}</span>
          <span class="fl-val ${profitClass(raw.profit)} fw-bold">${fmt$(raw.profit)}</span>
        </div>
        <div class="formula-line formula-result">
          <span class="fl-label">ROI</span>
          <span class="fl-expr">= Profit ÷ ${fmt$(raw_buy)} × 100</span>
          <span class="fl-val ${profitClass(raw.roi)} fw-bold">${fmtPct(raw.roi)}</span>
        </div>
      </div>

      <div class="formula-block">
        <div class="formula-heading">Grade &amp; Sell (PSA 10 example)</div>
        <div class="formula-line">
          <span class="fl-label">Cost basis</span>
          <span class="fl-expr">= Raw + Grading + Shipping</span>
          <span class="fl-val">${fmt$(raw_buy)} + ${fmt$(grading)} + ${fmt$(shipping)} = ${fmt$(graded_cost)}</span>
        </div>
        <div class="formula-line">
          <span class="fl-label">Selling fees</span>
          <span class="fl-expr">= PSA 10 Value × ${feeLabel}</span>
          <span class="fl-val">${fmt$(p10.sell_fee)}</span>
        </div>
        <div class="formula-line formula-result">
          <span class="fl-label">Profit</span>
          <span class="fl-expr">= ${fmt$(p10.sale_price)} − ${fmt$(graded_cost)} − ${fmt$(p10.sell_fee)}</span>
          <span class="fl-val ${profitClass(p10.profit)} fw-bold">${fmt$(p10.profit)}</span>
        </div>
        <div class="formula-line formula-result">
          <span class="fl-label">ROI</span>
          <span class="fl-expr">= Profit ÷ ${fmt$(graded_cost)} × 100</span>
          <span class="fl-val ${profitClass(p10.roi)} fw-bold">${fmtPct(p10.roi)}</span>
        </div>
      </div>

    </div>`;
}

// ── Add to Watchlist ───────────────────────────────────
async function addToWatchlist() {
  if (!calcData) { showToast("Calculate first before saving", "error"); return; }
  if (!calcData.player_name) { showToast("Enter a player name before saving", "error"); return; }

  try {
    const res = await fetch("/api/watchlist", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(calcData),
    });
    const result = await res.json();
    if (result.success) showToast("Card saved to watchlist!", "success");
    else showToast("Failed to save card", "error");
  } catch (e) {
    showToast("Error saving card", "error");
  }
}

// ── Watchlist ──────────────────────────────────────────
async function loadWatchlist() {
  try {
    const res   = await fetch("/api/watchlist");
    const cards = await res.json();
    renderWatchlist(cards);
  } catch (e) {
    showToast("Failed to load watchlist", "error");
  }
}

function renderWatchlist(cards) {
  const tbody = document.getElementById("watchlist-tbody");
  const empty = document.getElementById("watchlist-empty");

  if (!cards.length) {
    tbody.innerHTML = "";
    empty.style.display = "block";
    return;
  }
  empty.style.display = "none";

  tbody.innerHTML = cards.map(card => {
    const badgeCls = recBadgeClass(card.recommendation);
    const cardLine = [card.year, card.player_name].filter(Boolean).join(" ")
      + (card.set_name    ? " – " + card.set_name    : "")
      + (card.card_number ? " #"  + card.card_number : "");
    return `<tr>
      <td>
        <div class="fw-bold">${card.player_name || "—"}</div>
        <div class="cell-sub">${cardLine}</div>
      </td>
      <td>${card.sport || "—"}</td>
      <td class="fw-bold">${fmt$(card.raw_price)}</td>
      <td class="fw-bold">${fmt$(card.psa10_price)}</td>
      <td class="${profitClass(card.best_profit)} fw-bold">${fmt$(card.best_profit)}</td>
      <td class="${profitClass(card.best_roi)} fw-bold">${fmtPct(card.best_roi)}</td>
      <td><div class="fw-bold fs-sm">${card.best_option || "—"}</div></td>
      <td><span class="badge ${badgeCls}">${card.recommendation}</span></td>
      <td>
        <button class="btn btn-danger btn-sm watchlist-action" onclick="deleteCard(${card.id})">Remove</button>
      </td>
    </tr>`;
  }).join("");
}

function recBadgeClass(rec) {
  if (rec === "Strong Buy")   return "badge-strong-buy";
  if (rec === "Possible Buy") return "badge-possible-buy";
  return "badge-avoid";
}

async function deleteCard(id) {
  try {
    await fetch(`/api/watchlist/${id}`, { method: "DELETE" });
    showToast("Card removed", "info");
    loadWatchlist();
  } catch (e) {
    showToast("Error removing card", "error");
  }
}

// ── Toast ──────────────────────────────────────────────
function showToast(msg, type = "info") {
  const container = document.getElementById("toast-container");
  const toast     = document.createElement("div");
  toast.className  = `toast ${type}`;
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = "slideOut 0.3s ease forwards";
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

// ── Reset ──────────────────────────────────────────────
function resetForm() {
  document.getElementById("calc-form").reset();
  calcResults = null;
  calcData    = null;
  document.getElementById("results-placeholder").style.display = "flex";
  document.getElementById("results-content").style.display     = "none";
}
