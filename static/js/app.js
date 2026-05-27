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
  if (isNaN(n) || v == null) return "$0";
  return (n < 0 ? "-$" : "$") + Math.abs(n).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
};
const fmtPct  = v => { const n = parseFloat(v); return isNaN(n) ? "0.0%" : n.toFixed(1) + "%"; };
const profCls = v => parseFloat(v) >= 0 ? "text-green" : "text-red";
const confCls = c => ({ high: "conf-high", medium: "conf-medium", low: "conf-low" }[c] || "conf-low");
const capFirst= s => s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
const truncate= (s, n) => s && s.length > n ? s.slice(0, n) + "…" : (s || "");

function escHtml(str) {
  return String(str == null ? "" : str)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

// ── Expandable sections ────────────────────────────────
function toggleSection(btn) {
  const body = btn.nextElementSibling;
  const open = body.style.display !== "none";
  body.style.display = open ? "none" : "block";
  btn.classList.toggle("open", !open);
}

// ── Search links (auto-update from card details) ───────
function updateSearchLinks() {
  const player  = (document.getElementById("player_name")?.value || "").trim();
  const year    = (document.getElementById("year")?.value || "").trim();
  const setName = (document.getElementById("set_name")?.value || "").trim();
  const num     = (document.getElementById("card_number")?.value || "").trim();

  const parts = [year, player, setName, num ? "#" + num : ""].filter(Boolean);
  const q     = parts.join(" ");

  const ebayEl   = document.getElementById("link-ebay");
  const pt130El  = document.getElementById("link-130pt");
  const googleEl = document.getElementById("link-google");
  if (!ebayEl) return;

  if (!q) {
    ebayEl.href   = "https://www.ebay.com/sch/i.html?_nkw=sports+card+PSA&LH_Sold=1&LH_Complete=1&_sop=13";
    pt130El.href  = "https://www.130point.com/";
    googleEl.href = "https://www.google.com/search?q=sports+card+PSA+sold";
    return;
  }

  const gradeQ = q + " PSA";
  ebayEl.href   = "https://www.ebay.com/sch/i.html?" +
    new URLSearchParams({ _nkw: gradeQ, LH_Sold: "1", LH_Complete: "1", _sop: "13" });
  pt130El.href  = "https://www.130point.com/sales/?" + new URLSearchParams({ q });
  googleEl.href = "https://www.google.com/search?" + new URLSearchParams({ q: gradeQ + " sold" });
}

// ── Calculate ──────────────────────────────────────────
async function calculate() {
  const fields = [
    "player_name","year","set_name","card_number","sport","variation",
    "raw_price","grading_cost","shipping_fees","selling_fee_pct",
    "comps_raw","comps_psa8","comps_psa9","comps_psa10",
  ];
  const data = {};
  for (const f of fields) {
    const el = document.getElementById(f);
    if (el) data[f] = el.value;
  }
  if (!data.raw_price || parseFloat(data.raw_price) <= 0) {
    showToast("Enter a raw purchase price", "error"); return;
  }

  const btn = document.getElementById("calc-btn");
  btn.disabled  = true;
  btn.innerHTML = "Calculating…";

  document.getElementById("results-placeholder").style.display = "none";
  document.getElementById("results-loading").style.display     = "flex";
  document.getElementById("results-content").style.display     = "none";

  try {
    const res    = await fetch("/api/calculate", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(data),
    });
    const result = await res.json();
    if (result.error) { showToast(result.error, "error"); return; }
    calcResults = result; calcData = data;
    renderResults(result, data);
  } catch {
    showToast("Calculation failed.", "error");
    document.getElementById("results-loading").style.display     = "none";
    document.getElementById("results-placeholder").style.display = "flex";
  } finally {
    btn.disabled  = false;
    btn.innerHTML = "⚡ Calculate";
  }
}

// ── Main render ────────────────────────────────────────
function renderResults(r, data) {
  document.getElementById("results-loading").style.display  = "none";
  document.getElementById("results-content").style.display = "flex";

  renderSignalCard(r, data);
  renderGradeChips(r.prices, r.results, r.best, r.has_real_data);
  renderAnalysisTable(r.results, r.best, r.has_real_data, r.prices);
  renderCompsSection(r.prices);
  renderFormulas(r.results, r.costs);
}

// ── Signal card ────────────────────────────────────────
function renderSignalCard(r, data) {
  const { recommendation, best, has_real_data,
          break_even_grade, break_even_label,
          cardgrade_score: cg, set_risk, signal, prices } = r;

  const ring = document.getElementById("cg-ring");
  ring.className = `cg-ring ${cg.cls}`;
  document.getElementById("cg-score-num").textContent = has_real_data ? cg.score : "—";

  const pill = document.getElementById("signal-rec-pill");
  const pillCls = {
    "Strong Buy":       "strong-buy",
    "Possible Buy":     "possible",
    "Avoid":            "pass",
    "Insufficient Data":"insuf",
  }[recommendation] || "insuf";
  pill.className   = `rec-pill-new ${pillCls}`;
  pill.textContent = recommendation;

  const parts     = [data.year, data.player_name].filter(Boolean).join(" ");
  const variation = data.variation && data.variation !== "Base" ? ` · ${data.variation}` : "";
  const cardTitle = (parts + (data.set_name ? ` – ${data.set_name}` : "")
    + (data.card_number ? ` #${data.card_number}` : "") + variation) || "Card";
  document.getElementById("signal-sub").textContent  = escHtml(cardTitle);
  document.getElementById("signal-text").textContent = signal || "";

  const roiEl  = document.getElementById("signal-roi-num");
  const profEl = document.getElementById("signal-profit-sub");
  if (has_real_data) {
    roiEl.textContent  = fmtPct(best.roi);
    roiEl.className    = "signal-roi-num " + profCls(best.roi);
    profEl.textContent = `${best.label} · ${fmt$(best.profit)} profit`;
  } else {
    roiEl.textContent  = "—";
    roiEl.className    = "signal-roi-num text-muted";
    profEl.textContent = "No comps entered";
  }

  const chips = [];

  if (has_real_data && break_even_label) {
    const beColor = break_even_grade === "psa10" ? "chip-yellow" : "chip-green";
    chips.push(`<span class="chip ${beColor}">
      ${break_even_grade === "psa10" ? "⚠" : "✅"} Break-even ${break_even_label}+
    </span>`);
  }

  const liveGrades = ["raw","psa8","psa9","psa10"].filter(k => prices[k].is_live && prices[k].comp_count > 0);
  if (liveGrades.length) {
    const totalComps = liveGrades.reduce((s,k) => s + prices[k].comp_count, 0);
    chips.push(`<span class="chip chip-accent">${totalComps} sold comp${totalComps !== 1 ? "s" : ""} entered</span>`);
  }

  if (has_real_data && cg.score > 0) {
    chips.push(`<span class="chip">${cg.label}</span>`);
  }

  if (set_risk && set_risk.label) {
    const rCls = set_risk.color === "yellow" ? "chip-yellow" : "";
    chips.push(`<span class="chip ${rCls}">${escHtml(set_risk.label)}</span>`);
  }

  if (!has_real_data) {
    chips.push(`<span class="chip chip-yellow">⚠ No verified comps entered — add sold prices above</span>`);
  }

  document.getElementById("signal-chips").innerHTML = chips.join("");
}

// ── Grade chips row ────────────────────────────────────
function renderGradeChips(prices, results, best, hasRealData) {
  const grades = [
    { key:"raw",   label:"Raw",    valCls:"" },
    { key:"psa8",  label:"PSA 8",  valCls:"text-yellow" },
    { key:"psa9",  label:"PSA 9",  valCls:"text-accent" },
    { key:"psa10", label:"PSA 10", valCls:"text-green" },
  ];
  document.getElementById("grade-chips-row").innerHTML = grades.map(({ key, label, valCls }) => {
    const g      = prices[key];
    const r      = results[key];
    const isLive = g.is_live && g.comp_count >= 1;
    const dot    = isLive
      ? `<span class="live-dot" title="${g.comp_count} comps"></span>`
      : `<span class="est-dot" title="No comps entered"></span>`;
    const isBest = key === best.key && hasRealData && isLive;
    const border = isBest ? "style='border-color:var(--accent)'" : "";
    const priceHtml = isLive
      ? `<div class="grade-chip-value ${valCls}">${fmt$(g.median_price)}</div>`
      : `<div class="grade-chip-value text-muted" style="font-size:11px">No comps</div>`;
    const profitHtml = isLive
      ? `<div class="grade-chip-profit ${profCls(r.profit)}">${r.profit >= 0 ? "+" : ""}${fmt$(r.profit)}</div>`
      : `<div class="grade-chip-profit text-muted">—</div>`;
    return `<div class="grade-chip-v2" ${border}>
      <div class="grade-chip-header">${dot}<span class="grade-chip-label">${label}</span></div>
      ${priceHtml}
      ${profitHtml}
      <div class="grade-chip-conf"><span class="confidence-badge ${confCls(g.confidence)}">${capFirst(g.confidence)}</span></div>
    </div>`;
  }).join("");
}

// ── Analysis table (expandable) ────────────────────────
function renderAnalysisTable(results, best, hasRealData, prices) {
  const rows = [
    { label:"Sell Raw",       cls:"grade-raw", key:"raw"   },
    { label:"Grade → PSA 8",  cls:"grade-8",   key:"psa8"  },
    { label:"Grade → PSA 9",  cls:"grade-9",   key:"psa9"  },
    { label:"Grade → PSA 10", cls:"grade-10",  key:"psa10" },
  ];
  document.getElementById("results-tbody").innerHTML = rows.map(row => {
    const g      = results[row.key];
    const p      = prices ? prices[row.key] : null;
    const isLive = p && p.is_live && p.comp_count >= 1;
    const isBest = row.key === best.key && hasRealData && isLive;
    const star   = isBest ? ' <span class="best-badge">★ Best</span>' : "";
    if (!isLive) {
      return `<tr style="opacity:0.45">
        <td><span class="grade-badge ${row.cls}">${row.label}</span></td>
        <td class="text-muted fs-sm">No comps</td>
        <td class="text-muted fs-sm">—</td>
        <td class="text-muted fs-sm">—</td>
        <td class="text-muted fs-sm">—</td>
      </tr>`;
    }
    return `<tr class="${isBest ? "row-best" : ""}">
      <td><span class="grade-badge ${row.cls}">${row.label}</span>${star}</td>
      <td class="fw-bold">${fmt$(g.sale_price)}</td>
      <td>${fmt$(g.cost_basis)}<div class="cell-sub">+${fmt$(g.sell_fee)} fees</div></td>
      <td class="${profCls(g.profit)} fw-bold">${fmt$(g.profit)}</td>
      <td class="${profCls(g.roi)} fw-bold">${fmtPct(g.roi)}</td>
    </tr>`;
  }).join("");
}

// ── Comps section (expandable) ─────────────────────────
function renderCompsSection(prices) {
  const gradeOrder  = ["raw","psa8","psa9","psa10"];
  const gradeLabels = { raw:"Raw", psa8:"PSA 8", psa9:"PSA 9", psa10:"PSA 10" };
  let html = "";

  for (const key of gradeOrder) {
    const g      = prices[key];
    const isLive = g.is_live && g.comp_count >= 1;

    if (!isLive) {
      html += `<div class="comp-grade-section">
        <div class="comp-grade-label">${gradeLabels[key]}</div>
        <div class="no-comps-msg">No verified comps entered.</div>
      </div>`;
      continue;
    }

    // User-entered comps: show compact stats + price list
    const comps  = (g.comps || []).filter(c => c.price > 0);
    const prices_list = comps.map(c => fmt$(c.price)).join(" · ");
    const hi = fmt$(g.high_price || comps[comps.length - 1]?.price || 0);
    const lo = fmt$(g.low_price  || comps[0]?.price || 0);

    html += `<div class="comp-grade-section">
      <div class="comp-grade-label">
        ${gradeLabels[key]}
        <span class="source-badge source-scp">User Entered</span>
        <span class="comp-count">${g.comp_count} comp${g.comp_count !== 1 ? "s" : ""}</span>
      </div>
      <div class="user-comps-stats">
        <span>Median <strong>${fmt$(g.median_price)}</strong></span>
        <span class="text-muted">·</span>
        <span>Range <strong>${lo} – ${hi}</strong></span>
        <span class="text-muted">·</span>
        <span class="confidence-badge ${confCls(g.confidence)}">${capFirst(g.confidence)} confidence</span>
      </div>
      <div class="user-comps-prices">${prices_list}</div>
    </div>`;
  }

  document.getElementById("comps-by-grade").innerHTML = html ||
    `<div class="text-muted fs-sm">No comps entered.</div>`;
}

// ── Formulas (expandable) ──────────────────────────────
function renderFormulas(results, costs) {
  const { raw_price: raw_buy, grading, shipping, fee_pct, graded_cost } = costs;
  const raw = results.raw;
  const p10 = results.psa10;

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
          <span class="fl-expr">= Raw Market × ${fee_pct}%</span>
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
          <span class="fl-expr">= PSA 10 × ${fee_pct}%</span>
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
  if (!calcData)             { showToast("Calculate first", "error"); return; }
  if (!calcData.player_name) { showToast("Enter a player name first", "error"); return; }
  try {
    const res = await fetch("/api/watchlist", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(calcData),
    });
    const r = await res.json();
    if (r.success) showToast("Saved to watchlist!", "success");
    else showToast("Failed to save", "error");
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
  tbody.innerHTML = cards.map(c => {
    const line = [c.year, c.player_name].filter(Boolean).join(" ")
      + (c.set_name    ? " – " + c.set_name    : "")
      + (c.card_number ? " #"  + c.card_number : "");
    return `<tr>
      <td><div class="fw-bold">${escHtml(c.player_name||"—")}</div><div class="cell-sub">${escHtml(line)}</div></td>
      <td>${escHtml(c.sport||"—")}</td>
      <td class="fw-bold">${fmt$(c.raw_price)}</td>
      <td class="fw-bold">${fmt$(c.psa10_price)}</td>
      <td class="${profCls(c.best_profit)} fw-bold">${fmt$(c.best_profit)}</td>
      <td class="${profCls(c.best_roi)} fw-bold">${fmtPct(c.best_roi)}</td>
      <td class="fw-bold fs-sm">${escHtml(c.best_option||"—")}</td>
      <td><span class="badge ${recBadgeCls(c.recommendation)}">${escHtml(c.recommendation)}</span></td>
      <td><button class="btn btn-danger btn-sm watchlist-action" onclick="deleteCard(${c.id})">Remove</button></td>
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
    await fetch(`/api/watchlist/${id}`, { method:"DELETE" });
    showToast("Card removed", "info"); loadWatchlist();
  } catch { showToast("Error removing card", "error"); }
}

// ── Toast ──────────────────────────────────────────────
function showToast(msg, type = "info") {
  const c = document.getElementById("toast-container");
  const t = document.createElement("div");
  t.className   = `toast ${type}`;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => {
    t.style.animation = "slideOut 0.3s ease forwards";
    setTimeout(() => t.remove(), 300);
  }, 3500);
}

// ── Reset ──────────────────────────────────────────────
function resetForm() {
  document.getElementById("calc-form").reset();
  calcResults = null; calcData = null;
  document.getElementById("results-placeholder").style.display = "flex";
  document.getElementById("results-loading").style.display     = "none";
  document.getElementById("results-content").style.display     = "none";
  updateSearchLinks();
}
