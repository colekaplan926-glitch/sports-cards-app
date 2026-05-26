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

  const hasMock = deals.some(d => d.is_mock);
  const mockBanner = hasMock
    ? `<div class="mock-banner" style="grid-column:1/-1">
         &#9888; Some deals show estimated (mock) pricing — connect APIFY_TOKEN or SPORTSCARDSPRO_API_KEY for real sold comp data.
       </div>`
    : "";

  grid.innerHTML = mockBanner + deals.map(dealCardHtml).join("");
}

function dealCardHtml(d) {
  const rec    = d.recommendation || "PASS";
  const recCls = rec.toLowerCase();

  const beMap = { psa8: "PSA 8", psa9: "PSA 9", psa10: "PSA 10", none: "None" };
  const breakEven = beMap[d.break_even_grade] || "N/A";

  const title = d.listing_title || "Unknown listing";
  const link  = d.listing_url
    ? `<a href="${escHtml(d.listing_url)}" target="_blank" rel="noopener" class="comp-link">${escHtml(truncate(title, 70))}</a>`
    : escHtml(truncate(title, 70));

  const mockTag   = d.is_mock ? ` <span class="est-badge">MOCK</span>` : "";
  const confBadge = `<span class="confidence-badge ${confCls(d.confidence)}">${capFirst(d.confidence || "low")}</span>`;
  const reason    = d.reason ? `<div class="text-muted fs-sm" style="margin-bottom:10px">${escHtml(d.reason)}</div>` : "";

  return `<div class="deal-card ${recCls}" id="deal-card-${d.id}">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
      <span class="deal-rec-badge deal-rec-${rec}">${rec}</span>
      <div style="display:flex;gap:6px;align-items:center">${confBadge}${mockTag}</div>
    </div>

    <div class="deal-title">${link}</div>
    <div class="deal-price-row">
      <span class="deal-price-big text-green">${fmt$(d.listing_price)}</span>
      <span class="text-muted fs-sm">listing price</span>
    </div>

    <div class="deal-values-grid">
      ${valItem("Raw",    d.raw_market,   "")}
      ${valItem("PSA 8",  d.psa8_market,  "text-yellow")}
      ${valItem("PSA 9",  d.psa9_market,  "text-accent")}
      ${valItem("PSA 10", d.psa10_market, "text-green")}
    </div>

    <div class="deal-stats-row">
      <div class="deal-stat">
        <div class="deal-stat-label">Best Profit</div>
        <div class="deal-stat-val ${profCls(d.best_profit)}">${fmt$(d.best_profit)}</div>
      </div>
      <div class="deal-stat">
        <div class="deal-stat-label">Best ROI</div>
        <div class="deal-stat-val ${profCls(d.best_roi)}">${fmtPct(d.best_roi)}</div>
      </div>
      <div class="deal-stat">
        <div class="deal-stat-label">Break Even</div>
        <div class="deal-stat-val"><span class="break-even-badge">${escHtml(breakEven)}</span></div>
      </div>
    </div>

    ${reason}

    <div class="deal-footer">
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        ${d.listing_url
          ? `<a href="${escHtml(d.listing_url)}" target="_blank" rel="noopener" class="btn btn-primary btn-sm">View Listing</a>`
          : ""}
        <button class="btn btn-outline btn-sm" onclick="expandDeal(${d.id}, this)">Details</button>
      </div>
      <button class="btn btn-outline btn-sm" onclick="dismissDeal(${d.id}, this)"
              style="color:var(--text-muted)">Dismiss</button>
    </div>

    <div id="deal-detail-${d.id}" style="display:none"></div>
  </div>`;
}

function valItem(label, price, cls) {
  return `<div class="deal-val-item">
    <div class="deal-val-label">${label}</div>
    <div class="deal-val-price ${cls}">${fmt$(price)}</div>
  </div>`;
}

// ── Deal detail expand ─────────────────────────────────
async function expandDeal(did, btn) {
  const detail = document.getElementById(`deal-detail-${did}`);
  if (detail.style.display !== "none") {
    detail.style.display = "none";
    btn.textContent = "Details";
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
      `<div style="margin-bottom:14px">
         <div class="formula-heading" style="margin-bottom:6px">${escHtml(grade)} Sold Comps</div>
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
    ).join("") || `<div class="text-muted fs-sm">No sold comps stored for this deal.</div>`;

    detail.innerHTML = `<div style="border-top:1px solid var(--border);margin-top:12px;padding-top:14px">${compSections}</div>`;
    detail.style.display = "block";
    btn.textContent = "Hide";
    btn.disabled    = false;
  } catch {
    showToast("Failed to load deal details", "error");
    btn.textContent = "Details";
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
    if (grid && !grid.querySelector(".deal-card")) {
      document.getElementById("deals-empty").style.display = "flex";
    }
  } catch {
    showToast("Failed to dismiss deal", "error");
    btn.disabled = false;
  }
}

// ── Utilities ──────────────────────────────────────────
function escHtml(str) {
  return String(str == null ? "" : str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
