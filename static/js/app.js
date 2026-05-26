// ── State ──────────────────────────────────────────────
let calcResults = null;
let calcData    = null;

// ── Tabs ───────────────────────────────────────────────
function showPage(name) {
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".nav-tab").forEach(t => t.classList.remove("active"));
  document.getElementById("page-" + name).classList.add("active");
  document.querySelector(`[data-tab="${name}"]`).classList.add("active");
  if (name === "watchlist")      loadWatchlist();
  if (name === "snipe-searches") loadSearches();
  if (name === "snipe-deals")    loadDeals();
}

// ── Format helpers ─────────────────────────────────────
const fmt$ = v => {
  const n = parseFloat(v);
  if (isNaN(n)) return "$0.00";
  return (n < 0 ? "-$" : "$") + Math.abs(n).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
};

const fmtPct   = v => { const n = parseFloat(v); return isNaN(n) ? "0.0%" : n.toFixed(1) + "%"; };
const profCls  = v => parseFloat(v) >= 0 ? "text-green" : "text-red";
const confCls  = c => ({ high: "conf-high", medium: "conf-medium", low: "conf-low" }[c] || "conf-low");

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
  btn.disabled    = true;
  btn.innerHTML   = '<span class="spinner"></span> Fetching comps…';

  // Show loading skeleton in results column
  document.getElementById("results-placeholder").style.display = "none";
  document.getElementById("results-loading").style.display     = "flex";
  document.getElementById("results-content").style.display     = "none";

  try {
    const res    = await fetch("/api/calculate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const result = await res.json();
    if (result.error) { showToast(result.error, "error"); return; }
    calcResults = result;
    calcData    = data;
    renderResults(result, data);
  } catch (e) {
    showToast("Calculation failed. Check your inputs.", "error");
    document.getElementById("results-loading").style.display  = "none";
    document.getElementById("results-placeholder").style.display = "flex";
  } finally {
    btn.disabled  = false;
    btn.innerHTML = "⚡ Estimate &amp; Calculate";
  }
}

// ── Render Results ─────────────────────────────────────
function renderResults(r, data) {
  document.getElementById("results-loading").style.display  = "none";
  document.getElementById("results-content").style.display = "flex";

  const { results, best, recommendation, prices, costs } = r;

  // Card title
  const parts  = [data.year, data.player_name].filter(Boolean).join(" ");
  const cardTitle = parts
    + (data.set_name    ? " – " + data.set_name    : "")
    + (data.card_number ? " #"  + data.card_number : "")
    || "Card";

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

  // ── Price cards ──
  renderPriceCards(prices);

  // ── Profit table ──
  const rows = [
    { label: "Sell Raw",       cls: "grade-raw", key: "raw"   },
    { label: "Grade → PSA 8",  cls: "grade-8",   key: "psa8"  },
    { label: "Grade → PSA 9",  cls: "grade-9",   key: "psa9"  },
    { label: "Grade → PSA 10", cls: "grade-10",  key: "psa10" },
  ];
  document.getElementById("results-tbody").innerHTML = rows.map(row => {
    const g      = results[row.key];
    const isBest = row.key === best.key;
    const star   = isBest ? ' <span class="best-badge">★ Best</span>' : "";
    return `<tr class="${isBest ? "row-best" : ""}">
      <td><span class="grade-badge ${row.cls}">${row.label}</span>${star}</td>
      <td class="fw-bold">${fmt$(g.sale_price)}</td>
      <td>${fmt$(g.cost_basis)}<div class="cell-sub">+${fmt$(g.sell_fee)} selling fees</div></td>
      <td class="${profCls(g.profit)} fw-bold">${fmt$(g.profit)}</td>
      <td class="${profCls(g.roi)} fw-bold">${fmtPct(g.roi)}</td>
    </tr>`;
  }).join("");

  // ── Formulas ──
  renderFormulas(results, costs, prices);
}

// ── Render per-grade price cards ──────────────────────
function renderPriceCards(prices) {
  const gradeOrder = [
    { key: "raw",   label: "Raw",    valCls: "" },
    { key: "psa8",  label: "PSA 8",  valCls: "text-yellow" },
    { key: "psa9",  label: "PSA 9",  valCls: "text-accent" },
    { key: "psa10", label: "PSA 10", valCls: "text-green"  },
  ];

  document.getElementById("price-cards-grid").innerHTML = gradeOrder.map(({ key, label, valCls }) => {
    const g = prices[key];
    const isLive  = g.is_live && g.comp_count >= 1;
    const srcBadge = isLive
      ? `<span class="live-badge">● Live</span>`
      : `<span class="est-badge">~ Est</span>`;
    const ccBadge = isLive
      ? `<span class="comp-count">${g.comp_count} comp${g.comp_count !== 1 ? "s" : ""}</span>`
      : "";
    const confBadge = `<span class="confidence-badge ${confCls(g.confidence)}">${capFirst(g.confidence)}</span>`;

    // Comp list (collapsed by default)
    const compRows = (g.comps || []).filter(c => c.price > 0).map(c =>
      `<tr>
        <td>${c.url ? `<a href="${c.url}" target="_blank" rel="noopener" class="comp-link">${truncate(c.title, 55)}</a>` : truncate(c.title, 55)}</td>
        <td class="fw-bold">${fmt$(c.price)}</td>
        <td class="text-muted">${c.date ? c.date.slice(0,10) : "—"}</td>
      </tr>`
    ).join("");

    const compsSection = compRows
      ? `<div class="comp-expander">
           <button class="comp-toggle" onclick="toggleComps(this)">▸ Show comps (${g.comp_count})</button>
           <div class="comp-list" style="display:none">
             <table class="comp-table">
               <thead><tr><th>Title</th><th>Price</th><th>Date</th></tr></thead>
               <tbody>${compRows}</tbody>
             </table>
             ${g.date_range !== "—" ? `<p class="comp-range">Sales: ${g.date_range}</p>` : ""}
           </div>
         </div>`
      : "";

    return `<div class="price-card">
      <div class="price-card-header">
        <span class="price-card-label">${label}</span>
        <div class="price-card-badges">${srcBadge}${ccBadge}</div>
      </div>
      <div class="price-card-value ${valCls}">${fmt$(g.median_price)}</div>
      ${g.avg_price && g.avg_price !== g.median_price
        ? `<div class="price-card-sub">avg ${fmt$(g.avg_price)} · range ${fmt$(g.low_price)}–${fmt$(g.high_price)}</div>`
        : ""}
      <div class="price-card-footer">
        ${confBadge}
        <span class="price-card-source">${g.source_name}</span>
      </div>
      ${compsSection}
    </div>`;
  }).join("");
}

function toggleComps(btn) {
  const list = btn.nextElementSibling;
  const open = list.style.display !== "none";
  list.style.display = open ? "none" : "block";
  btn.textContent    = (open ? "▸" : "▾") + btn.textContent.slice(1);
}

// ── Formulas ───────────────────────────────────────────
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
          <span class="fl-val ${profCls(raw.profit)} fw-bold">${fmt$(raw.profit)}</span>
        </div>
        <div class="formula-line formula-result">
          <span class="fl-label">ROI</span>
          <span class="fl-expr">= Profit ÷ ${fmt$(raw_buy)} × 100</span>
          <span class="fl-val ${profCls(raw.roi)} fw-bold">${fmtPct(raw.roi)}</span>
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
          <span class="fl-val ${profCls(p10.profit)} fw-bold">${fmt$(p10.profit)}</span>
        </div>
        <div class="formula-line formula-result">
          <span class="fl-label">ROI</span>
          <span class="fl-expr">= Profit ÷ ${fmt$(graded_cost)} × 100</span>
          <span class="fl-val ${profCls(p10.roi)} fw-bold">${fmtPct(p10.roi)}</span>
        </div>
      </div>
    </div>`;
}

// ── Watchlist ──────────────────────────────────────────
async function addToWatchlist() {
  if (!calcData)               { showToast("Calculate first before saving", "error"); return; }
  if (!calcData.player_name)   { showToast("Enter a player name before saving", "error"); return; }
  try {
    const res = await fetch("/api/watchlist", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(calcData),
    });
    const r = await res.json();
    if (r.success) showToast("Card saved to watchlist!", "success");
    else showToast("Failed to save card", "error");
  } catch { showToast("Error saving card", "error"); }
}

async function loadWatchlist() {
  try {
    const cards = await (await fetch("/api/watchlist")).json();
    renderWatchlist(cards);
  } catch { showToast("Failed to load watchlist", "error"); }
}

function renderWatchlist(cards) {
  const tbody = document.getElementById("watchlist-tbody");
  const empty = document.getElementById("watchlist-empty");
  if (!cards.length) { tbody.innerHTML = ""; empty.style.display = "block"; return; }
  empty.style.display = "none";
  tbody.innerHTML = cards.map(card => {
    const line = [card.year, card.player_name].filter(Boolean).join(" ")
      + (card.set_name    ? " – " + card.set_name    : "")
      + (card.card_number ? " #"  + card.card_number : "");
    return `<tr>
      <td><div class="fw-bold">${card.player_name || "—"}</div><div class="cell-sub">${line}</div></td>
      <td>${card.sport || "—"}</td>
      <td class="fw-bold">${fmt$(card.raw_price)}</td>
      <td class="fw-bold">${fmt$(card.psa10_price)}</td>
      <td class="${profCls(card.best_profit)} fw-bold">${fmt$(card.best_profit)}</td>
      <td class="${profCls(card.best_roi)} fw-bold">${fmtPct(card.best_roi)}</td>
      <td class="fw-bold fs-sm">${card.best_option || "—"}</td>
      <td><span class="badge ${recBadgeCls(card.recommendation)}">${card.recommendation}</span></td>
      <td><button class="btn btn-danger btn-sm watchlist-action" onclick="deleteCard(${card.id})">Remove</button></td>
    </tr>`;
  }).join("");
}

function recBadgeCls(rec) {
  if (rec === "Strong Buy")   return "badge-strong-buy";
  if (rec === "Possible Buy") return "badge-possible-buy";
  return "badge-avoid";
}

async function deleteCard(id) {
  try {
    await fetch(`/api/watchlist/${id}`, { method: "DELETE" });
    showToast("Card removed", "info");
    loadWatchlist();
  } catch { showToast("Error removing card", "error"); }
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
  }, 3500);
}

// ── Reset ──────────────────────────────────────────────
function resetForm() {
  document.getElementById("calc-form").reset();
  calcResults = null;
  calcData    = null;
  document.getElementById("results-placeholder").style.display = "flex";
  document.getElementById("results-loading").style.display     = "none";
  document.getElementById("results-content").style.display     = "none";
}

// ── Utilities ──────────────────────────────────────────
const capFirst = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
const truncate = (s, n) => s.length > n ? s.slice(0, n) + "…" : s;
