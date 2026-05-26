// ── State ──────────────────────────────────────────────
let snipeFilter     = "ALL";
let editingSearchId = null;

// ── ESC closes modal ───────────────────────────────────
document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeSearchModal();
});

// ── Searches ───────────────────────────────────────────
async function loadSearches() {
  try {
    const searches = await (await fetch("/api/snipe/searches")).json();
    renderSearches(searches);
  } catch { showToast("Failed to load searches", "error"); }
}

function renderSearches(searches) {
  const list  = document.getElementById("searches-list");
  const empty = document.getElementById("searches-empty");

  if (!searches.length) {
    list.innerHTML       = "";
    empty.style.display  = "block";
    return;
  }
  empty.style.display = "none";

  list.innerHTML = searches.map(s => {
    const players   = JSON.parse(s.player_names || "[]").join(", ") || "Any player";
    const freq      = capFirst(s.frequency);
    const statusCls = s.is_active ? "active" : "paused";
    const statusTxt = s.is_active ? "Active" : "Paused";
    const deals     = s.deal_count || 0;

    return `<div class="search-item" id="search-item-${s.id}">
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span class="search-item-name">${escHtml(s.name)}</span>
          <span class="run-status ${statusCls}">${statusTxt}</span>
        </div>
        <div class="search-item-meta">
          ${freq} &middot; ${escHtml(players)} &middot; Max $${s.max_raw_price}
          &middot; Min ROI ${s.min_roi}% &middot; ${deals} deal(s)
        </div>
      </div>
      <div class="search-item-actions">
        <button class="btn btn-primary btn-sm" onclick="runSearchNow(${s.id}, this)">&#9654; Run</button>
        <button class="btn btn-outline btn-sm" onclick="openEditModal(${s.id})">Edit</button>
        <button class="btn btn-danger  btn-sm" onclick="deleteSearch(${s.id})">Delete</button>
      </div>
    </div>`;
  }).join("");
}

// ── Run search ─────────────────────────────────────────
async function runSearchNow(sid, btn) {
  btn.disabled  = true;
  btn.innerHTML = '<span class="spinner"></span>';
  try {
    const res  = await fetch(`/api/snipe/searches/${sid}/run`, { method: "POST" });
    const data = await res.json();
    if (!data.success) throw new Error(data.error || "Failed to start scan");
    showToast("Scan started…", "info");
    pollRunStatus(data.scan_run_id, btn);
  } catch (e) {
    showToast("Could not start scan: " + e.message, "error");
    btn.disabled  = false;
    btn.innerHTML = "&#9654; Run";
  }
}

async function pollRunStatus(runId, btn) {
  const maxPolls = 30; // 30 × 4 s = 2 min
  let polls = 0;

  const check = async () => {
    try {
      const run = await (await fetch(`/api/snipe/runs/${runId}/status`)).json();

      if (run.status === "done") {
        showToast(`Scan complete — ${run.deals_found} deal(s) found`, "success");
        btn.disabled  = false;
        btn.innerHTML = "&#9654; Run";
        loadSearches();
        if (document.getElementById("page-snipe-deals").classList.contains("active")) {
          loadDeals();
        }
        return;
      }

      if (run.status === "error") {
        showToast("Scan error: " + (run.error_msg || "unknown"), "error");
        btn.disabled  = false;
        btn.innerHTML = "&#9654; Run";
        return;
      }

      polls++;
      if (polls >= maxPolls) {
        btn.disabled  = false;
        btn.innerHTML = "&#9654; Run";
        showToast("Scan is taking longer than expected — check back later", "info");
        return;
      }
      setTimeout(check, 4000);
    } catch {
      btn.disabled  = false;
      btn.innerHTML = "&#9654; Run";
    }
  };

  setTimeout(check, 2000);
}

// ── Search modal ───────────────────────────────────────
function openCreateModal() {
  editingSearchId = null;
  document.getElementById("modal-title").textContent = "New Saved Search";
  document.getElementById("search-form").reset();
  document.getElementById("search-modal").style.display = "flex";
}

async function openEditModal(sid) {
  try {
    const s = await (await fetch(`/api/snipe/searches/${sid}`)).json();
    editingSearchId = sid;
    document.getElementById("modal-title").textContent = "Edit Search";

    document.getElementById("sf-name").value        = s.name;
    document.getElementById("sf-sport").value        = s.sport || "";
    document.getElementById("sf-players").value      = JSON.parse(s.player_names || "[]").join(", ");
    document.getElementById("sf-year-min").value     = s.year_min  || 2015;
    document.getElementById("sf-year-max").value     = s.year_max  || 2024;
    document.getElementById("sf-max-price").value    = s.max_raw_price   || 100;
    document.getElementById("sf-grading").value      = s.grading_cost    || 25;
    document.getElementById("sf-shipping").value     = s.shipping_cost   || 15;
    document.getElementById("sf-fee-pct").value      = s.selling_fee_pct || 12.9;
    document.getElementById("sf-min-profit").value   = s.min_profit      || 0;
    document.getElementById("sf-min-roi").value      = s.min_roi         || 20;
    document.getElementById("sf-frequency").value    = s.frequency       || "daily";

    document.getElementById("search-modal").style.display = "flex";
  } catch { showToast("Failed to load search", "error"); }
}

function closeSearchModal() {
  document.getElementById("search-modal").style.display = "none";
}

async function saveSearch() {
  const name = document.getElementById("sf-name").value.trim();
  if (!name) { showToast("Search name is required", "error"); return; }

  const playersRaw = document.getElementById("sf-players").value;
  const players    = playersRaw.split(",").map(p => p.trim()).filter(Boolean);

  const payload = {
    name,
    sport:           document.getElementById("sf-sport").value,
    player_names:    players,
    year_min:        parseInt(document.getElementById("sf-year-min").value)    || 2015,
    year_max:        parseInt(document.getElementById("sf-year-max").value)    || 2024,
    max_raw_price:   parseFloat(document.getElementById("sf-max-price").value) || 100,
    grading_cost:    parseFloat(document.getElementById("sf-grading").value)   || 25,
    shipping_cost:   parseFloat(document.getElementById("sf-shipping").value)  || 15,
    selling_fee_pct: parseFloat(document.getElementById("sf-fee-pct").value)   || 12.9,
    min_profit:      parseFloat(document.getElementById("sf-min-profit").value)|| 0,
    min_roi:         parseFloat(document.getElementById("sf-min-roi").value)   || 20,
    frequency:       document.getElementById("sf-frequency").value,
  };

  try {
    const url    = editingSearchId ? `/api/snipe/searches/${editingSearchId}` : "/api/snipe/searches";
    const method = editingSearchId ? "PUT" : "POST";
    const r      = await (await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })).json();
    if (!r.success) throw new Error(r.error || "Save failed");
    showToast(editingSearchId ? "Search updated" : "Search created", "success");
    closeSearchModal();
    loadSearches();
  } catch (e) { showToast("Save failed: " + e.message, "error"); }
}

async function deleteSearch(sid) {
  if (!confirm("Delete this search and all its deals?")) return;
  try {
    await fetch(`/api/snipe/searches/${sid}`, { method: "DELETE" });
    showToast("Search deleted", "info");
    loadSearches();
  } catch { showToast("Delete failed", "error"); }
}

// ── Deals ──────────────────────────────────────────────
function setDealsFilter(filter) {
  snipeFilter = filter;
  document.querySelectorAll(".filter-tab").forEach(t => {
    t.classList.toggle("active", t.dataset.filter === filter);
  });
  loadDeals();
}

async function loadDeals() {
  const params = new URLSearchParams();
  if (snipeFilter !== "ALL") params.set("recommendation", snipeFilter);

  try {
    const deals = await (await fetch("/api/snipe/deals?" + params)).json();
    renderDealCards(deals);
  } catch { showToast("Failed to load deals", "error"); }
}

function renderDealCards(deals) {
  const grid  = document.getElementById("deals-grid");
  const empty = document.getElementById("deals-empty");

  if (!deals.length) {
    grid.innerHTML      = "";
    empty.style.display = "flex";
    return;
  }
  empty.style.display = "none";
  grid.innerHTML = deals.map(dealCardHtml).join("");
}

// ── CG score ring class ────────────────────────────────
function cgRingClass(score) {
  const n = parseInt(score) || 0;
  if (n >= 85) return "cg-elite";
  if (n >= 70) return "cg-solid";
  if (n >= 55) return "cg-possible";
  if (n >= 40) return "cg-risky";
  if (n >  0)  return "cg-avoid";
  return "cg-nodata";
}

// ── Deal card v2 ───────────────────────────────────────
function dealCardHtml(d) {
  const rec     = d.recommendation || "PASS";
  const recCls  = rec.toLowerCase();
  const pillCls = { "BUY": "strong-buy", "WATCH": "possible", "PASS": "pass" }[rec] || "pass";

  // CG score circle
  const cg      = parseInt(d.cardgrade_score) || 0;
  const cgCircle = `<span class="cg-score-sm ${cgRingClass(cg)}" title="CardGrade Score">${cg > 0 ? cg : "—"}</span>`;

  // Confidence chip
  const confBadge = `<span class="confidence-badge ${confCls(d.confidence)}">${capFirst(d.confidence || "low")}</span>`;

  // Title with link
  const title    = d.listing_title || "Unknown listing";
  const titleHtml = d.listing_url
    ? `<a href="${escHtml(d.listing_url)}" target="_blank" rel="noopener" class="comp-link">${escHtml(truncate(title, 75))}</a>`
    : escHtml(truncate(title, 75));

  // Best market price across grades
  const bestMarket = Math.max(
    parseFloat(d.psa10_market) || 0,
    parseFloat(d.psa9_market)  || 0,
    parseFloat(d.psa8_market)  || 0,
    parseFloat(d.raw_market)   || 0
  );

  // Break-even chip
  const beMap   = { psa8: "PSA 8+", psa9: "PSA 9+", psa10: "PSA 10 only" };
  const beLabel = beMap[d.break_even_grade];
  const beChip  = beLabel
    ? `<span class="chip ${d.break_even_grade === "psa10" ? "chip-yellow" : "chip-green"}">Break-even ${escHtml(beLabel)}</span>`
    : `<span class="chip">No break-even grade</span>`;

  // Search name chip
  const searchChip = d.search_name
    ? `<span class="chip chip-accent">${escHtml(truncate(d.search_name, 22))}</span>`
    : "";

  return `<div class="deal-card-v2 ${recCls}" id="deal-card-${d.id}">
    <div class="deal-header-v2">
      <span class="rec-pill-new ${pillCls}">${escHtml(rec)}</span>
      ${confBadge}
      ${cgCircle}
      <span style="margin-left:auto;display:flex;gap:4px;align-items:center">${searchChip}</span>
    </div>

    <div class="deal-title-v2">${titleHtml}</div>

    <div class="deal-metrics-v2">
      <div class="deal-metric-item">
        <div class="deal-metric-label">Listing</div>
        <div class="deal-metric-val">${fmt$(d.listing_price)}</div>
      </div>
      <div class="deal-metric-item">
        <div class="deal-metric-label">Best Market</div>
        <div class="deal-metric-val text-accent">${fmt$(bestMarket)}</div>
      </div>
      <div class="deal-metric-item">
        <div class="deal-metric-label">Best ROI</div>
        <div class="deal-metric-val ${profCls(d.best_roi)}">${fmtPct(d.best_roi)}</div>
      </div>
      <div class="deal-metric-item">
        <div class="deal-metric-label">Profit</div>
        <div class="deal-metric-val ${profCls(d.best_profit)}">${fmt$(d.best_profit)}</div>
      </div>
    </div>

    <div class="deal-chips-v2">
      ${beChip}
      ${d.reason ? `<span class="chip fs-sm" style="font-size:10px;color:var(--text-muted)">${escHtml(truncate(d.reason, 70))}</span>` : ""}
    </div>

    <div id="deal-comps-${d.id}" style="display:none"></div>

    <div class="deal-actions-v2">
      ${d.listing_url
        ? `<a href="${escHtml(d.listing_url)}" target="_blank" rel="noopener" class="btn btn-primary btn-sm">View Listing</a>`
        : ""}
      <button class="btn btn-outline btn-sm" onclick="expandDeal(${d.id}, this)">View Comps</button>
      <button class="btn btn-outline btn-sm" onclick="dismissDeal(${d.id}, this)"
              style="margin-left:auto;color:var(--text-muted)">Dismiss</button>
    </div>
  </div>`;
}

// ── Deal comps expand ──────────────────────────────────
async function expandDeal(did, btn) {
  const detail = document.getElementById(`deal-comps-${did}`);
  if (detail.style.display !== "none") {
    detail.style.display = "none";
    btn.textContent = "View Comps";
    return;
  }

  btn.textContent = "Loading…";
  btn.disabled    = true;
  try {
    const deal  = await (await fetch(`/api/snipe/deals/${did}`)).json();
    const comps = deal.comps || [];

    const byGrade = {};
    for (const c of comps) {
      if (!byGrade[c.grade]) byGrade[c.grade] = [];
      byGrade[c.grade].push(c);
    }

    const compSections = Object.entries(byGrade).map(([grade, cs]) =>
      `<div class="comp-grade-section">
         <div class="comp-grade-label">${escHtml(grade)} Sold Comps</div>
         <table class="comp-table">
           <thead><tr><th>Title</th><th>Price</th><th>Date</th><th>Source</th></tr></thead>
           <tbody>${cs.map(c =>
             `<tr>
               <td>${c.url
                 ? `<a href="${escHtml(c.url)}" target="_blank" rel="noopener" class="comp-link">${escHtml(truncate(c.title || "", 50))}</a>`
                 : escHtml(truncate(c.title || "", 50))}</td>
               <td class="fw-bold">${fmt$(c.price)}</td>
               <td class="text-muted">${c.sale_date ? c.sale_date.slice(0,10) : "—"}</td>
               <td class="text-muted">${escHtml(c.source || "—")}</td>
             </tr>`).join("")}
           </tbody>
         </table>
       </div>`
    ).join("") || `<div class="no-comps-msg">No sold comps stored for this deal.</div>`;

    detail.innerHTML = `<div style="border-top:1px solid var(--border);margin-top:8px;padding-top:12px">${compSections}</div>`;
    detail.style.display = "block";
    btn.textContent = "Hide Comps";
    btn.disabled    = false;
  } catch {
    showToast("Failed to load deal details", "error");
    btn.textContent = "View Comps";
    btn.disabled    = false;
  }
}

// ── Dismiss deal ───────────────────────────────────────
async function dismissDeal(did, btn) {
  if (!confirm("Dismiss this deal?")) return;
  btn.disabled = true;
  try {
    await fetch(`/api/snipe/deals/${did}/dismiss`, { method: "POST" });
    const card = document.getElementById(`deal-card-${did}`);
    if (card) card.remove();
    showToast("Deal dismissed", "info");
    const grid = document.getElementById("deals-grid");
    if (grid && !grid.querySelector(".deal-card-v2")) {
      document.getElementById("deals-empty").style.display = "flex";
    }
  } catch {
    showToast("Failed to dismiss deal", "error");
    btn.disabled = false;
  }
}

// escHtml, fmt$, fmtPct, profCls, confCls, capFirst, truncate — defined in app.js (loaded first)
