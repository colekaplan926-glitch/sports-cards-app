// ── State ──────────────────────────────────────────────
let calcResults  = null;
let calcData     = null;
let _pollTimer   = null;
let _knownCompTs = {};
let _parsedComps = [];
let _parseDebounce = null;

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

function escHtml(str) {
  return String(str == null ? "" : str)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function toggleSection(btn) {
  const body = btn.nextElementSibling;
  const open = body.style.display !== "none";
  body.style.display = open ? "none" : "block";
  btn.classList.toggle("open", !open);
}

// ── Read card detail inputs ────────────────────────────
function getCardData() {
  const fields = [
    "player_name","year","set_name","card_number","sport","variation",
    "raw_price","grading_cost","shipping_fees","selling_fee_pct",
  ];
  const d = {};
  for (const f of fields) {
    const el = document.getElementById(f);
    if (el) d[f] = el.value;
  }
  return d;
}

// ── Per-grade search link updater ──────────────────────
function updateCompLinks() {
  const player    = (document.getElementById("player_name")?.value  || "").trim();
  const year      = (document.getElementById("year")?.value         || "").trim();
  const setName   = (document.getElementById("set_name")?.value     || "").trim();
  const num       = (document.getElementById("card_number")?.value  || "").trim();
  const variation = (document.getElementById("variation")?.value    || "").trim();

  const base = [year, player, setName,
                (variation && variation !== "Base") ? variation : "",
                num ? "#" + num : ""]
    .filter(Boolean).join(" ");

  if (!base) return;

  const gradeQ = {
    raw:   base,
    psa8:  base + " PSA 8",
    psa9:  base + " PSA 9",
    psa10: base + " PSA 10",
  };

  for (const [g, q] of Object.entries(gradeQ)) {
    const setA = (id, href) => { const el = document.getElementById(id); if (el && el.tagName === "A") el.href = href; };
    setA(`link-${g}-130`,
        "https://www.130point.com/sales/?" + new URLSearchParams({q}));
    setA(`link-${g}-google`,
        "https://www.google.com/search?" + new URLSearchParams({q: q + " sold"}));
  }
}

// ── Parser helpers ─────────────────────────────────────
const _BULK_EXCLUDE = ["lot of"," lot ","lots of","reprint","custom card",
                       "fake ","proxy","redemption","blank back","commemorative"];

const _OTHER_GRADER_PAT = /\b(bgs|sgc|cgc|beckett|hga|gma)\b/i;
const _AUTO_KW_PAT      = /\b(auto|autograph|signed)\b/i;
const _SHIP_KW_PAT      = /\bshipping\b|\bpostage\b|\bhandling\b/i;
// eBay condition strings between title and price — transparent, don't reset grade context
const _EBAY_COND_PAT    = /^(?:new(?:\s*\([^)]{0,50}\))?|pre.?owned|used|brand\s+new|like\s+new|very\s+good\+?|good\+?|fair|acceptable|for\s+parts|not\s+working|open\s+box|seller\s+refurbished|graded|ungraded)\s*$/i;
const _GRADE10_PAT      = /\bpsa\s*(?:gem\s*mint\s*)?10\b|\bgem\s*mt\s*10\b|\bgrade\s*10\b/i;
const _GRADE9_PAT       = /\bpsa\s*9(?!\d)|\bmint\s*9\b|\bgrade\s*9\b/i;
const _GRADE8_PAT       = /\bpsa\s*8(?!\d)|\bnm.?mt\s*8\b|\bgrade\s*8\b/i;
const _ANY_PSA_PAT      = /\bpsa\s*\d+/i;
const _DOLLAR_PAT       = /(?:US\s*)?\$\s*([0-9,]+(?:\.\d{1,2})?)/i;
const _SALE_PAT         = /(?:sold(?:\s+for)?|price\s*:|accepted(?:\s+for)?|final\s+price\s*:?)\s+([0-9][0-9,]*(?:\.\d{2})?)\b/i;

function _extractDollarPrice(line) {
  let m = line.match(_DOLLAR_PAT);
  if (m) {
    const v = parseFloat(m[1].replace(/,/g, ""));
    return (v > 0.5 && v < 500000) ? v : null;
  }
  m = line.match(_SALE_PAT);
  if (m) {
    const v = parseFloat(m[1].replace(/,/g, ""));
    return (v > 0.5 && v < 500000) ? v : null;
  }
  return null;
}

function _detectGrade(ll) {
  // Returns 'psa10'/'psa9'/'psa8'/'SKIP'/null
  if (_OTHER_GRADER_PAT.test(ll)) return "SKIP";
  const isAutoCard = (document.getElementById("variation")?.value || "").toLowerCase().includes("auto");
  if (!isAutoCard && _AUTO_KW_PAT.test(ll)) return "SKIP";
  if (_BULK_EXCLUDE.some(kw => ll.includes(kw))) return "SKIP";
  if (_GRADE10_PAT.test(ll)) return "psa10";
  if (_GRADE9_PAT.test(ll))  return "psa9";
  if (_GRADE8_PAT.test(ll))  return "psa8";
  if (_ANY_PSA_PAT.test(ll)) return "SKIP"; // PSA 7/6/5 etc
  return null;
}

function _isBarePriceLine(line) {
  let s = line.replace(/(?:US\s*)?\$\s*[0-9,]+(?:\.\d{1,2})?/gi, "");
  s = s.replace(/(?:sold(?:\s+for)?|price\s*:|accepted(?:\s+for)?|final\s+price\s*:?)\s+[0-9][0-9,]*(?:\.\d{2})?/gi, "");
  return s.trim().length < 12;
}

function _isTrivialLine(ll) {
  const s = ll
    .replace(/\b(?:sold|for|free|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|pm|am|the|and|or|new|used|pre|owned|other|like|brand|very|good|fair)\b|\d+/gi, "")
    .replace(/[^a-z]/gi, "");
  return s.length < 5;
}

function _parseBulkText(text) {
  const result = { raw: [], psa8: [], psa9: [], psa10: [] };
  const parsedComps = [];
  const lines = text.split(/\r?\n/);
  let prevGrade = null;

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;
    const ll = line.toLowerCase();

    // Hard exclusions — skip line and reset grade context
    if (_BULK_EXCLUDE.some(kw => ll.includes(kw))) { prevGrade = null; continue; }
    const isAutoCard = (document.getElementById("variation")?.value || "").toLowerCase().includes("auto");
    if (!isAutoCard && _AUTO_KW_PAT.test(ll)) { prevGrade = null; continue; }
    if (_OTHER_GRADER_PAT.test(ll)) { prevGrade = null; continue; }

    // Transparent status lines — skip without touching grade context
    if (_SHIP_KW_PAT.test(ll)) continue;
    if (_EBAY_COND_PAT.test(ll)) continue; // "New (Other)", "Pre-Owned", "Used" …

    const gradeOnLine = _detectGrade(ll);
    if (gradeOnLine === "SKIP") { prevGrade = null; continue; }

    const price = _extractDollarPrice(line);
    if (price !== null) {
      const bare = _isBarePriceLine(line);
      let grade;
      if (gradeOnLine) {
        grade     = gradeOnLine;
        prevGrade = gradeOnLine;
      } else if (bare && prevGrade !== null) {
        grade = prevGrade; // bare price borrows grade from previous title
      } else {
        grade = "raw";
        if (!bare) prevGrade = null;
      }
      result[grade].push(price);
      parsedComps.push({ grade, price, line: line.slice(0, 80) });
    } else {
      if (gradeOnLine) {
        prevGrade = gradeOnLine;
      } else if (!_isTrivialLine(ll)) {
        prevGrade = null; // substantive listing title resets context
      }
    }
  }

  return { result, parsedComps };
}

function _applyIQR(prices) {
  if (prices.length < 4) return prices;
  const s = [...prices].sort((a, b) => a - b);
  const n = s.length;
  const q1 = s[Math.floor(n / 4)], q3 = s[Math.floor(3 * n / 4)];
  const iqr = q3 - q1;
  if (iqr <= 0) return prices;
  return prices.filter(p => p >= q1 - 1.5 * iqr && p <= q3 + 1.5 * iqr);
}

function _median(arr) {
  if (!arr.length) return 0;
  const s = [...arr].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function _setBadge(grade, count, median) {
  const badge = document.getElementById("status-" + grade);
  if (!badge) return;
  if (count > 0) {
    const labels = { raw: "Raw", psa8: "PSA 8", psa9: "PSA 9", psa10: "PSA 10" };
    badge.textContent = `${labels[grade]}: ${count} comp${count !== 1 ? "s" : ""} · ${fmt$(median)}`;
    badge.className   = "comp-status-badge found";
    badge.style.display = "";
  } else {
    badge.style.display = "none";
    badge.textContent   = "";
  }
}

// ── Debounced auto-parse ───────────────────────────────
function scheduleParse() {
  if (_parseDebounce) clearTimeout(_parseDebounce);
  _parseDebounce = setTimeout(_runAutoparse, 400);
}

function _runAutoparse() {
  _parseDebounce = null;
  const textarea = document.getElementById("bulk-paste-area");
  const text = (textarea?.value || "").trim();
  if (!text) { clearBulkImport(); return; }

  const { result, parsedComps } = _parseBulkText(text);
  _parsedComps = parsedComps;
  _applyResultToUI(result, parsedComps, /*autoCalc=*/false);
}

function _applyResultToUI(result, parsedComps, autoCalc) {
  const detRow    = document.getElementById("comp-detection-row");
  const summaryEl = document.getElementById("bulk-parse-summary");
  let anyFound = false;
  const summaryParts = [];
  const gradeLabels  = { raw:"Raw", psa8:"PSA 8", psa9:"PSA 9", psa10:"PSA 10" };

  for (const [grade, rawPrices] of Object.entries(result)) {
    const prices = _applyIQR(rawPrices);
    const inp    = document.getElementById("comps_" + grade);
    if (prices.length > 0) {
      const med = _median(prices);
      const csv = prices.map(p => Number.isInteger(p) ? p : p.toFixed(2)).join(", ");
      if (inp) inp.value = csv;
      _setBadge(grade, prices.length, med);
      summaryParts.push(`${prices.length} ${gradeLabels[grade]}`);
      anyFound = true;
    } else {
      if (inp) inp.value = "";
      _setBadge(grade, 0, 0);
    }
  }

  if (detRow) detRow.style.display = anyFound ? "" : "none";
  renderCompPreview(parsedComps);

  if (!anyFound) {
    if (summaryEl) {
      summaryEl.textContent = "No valid sold prices detected. Prices must include a $ sign — e.g. \"PSA 9 $430\" or \"Sold $1,245\".";
      summaryEl.style.display = "";
    }
    return;
  }

  if (summaryEl) {
    summaryEl.textContent = "Detected: " + summaryParts.join("  ·  ");
    summaryEl.style.display = "";
  }

  if (autoCalc) {
    const rawPrice = parseFloat(document.getElementById("raw_price")?.value || 0);
    if (rawPrice > 0) calculate();
    else showToast("Comps loaded! Enter a raw purchase price and click ⚡ Calculate.", "success");
  }
}

async function importBulkComps() {
  const textarea = document.getElementById("bulk-paste-area");
  const text     = (textarea?.value || "").trim();
  if (!text) { showToast("Paste sold listings first", "error"); return; }

  const { result, parsedComps } = _parseBulkText(text);
  _parsedComps = parsedComps;

  const totalFound = Object.values(result).reduce((s, arr) => s + arr.length, 0);
  if (!totalFound) {
    _applyResultToUI(result, parsedComps, false);
    showToast("No valid sold prices — include $ sign with prices", "error");
    return;
  }

  // Show preview — user reviews and removes bad comps, then clicks Calculate
  _applyResultToUI(result, parsedComps, /*autoCalc=*/false);
  showToast(`${totalFound} comp${totalFound !== 1 ? "s" : ""} parsed — review below, then click ⚡ Calculate`, "success");

  // Scroll the preview into view
  const preview = document.getElementById("comp-preview-area");
  if (preview) preview.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function importFromClipboard() {
  try {
    const text     = await navigator.clipboard.readText();
    const textarea = document.getElementById("bulk-paste-area");
    if (textarea) textarea.value = text;
    await importBulkComps();
  } catch {
    showToast("Clipboard access denied — paste manually into the box above", "error");
  }
}

function clearBulkImport() {
  const ta = document.getElementById("bulk-paste-area");
  if (ta) ta.value = "";
  _parsedComps = [];
  const previewArea = document.getElementById("comp-preview-area");
  if (previewArea) { previewArea.style.display = "none"; previewArea.innerHTML = ""; }
  const summaryEl = document.getElementById("bulk-parse-summary");
  if (summaryEl) { summaryEl.style.display = "none"; summaryEl.textContent = ""; }
  const detRow = document.getElementById("comp-detection-row");
  if (detRow) detRow.style.display = "none";
  for (const g of ["raw","psa8","psa9","psa10"]) {
    _setBadge(g, 0, 0);
    const inp = document.getElementById("comps_" + g);
    if (inp) inp.value = "";
  }
}

// ── Comp preview table ─────────────────────────────────
function renderCompPreview(comps) {
  const area = document.getElementById("comp-preview-area");
  if (!area) return;
  if (!comps.length) { area.style.display = "none"; return; }

  const gradeLabels = { raw:"Raw", psa8:"PSA 8", psa9:"PSA 9", psa10:"PSA 10" };
  const gradeCls    = { raw:"comp-grade-raw", psa8:"comp-grade-psa8", psa9:"comp-grade-psa9", psa10:"comp-grade-psa10" };

  const rows = comps.map((c, i) => `
    <div class="comp-preview-row" id="comp-row-${i}">
      <span class="comp-preview-grade ${gradeCls[c.grade]}">${gradeLabels[c.grade]}</span>
      <span class="comp-preview-price">${fmt$(c.price)}</span>
      <span class="comp-preview-line">${escHtml(c.line)}</span>
      <button class="comp-preview-remove" onclick="removeComp(${i})" title="Remove this comp">✕</button>
    </div>`).join("");

  area.innerHTML = `
    <div class="comp-preview-header">
      <span>Parsed Comps (${comps.length})</span>
      <span class="fs-sm text-muted" style="font-weight:400">Remove bad ones before calculating</span>
    </div>
    ${rows}`;
  area.style.display = "";
}

function removeComp(idx) {
  _parsedComps.splice(idx, 1);
  _rebuildFromParsed();
}

function _rebuildFromParsed() {
  const result = { raw: [], psa8: [], psa9: [], psa10: [] };
  for (const c of _parsedComps) result[c.grade].push(c.price);

  const summaryParts = [];
  const gradeLabels  = { raw:"Raw", psa8:"PSA 8", psa9:"PSA 9", psa10:"PSA 10" };
  for (const [grade, rawPrices] of Object.entries(result)) {
    const prices = _applyIQR(rawPrices);
    const inp    = document.getElementById("comps_" + grade);
    if (prices.length > 0) {
      const med = _median(prices);
      const csv = prices.map(p => Number.isInteger(p) ? p : p.toFixed(2)).join(", ");
      if (inp) inp.value = csv;
      _setBadge(grade, prices.length, med);
      summaryParts.push(`${prices.length} ${gradeLabels[grade]}`);
    } else {
      if (inp) inp.value = "";
      _setBadge(grade, 0, 0);
    }
  }

  const detRow    = document.getElementById("comp-detection-row");
  const summaryEl = document.getElementById("bulk-parse-summary");
  const anyFound  = summaryParts.length > 0;
  if (detRow) detRow.style.display = anyFound ? "" : "none";
  if (summaryEl) {
    summaryEl.textContent  = anyFound ? "Detected: " + summaryParts.join("  ·  ") : "All comps removed.";
    summaryEl.style.display = "";
  }
  renderCompPreview(_parsedComps);
}

// ── Bookmarklet: open sold search + start polling ──────
function getBaseQuery() {
  const d = getCardData();
  const { player_name: p, year: y, set_name: s, card_number: n, variation: v } = d;
  return [y, p, s, (v && v !== "Base") ? v : "", n ? "#" + n : ""].filter(Boolean).join(" ");
}

async function openSoldSearch(grade) {
  const data = getCardData();
  if (!data.player_name && !data.set_name) { showToast("Enter card details first", "error"); return; }

  fetch("/api/set-card-context", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...data, grade }),
  }).catch(() => {});

  const suffix = { raw: "", psa8: " PSA 8", psa9: " PSA 9", psa10: " PSA 10" }[grade] || "";
  const q      = getBaseQuery() + suffix;
  window.open("https://www.ebay.com/sch/i.html?" +
    new URLSearchParams({ _nkw: q, LH_Sold: "1", LH_Complete: "1", _sop: "13" }), "_blank");

  startCompPoll();
  const labels = { raw: "Raw", psa8: "PSA 8", psa9: "PSA 9", psa10: "PSA 10" };
  showToast(`Opened ${labels[grade]} search — click Comp Collector bookmarklet on that page`, "info");
}

function startCompPoll() {
  if (_pollTimer) return;
  _pollTimer = setInterval(async () => {
    try {
      const result = await (await fetch("/api/comp-poll")).json();
      let anyNew = false;
      for (const [grade, gdata] of Object.entries(result)) {
        if (gdata.ts && gdata.ts !== _knownCompTs[grade]) {
          _knownCompTs[grade] = gdata.ts;
          const inp = document.getElementById("comps_" + grade);
          if (inp) inp.value = gdata.prices_csv;
          _setBadge(grade, gdata.count, gdata.median);
          anyNew = true;
        }
      }
      if (anyNew) {
        const detRow = document.getElementById("comp-detection-row");
        if (detRow) detRow.style.display = "";
        showToast("Comps received! Click ⚡ Calculate.", "success");
        await calculate();
      }
    } catch { /* network hiccup — retry next tick */ }
  }, 2000);
}

function stopCompPoll() {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
}

window.addEventListener("beforeunload", stopCompPoll);

// ── Calculate ──────────────────────────────────────────
async function calculate() {
  const data = getCardData();
  data.comps_raw   = document.getElementById("comps_raw")?.value   || "";
  data.comps_psa8  = document.getElementById("comps_psa8")?.value  || "";
  data.comps_psa9  = document.getElementById("comps_psa9")?.value  || "";
  data.comps_psa10 = document.getElementById("comps_psa10")?.value || "";

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
      method: "POST", headers: { "Content-Type": "application/json" },
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
    "Strong Buy":"strong-buy","Possible Buy":"possible","Avoid":"pass","Insufficient Data":"insuf",
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
    profEl.textContent = "No comps yet";
  }

  const chips = [];
  if (has_real_data && break_even_label) {
    const beColor = break_even_grade === "psa10" ? "chip-yellow" : "chip-green";
    chips.push(`<span class="chip ${beColor}">${break_even_grade === "psa10" ? "⚠" : "✅"} Break-even ${break_even_label}+</span>`);
  }
  const liveGrades = ["raw","psa8","psa9","psa10"].filter(k => prices[k].is_live && prices[k].comp_count > 0);
  if (liveGrades.length) {
    const total = liveGrades.reduce((s,k) => s + prices[k].comp_count, 0);
    chips.push(`<span class="chip chip-accent">${total} sold comp${total !== 1 ? "s" : ""}</span>`);
  }
  if (has_real_data && cg.score > 0) chips.push(`<span class="chip">${cg.label}</span>`);
  if (set_risk?.label) {
    chips.push(`<span class="chip ${set_risk.color === "yellow" ? "chip-yellow" : ""}">${escHtml(set_risk.label)}</span>`);
  }
  if (!has_real_data) {
    chips.push(`<span class="chip chip-yellow">⚠ No verified comps — paste sold listings above</span>`);
  }
  document.getElementById("signal-chips").innerHTML = chips.join("");
}

// ── Grade chips row ────────────────────────────────────
function renderGradeChips(prices, results, best, hasRealData) {
  const grades = [
    {key:"raw",   label:"Raw",    valCls:""},
    {key:"psa8",  label:"PSA 8",  valCls:"text-yellow"},
    {key:"psa9",  label:"PSA 9",  valCls:"text-accent"},
    {key:"psa10", label:"PSA 10", valCls:"text-green"},
  ];
  document.getElementById("grade-chips-row").innerHTML = grades.map(({key, label, valCls}) => {
    const g      = prices[key];
    const r      = results[key];
    const isLive = g.is_live && g.comp_count >= 1;
    const dot    = isLive
      ? `<span class="live-dot" title="${g.comp_count} comps"></span>`
      : `<span class="est-dot"  title="No comps"></span>`;
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
      ${priceHtml}${profitHtml}
      <div class="grade-chip-conf"><span class="confidence-badge ${confCls(g.confidence)}">${capFirst(g.confidence)}</span></div>
    </div>`;
  }).join("");
}

// ── Analysis table ─────────────────────────────────────
function renderAnalysisTable(results, best, hasRealData, prices) {
  const rows = [
    {label:"Sell Raw",       cls:"grade-raw", key:"raw"},
    {label:"Grade → PSA 8",  cls:"grade-8",   key:"psa8"},
    {label:"Grade → PSA 9",  cls:"grade-9",   key:"psa9"},
    {label:"Grade → PSA 10", cls:"grade-10",  key:"psa10"},
  ];
  document.getElementById("results-tbody").innerHTML = rows.map(row => {
    const g      = results[row.key];
    const p      = prices?.[row.key];
    const isLive = p && p.is_live && p.comp_count >= 1;
    const isBest = row.key === best.key && hasRealData && isLive;
    const star   = isBest ? ' <span class="best-badge">★ Best</span>' : "";
    if (!isLive) return `<tr style="opacity:0.45">
      <td><span class="grade-badge ${row.cls}">${row.label}</span></td>
      <td colspan="4" class="text-muted fs-sm">No comps</td></tr>`;
    return `<tr class="${isBest ? "row-best" : ""}">
      <td><span class="grade-badge ${row.cls}">${row.label}</span>${star}</td>
      <td class="fw-bold">${fmt$(g.sale_price)}</td>
      <td>${fmt$(g.cost_basis)}<div class="cell-sub">+${fmt$(g.sell_fee)} fees</div></td>
      <td class="${profCls(g.profit)} fw-bold">${fmt$(g.profit)}</td>
      <td class="${profCls(g.roi)} fw-bold">${fmtPct(g.roi)}</td></tr>`;
  }).join("");
}

// ── Comps section ──────────────────────────────────────
function renderCompsSection(prices) {
  const gradeOrder  = ["raw","psa8","psa9","psa10"];
  const gradeLabels = {raw:"Raw", psa8:"PSA 8", psa9:"PSA 9", psa10:"PSA 10"};
  let html = "";
  for (const key of gradeOrder) {
    const g = prices[key];
    if (!g.is_live || g.comp_count < 1) {
      html += `<div class="comp-grade-section">
        <div class="comp-grade-label">${gradeLabels[key]}</div>
        <div class="no-comps-msg">No verified comps.</div></div>`;
      continue;
    }
    const comps = (g.comps || []).filter(c => c.price > 0);
    const priceList = comps.map(c => fmt$(c.price)).join(" · ");
    const hi = fmt$(g.high_price || comps[comps.length-1]?.price || 0);
    const lo = fmt$(g.low_price  || comps[0]?.price || 0);
    const src = g.source_name === "User entered" ? "Pasted" : escHtml(g.source_name || "Manual");
    html += `<div class="comp-grade-section">
      <div class="comp-grade-label">
        ${gradeLabels[key]}
        <span class="source-badge source-scp">${src}</span>
        <span class="comp-count">${g.comp_count} comp${g.comp_count !== 1 ? "s" : ""}</span>
      </div>
      <div class="user-comps-stats">
        <span>Median <strong>${fmt$(g.median_price)}</strong></span>
        <span class="text-muted">·</span>
        <span>Range <strong>${lo} – ${hi}</strong></span>
        <span class="text-muted">·</span>
        <span class="confidence-badge ${confCls(g.confidence)}">${capFirst(g.confidence)} confidence</span>
      </div>
      <div class="user-comps-prices">${priceList}</div>
    </div>`;
  }
  document.getElementById("comps-by-grade").innerHTML = html ||
    `<div class="text-muted fs-sm">No comps available.</div>`;
}

// ── Formulas ───────────────────────────────────────────
function renderFormulas(results, costs) {
  const {raw_price: rb, grading, shipping, fee_pct, graded_cost} = costs;
  const raw = results.raw, p10 = results.psa10;
  document.getElementById("formulas-section").innerHTML = `
    <div class="formula-grid">
      <div class="formula-block">
        <div class="formula-heading">Sell Raw Now</div>
        <div class="formula-line"><span class="fl-label">Cost basis</span><span class="fl-expr">= Raw Purchase Price</span><span class="fl-val">${fmt$(rb)}</span></div>
        <div class="formula-line"><span class="fl-label">Selling fees</span><span class="fl-expr">= Raw Market × ${fee_pct}%</span><span class="fl-val">${fmt$(raw.sell_fee)}</span></div>
        <div class="formula-line formula-result"><span class="fl-label">Profit</span><span class="fl-expr">= ${fmt$(raw.sale_price)} − ${fmt$(rb)} − ${fmt$(raw.sell_fee)}</span><span class="fl-val ${profCls(raw.profit)} fw-bold">${fmt$(raw.profit)}</span></div>
        <div class="formula-line formula-result"><span class="fl-label">ROI</span><span class="fl-expr">= Profit ÷ ${fmt$(rb)} × 100</span><span class="fl-val ${profCls(raw.roi)} fw-bold">${fmtPct(raw.roi)}</span></div>
      </div>
      <div class="formula-block">
        <div class="formula-heading">Grade &amp; Sell (PSA 10 example)</div>
        <div class="formula-line"><span class="fl-label">Cost basis</span><span class="fl-expr">= Raw + Grading + Shipping</span><span class="fl-val">${fmt$(rb)} + ${fmt$(grading)} + ${fmt$(shipping)} = ${fmt$(graded_cost)}</span></div>
        <div class="formula-line"><span class="fl-label">Selling fees</span><span class="fl-expr">= PSA 10 × ${fee_pct}%</span><span class="fl-val">${fmt$(p10.sell_fee)}</span></div>
        <div class="formula-line formula-result"><span class="fl-label">Profit</span><span class="fl-expr">= ${fmt$(p10.sale_price)} − ${fmt$(graded_cost)} − ${fmt$(p10.sell_fee)}</span><span class="fl-val ${profCls(p10.profit)} fw-bold">${fmt$(p10.profit)}</span></div>
        <div class="formula-line formula-result"><span class="fl-label">ROI</span><span class="fl-expr">= Profit ÷ ${fmt$(graded_cost)} × 100</span><span class="fl-val ${profCls(p10.roi)} fw-bold">${fmtPct(p10.roi)}</span></div>
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
    await fetch(`/api/watchlist/${id}`, {method:"DELETE"});
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
  _parsedComps = [];
  document.getElementById("results-placeholder").style.display = "flex";
  document.getElementById("results-loading").style.display     = "none";
  document.getElementById("results-content").style.display     = "none";
  stopCompPoll();
  _knownCompTs = {};
  fetch("/api/reset-comps", { method: "POST" }).catch(() => {});
  clearBulkImport();
  updateCompLinks();
}
