// ── State ──────────────────────────────────────────────
let calcResults = null;
let calcData = null;

// ── Tabs ───────────────────────────────────────────────
function showPage(name) {
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".nav-tab").forEach(t => t.classList.remove("active"));
  document.getElementById("page-" + name).classList.add("active");
  document.querySelector(`[data-tab="${name}"]`).classList.add("active");
  if (name === "watchlist") loadWatchlist();
}

// ── Probability totals ─────────────────────────────────
function updateProbTotal() {
  const ids = ["prob_10", "prob_9", "prob_8", "prob_lower"];
  const total = ids.reduce((s, id) => s + (parseFloat(document.getElementById(id).value) || 0), 0);
  const el = document.getElementById("prob-total");
  el.textContent = `Total: ${total.toFixed(1)}%`;
  el.className = "prob-total " + (Math.abs(total - 100) > 0.1 ? "text-red" : "text-green");
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
    "player_name","year","set_name","card_number","sport",
    "raw_price","grading_cost","shipping_fees","selling_fee_pct",
    "raw_market","psa10_market","psa9_market","psa8_market",
    "prob_10","prob_9","prob_8","prob_lower"
  ];

  const data = {};
  for (const f of fields) {
    const el = document.getElementById(f);
    if (el) data[f] = el.value;
  }

  // Validation
  if (!data.raw_price || parseFloat(data.raw_price) <= 0) {
    showToast("Enter a raw purchase price", "error");
    return;
  }

  const probTotal = ["prob_10","prob_9","prob_8","prob_lower"]
    .reduce((s, id) => s + (parseFloat(data[id]) || 0), 0);
  if (Math.abs(probTotal - 100) > 0.5) {
    showToast(`Grade probabilities must sum to 100% (currently ${probTotal.toFixed(1)}%)`, "error");
    return;
  }

  try {
    const res = await fetch("/api/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const result = await res.json();
    calcResults = result;
    calcData = data;
    renderResults(result, data);
  } catch (e) {
    showToast("Calculation failed. Check your inputs.", "error");
  }
}

// ── Render Results ─────────────────────────────────────
function renderResults(r, data) {
  document.getElementById("results-placeholder").style.display = "none";
  document.getElementById("results-content").style.display = "flex";

  const { grades, expected } = r;
  const playerName = data.player_name || "Card";
  const year = data.year ? data.year + " " : "";
  const setName = data.set_name || "";
  const cardNum = data.card_number ? " #" + data.card_number : "";
  const cardTitle = `${year}${playerName}${setName ? " – " + setName : ""}${cardNum}`;

  // Recommendation banner
  const recEl = document.getElementById("rec-banner");
  const recMap = {
    "Strong Buy":    { cls: "rec-strong-buy",   badge: "badge-strong-buy",   icon: "📈", color: "text-green" },
    "Possible Buy":  { cls: "rec-possible-buy",  badge: "badge-possible-buy", icon: "🤔", color: "text-yellow" },
    "Avoid":         { cls: "rec-avoid",         badge: "badge-avoid",        icon: "🚫", color: "text-red" },
  };
  const recInfo = recMap[expected.recommendation] || recMap["Avoid"];
  recEl.className = "recommendation-banner " + recInfo.cls;
  recEl.innerHTML = `
    <div class="rec-icon">${recInfo.icon}</div>
    <div>
      <div class="rec-label">Recommendation for</div>
      <div class="rec-title ${recInfo.color}">${expected.recommendation}</div>
      <div class="rec-sub">${cardTitle}</div>
    </div>
    <div style="margin-left:auto;text-align:right;">
      <div class="stat-label">Expected ROI</div>
      <div class="stat-value ${recInfo.color}">${fmtPct(expected.roi)}</div>
      <div class="stat-sub">Exp. Profit: ${fmt$(expected.profit)}</div>
    </div>
  `;

  // Stats row
  document.getElementById("stat-ev-profit").textContent = fmt$(expected.profit);
  document.getElementById("stat-ev-profit").className = "stat-value " + profitClass(expected.profit);
  document.getElementById("stat-ev-roi").textContent = fmtPct(expected.roi);
  document.getElementById("stat-ev-roi").className = "stat-value " + profitClass(expected.roi);
  document.getElementById("stat-best-grade").textContent = bestGrade(grades);
  document.getElementById("stat-total-cost").textContent = fmt$(grades.psa10.total_cost);

  // Results table
  const tbody = document.getElementById("results-tbody");
  const rows = [
    { label: "PSA 10", cls: "grade-10", key: "psa10" },
    { label: "PSA 9",  cls: "grade-9",  key: "psa9"  },
    { label: "PSA 8",  cls: "grade-8",  key: "psa8"  },
    { label: "Raw",    cls: "grade-raw", key: "raw"  },
  ];

  tbody.innerHTML = rows.map(row => {
    const g = grades[row.key];
    return `<tr>
      <td><span class="grade-badge ${row.cls}">${row.label}</span></td>
      <td class="fw-bold">${fmt$(g.sale_price)}</td>
      <td>${fmt$(g.total_cost)}<div class="cell-sub">+${fmt$(g.selling_fees)} fees</div></td>
      <td class="${profitClass(g.profit)} fw-bold">${fmt$(g.profit)}</td>
      <td class="${profitClass(g.roi)} fw-bold">${fmtPct(g.roi)}</td>
    </tr>`;
  }).join("");
}

function bestGrade(grades) {
  const order = ["psa10", "psa9", "psa8", "raw"];
  const labels = { psa10: "PSA 10", psa9: "PSA 9", psa8: "PSA 8", raw: "Raw" };
  let best = order[0];
  order.forEach(k => {
    if ((grades[k].roi || 0) > (grades[best].roi || 0)) best = k;
  });
  return labels[best];
}

// ── Add to Watchlist ───────────────────────────────────
async function addToWatchlist() {
  if (!calcData) {
    showToast("Calculate first before saving", "error");
    return;
  }
  if (!calcData.player_name) {
    showToast("Enter a player name before saving", "error");
    return;
  }

  try {
    const res = await fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(calcData),
    });
    const result = await res.json();
    if (result.success) {
      showToast("Card saved to watchlist!", "success");
    } else {
      showToast("Failed to save card", "error");
    }
  } catch (e) {
    showToast("Error saving card", "error");
  }
}

// ── Watchlist ──────────────────────────────────────────
async function loadWatchlist() {
  try {
    const res = await fetch("/api/watchlist");
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
    const badgeClass = recBadgeClass(card.recommendation);
    const cardName = [card.year, card.player_name].filter(Boolean).join(" ")
      + (card.set_name ? " – " + card.set_name : "")
      + (card.card_number ? " #" + card.card_number : "");
    return `<tr>
      <td>
        <div class="fw-bold">${card.player_name || "—"}</div>
        <div class="cell-sub">${cardName}</div>
      </td>
      <td>${card.sport || "—"}</td>
      <td class="fw-bold">${fmt$(card.raw_price)}</td>
      <td class="fw-bold">${fmt$(card.psa10_price)}</td>
      <td class="${profitClass(card.expected_profit)} fw-bold">${fmt$(card.expected_profit)}</td>
      <td class="${profitClass(card.expected_roi)} fw-bold">${fmtPct(card.expected_roi)}</td>
      <td><span class="badge ${badgeClass}">${card.recommendation}</span></td>
      <td>
        <button class="btn btn-danger btn-sm watchlist-action" onclick="deleteCard(${card.id})">Remove</button>
      </td>
    </tr>`;
  }).join("");
}

function recBadgeClass(rec) {
  if (rec === "Strong Buy") return "badge-strong-buy";
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
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
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
  calcData = null;
  document.getElementById("results-placeholder").style.display = "flex";
  document.getElementById("results-content").style.display = "none";
  updateProbTotal();
}

// ── Init ───────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  updateProbTotal();
  ["prob_10","prob_9","prob_8","prob_lower"].forEach(id => {
    document.getElementById(id).addEventListener("input", updateProbTotal);
  });
});
