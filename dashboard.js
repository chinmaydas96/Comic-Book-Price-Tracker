const HISTORY_URL = "/api/history";
const BOOKS_URL = "/api/books";
const AUTO_REFRESH_MS = 5 * 60 * 1000;

// Per-franchise accent colors — the UI recolors itself around the era/franchise.
const FRANCHISE_ACCENTS = {
  "Batman": "#f5c518",
  "Justice League": "#3b82f6",
  "Crisis / Universe Events": "#a855f7",
  "Green Lantern": "#22c55e",
  "Superman": "#1d4ed8",
  "Flash": "#ef4444",
  "Justice League Dark": "#14b8a6",
};
const DEFAULT_ACCENT = "#3b82f6";

// Prices above this are treated as "not available" — inflated third-party or
// placeholder listings, not a real buyable price.
const MAX_PRICE = 15000;
const availablePrice = (v) => (v != null && v <= MAX_PRICE ? v : null);

const accentFor = (franchise) => FRANCHISE_ACCENTS[franchise] || DEFAULT_ACCENT;

const updatedEl = document.getElementById("last-updated");
const refreshBtn = document.getElementById("refresh-btn");
const statsRow = document.getElementById("stats-row");
const filtersEl = document.getElementById("filters");
const moversEl = document.getElementById("movers");
const contentEl = document.getElementById("content");

let chartInstances = new Map();
let booksData = [];
let historyData = [];
let activeFilter = "All";

function formatCurrency(value) {
  if (value === null || value === undefined) return null;
  return `₹${Math.round(value).toLocaleString("en-IN")}`;
}

function formatDate(iso) {
  const d = new Date(iso + "T00:00:00");
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

// Lowest price ever recorded for a book across both stores + when it happened.
// Seeds with an optional manually-provided historical low (looked up on Keepa,
// etc.), then lets tracked snapshots override it if they ever go lower.
function allTimeLow(book) {
  let low = book.manual_low
    ? {
        price: book.manual_low.price,
        date: book.manual_low.date,
        store: book.manual_low.store || "Amazon",
        manual: true,
      }
    : null;
  historyData.forEach((snapshot) => {
    const item = snapshot.items.find((entry) => itemMatchesBook(entry, book));
    if (!item) return;
    [["Bookswagon", item.price], ["Amazon", item.amazon_price]].forEach(([store, val]) => {
      if (val == null || val > MAX_PRICE) return;
      if (low == null || val < low.price) low = { price: val, date: snapshot.date, store };
    });
  });
  return low;
}

function itemMatchesBook(item, book) {
  if (item.id && book.id && item.id === book.id) return true;
  return Boolean(book.bookswagon_url) && item.url === book.bookswagon_url;
}

function buildSeries(book) {
  const dates = [];
  const bookswagon = [];
  const amazon = [];
  historyData.forEach((snapshot) => {
    const item = snapshot.items.find((entry) => itemMatchesBook(entry, book));
    if (!item) return;
    dates.push(snapshot.date);
    bookswagon.push(availablePrice(item.price));
    amazon.push(availablePrice(item.amazon_price));
  });
  return { dates, bookswagon, amazon };
}

function latestItem(book) {
  for (let i = historyData.length - 1; i >= 0; i -= 1) {
    const item = historyData[i].items.find((entry) => itemMatchesBook(entry, book));
    if (item) return item;
  }
  return null;
}

// Resolve prices + who wins for a book from its latest snapshot.
function priceInfo(book) {
  const item = latestItem(book);
  const bookswagonRaw =
    item && item.price != null && item.in_stock !== false ? item.price : null;
  const amazonRaw = item && item.amazon_price != null ? item.amazon_price : null;
  const bookswagon = availablePrice(bookswagonRaw);
  const amazon = availablePrice(amazonRaw);
  // A store whose listing exists but is priced above the cap: mark it "not available".
  const bookswagonOver = bookswagonRaw != null && bookswagon == null;
  const amazonOver = amazonRaw != null && amazon == null;

  let best = null; // "bookswagon" | "amazon"
  if (bookswagon != null && amazon != null) best = bookswagon <= amazon ? "bookswagon" : "amazon";
  else if (bookswagon != null) best = "bookswagon";
  else if (amazon != null) best = "amazon";

  const bestPrice = best === "bookswagon" ? bookswagon : best === "amazon" ? amazon : null;
  const bestLink =
    best === "bookswagon" ? book.bookswagon_url : best === "amazon" ? book.amazon_url : null;
  const savings =
    bookswagon != null && amazon != null ? Math.abs(bookswagon - amazon) : 0;

  return { bookswagon, amazon, bookswagonOver, amazonOver, best, bestPrice, bestLink, savings, tracked: Boolean(book.bookswagon_url || book.amazon_url) };
}

/* ---------------- Today's movers ---------------- */
// Diff the two most recent snapshots, per store, and list every title whose
// price moved. Out-of-stock Bookswagon entries are ignored (not a real price).
function computeMovers() {
  if (historyData.length < 2) return [];
  const prev = historyData[historyData.length - 2];
  const cur = historyData[historyData.length - 1];
  const movers = [];

  booksData.forEach((book) => {
    const pi = prev.items.find((e) => itemMatchesBook(e, book));
    const ci = cur.items.find((e) => itemMatchesBook(e, book));
    if (!pi || !ci) return;

    [
      ["Bookswagon", "price", book.bookswagon_url],
      ["Amazon", "amazon_price", book.amazon_url],
    ].forEach(([store, field, href]) => {
      let a = pi[field];
      let b = ci[field];
      if (field === "price") {
        if (pi.in_stock === false) a = null;
        if (ci.in_stock === false) b = null;
      }
      a = availablePrice(a);
      b = availablePrice(b);
      if (a == null || b == null || a === b) return;
      movers.push({ book, store, href, from: a, to: b, delta: b - a, pct: ((b - a) / a) * 100 });
    });
  });

  // Biggest drops first, then biggest rises.
  movers.sort((x, y) => x.delta - y.delta);
  return movers;
}

function renderMovers() {
  // Drops only — price rises are intentionally ignored.
  const movers = computeMovers().filter((m) => m.delta < 0);
  if (!movers.length) {
    moversEl.innerHTML = "";
    moversEl.classList.add("hidden");
    return;
  }
  moversEl.classList.remove("hidden");

  const asOf = historyData[historyData.length - 1].date;

  const rows = movers
    .map((m) => {
      const dir = "down";
      const arrow = "▼";
      const link = m.href
        ? `href="${m.href}" target="_blank" rel="noopener noreferrer"`
        : "";
      const tag = m.href ? "a" : "div";
      return `<${tag} class="mover ${dir}" data-franchise="${m.book.franchise}" ${link}
          style="--accent:${accentFor(m.book.franchise)}">
        <span class="mover-name">${m.book.name}</span>
        <span class="mover-store"><i class="dot ${m.store.toLowerCase()}"></i>${m.store}</span>
        <span class="mover-prices">${formatCurrency(m.from)} <span class="arrow">→</span> ${formatCurrency(m.to)}</span>
        <span class="mover-delta">${arrow} ${Math.abs(m.pct).toFixed(1)}%</span>
      </${tag}>`;
    })
    .join("");

  moversEl.innerHTML = `
    <div class="movers-head">
      <h2>Today's price drops</h2>
      <span class="movers-sub">${movers.length} drop${movers.length === 1 ? "" : "s"} · as of ${formatDate(asOf)}</span>
    </div>
    <div class="movers-list">${rows}</div>
    <p class="movers-empty muted small hidden">No price drops in this franchise today.</p>`;
}

/* ---------------- Stat tiles ---------------- */
function renderStats() {
  const infos = booksData.map(priceInfo);
  const tracked = infos.filter((i) => i.tracked).length;
  const priced = infos.filter((i) => i.bestPrice != null);
  const cheapest = priced.reduce(
    (min, i) => (min == null || i.bestPrice < min.bestPrice ? i : min),
    null
  );
  const totalBest = priced.reduce((sum, i) => sum + i.bestPrice, 0);

  const tiles = [
    { label: "Titles to collect", value: `${booksData.length}` },
    { label: "Live tracked", value: `${tracked}<small> / ${booksData.length}</small>` },
    {
      label: "Cheapest right now",
      value: cheapest ? formatCurrency(cheapest.bestPrice) : "—",
    },
    {
      label: "Total (best prices)",
      value: priced.length ? formatCurrency(totalBest) : "—",
    },
  ];

  statsRow.innerHTML = tiles
    .map(
      (t) => `<div class="stat-tile"><div class="label">${t.label}</div><div class="value">${t.value}</div></div>`
    )
    .join("");
}

/* ---------------- Filters ---------------- */
function renderFilters() {
  const franchises = [];
  booksData.forEach((b) => {
    if (!franchises.includes(b.franchise)) franchises.push(b.franchise);
  });

  const makeChip = (name, accent) => {
    const count =
      name === "All" ? booksData.length : booksData.filter((b) => b.franchise === name).length;
    const active = name === activeFilter ? " active" : "";
    return `<button class="chip${active}" data-filter="${name}" style="--chip-accent:${accent}">
      <span class="swatch"></span>${name}<span class="count">${count}</span>
    </button>`;
  };

  filtersEl.innerHTML =
    makeChip("All", DEFAULT_ACCENT) +
    franchises.map((f) => makeChip(f, accentFor(f))).join("");

  filtersEl.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      activeFilter = chip.dataset.filter;
      renderFilters();
      applyFilter();
    });
  });
}

function applyFilter() {
  contentEl.querySelectorAll(".franchise-block").forEach((block) => {
    const show = activeFilter === "All" || block.dataset.franchise === activeFilter;
    block.classList.toggle("hidden", !show);
  });

  // Keep the movers summary in sync with the active franchise filter.
  let anyMover = false;
  moversEl.querySelectorAll(".mover").forEach((el) => {
    const show = activeFilter === "All" || el.dataset.franchise === activeFilter;
    el.classList.toggle("hidden", !show);
    if (show) anyMover = true;
  });
  const empty = moversEl.querySelector(".movers-empty");
  if (empty) empty.classList.toggle("hidden", anyMover || !moversEl.querySelector(".mover"));
}

/* ---------------- Cards ---------------- */
function priceRow(label, amount, dotClass, isBest, href, overCap) {
  const cls = amount == null ? "price-row na" : isBest ? "price-row best" : "price-row";
  const value = amount == null ? (overCap ? "Not available" : "—") : formatCurrency(amount);
  const tag = isBest ? '<span class="tag">Best</span>' : "";
  const inner = `
    <span class="store"><i class="dot ${dotClass}"></i>${label}</span>
    <span class="amount">${value}${tag}<i class="go" aria-hidden="true">↗</i></span>`;
  // Each store row links straight to its own listing, so both stores are
  // reachable regardless of which one currently wins the Buy button. An
  // over-cap ("Not available") store isn't buyable, so it isn't linked.
  if (href && !overCap) {
    return `<a class="${cls} linked" href="${href}" target="_blank" rel="noopener noreferrer">${inner}</a>`;
  }
  return `<div class="${cls}">${inner}</div>`;
}

function renderCard(book, accent) {
  const info = priceInfo(book);
  const card = document.createElement("div");
  card.className = "card";
  card.style.setProperty("--accent", accent);

  if (!info.tracked) {
    card.innerHTML = `<h3>${book.name}</h3><p class="pending">Link pending — not tracked yet.</p>`;
    return card;
  }

  const savingsText =
    info.savings > 0 && info.bookswagon != null && info.amazon != null
      ? `<span class="savings">Save ${formatCurrency(info.savings)}</span>`
      : `<span class="savings none">—</span>`;

  const buy = info.bestLink
    ? `<a class="buy-btn" href="${info.bestLink}" target="_blank" rel="noopener noreferrer">Buy on ${info.best === "bookswagon" ? "Bookswagon" : "Amazon"}</a>`
    : "";

  const low = allTimeLow(book);
  const atCurrentLow = low && info.bestPrice != null && info.bestPrice <= low.price;
  const lowHtml = low
    ? `<div class="low${atCurrentLow ? " active" : ""}">
        <span class="low-tag">▼ Record low</span>
        <span class="low-val">${formatCurrency(low.price)}</span>
        <span class="low-meta">${formatDate(low.date)} · ${low.store}</span>
      </div>`
    : "";

  card.innerHTML = `
    <h3>${book.name}</h3>
    <div class="price-rows">
      ${priceRow("Bookswagon", info.bookswagon, "bookswagon", info.best === "bookswagon", book.bookswagon_url, info.bookswagonOver)}
      ${priceRow("Amazon", info.amazon, "amazon", info.best === "amazon", book.amazon_url, info.amazonOver)}
    </div>
    ${lowHtml}
    <div class="card-foot">${savingsText}${buy}</div>
    <div class="spark"><canvas></canvas></div>
  `;

  // Only draw a sparkline when there is history worth showing.
  const series = buildSeries(book);
  const points = series.bookswagon.concat(series.amazon).filter((v) => v != null);
  if (points.length >= 2) {
    const canvas = card.querySelector("canvas");
    const chart = makeSpark(canvas, series, low ? low.price : null);
    chartInstances.set(book.id, chart);
  } else {
    card.querySelector(".spark").remove();
  }
  return card;
}

function makeSpark(canvas, series, lowValue) {
  const datasets = [
    {
      label: "Bookswagon",
      data: series.bookswagon,
      borderColor: "#38bdf8",
      backgroundColor: "rgba(56,189,248,0.12)",
      borderWidth: 2,
      tension: 0.3,
      pointRadius: 0,
      pointHoverRadius: 4,
      pointHoverBackgroundColor: "#38bdf8",
      pointHoverBorderColor: "#0a0b12",
      fill: true,
      spanGaps: true,
    },
    {
      label: "Amazon",
      data: series.amazon,
      borderColor: "#f59e0b",
      backgroundColor: "rgba(245,158,11,0.10)",
      borderWidth: 2,
      tension: 0.3,
      pointRadius: 0,
      pointHoverRadius: 4,
      pointHoverBackgroundColor: "#f59e0b",
      pointHoverBorderColor: "#0a0b12",
      fill: false,
      spanGaps: true,
    },
  ];

  if (lowValue != null) {
    datasets.push({
      label: "Record low",
      data: series.dates.map(() => lowValue),
      borderColor: "#22c55e",
      borderWidth: 1.5,
      borderDash: [5, 4],
      pointRadius: 0,
      pointHoverRadius: 0,
      fill: false,
      order: -1,
    });
  }

  return new Chart(canvas, {
    type: "line",
    data: { labels: series.dates, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      // Hover anywhere over the chart: snap to the nearest date and show both stores.
      interaction: { mode: "index", intersect: false, axis: "x" },
      plugins: {
        legend: { display: false },
        tooltip: {
          mode: "index",
          intersect: false,
          displayColors: true,
          position: "nearest",
          padding: 8,
          boxPadding: 4,
          titleFont: { size: 11 },
          bodyFont: { size: 11 },
          caretPadding: 6,
          // Keep the dashed record-low line out of the tooltip.
          filter: (item) => item.dataset.label !== "Record low",
          callbacks: {
            title: (ctx) => (ctx.length ? ctx[0].label : ""),
            label: (ctx) =>
              `${ctx.dataset.label}: ${ctx.parsed.y == null ? "n/a" : formatCurrency(ctx.parsed.y)}`,
          },
        },
      },
      scales: { x: { display: false }, y: { display: false } },
    },
  });
}

/* ---------------- Layout ---------------- */
function clearCharts() {
  chartInstances.forEach((c) => c.destroy());
  chartInstances = new Map();
}

function renderContent() {
  clearCharts();
  contentEl.innerHTML = "";

  let franchiseBlock = null;
  let grid = null;
  let currentFranchise = null;
  let currentEra = null;

  booksData.forEach((book) => {
    const accent = accentFor(book.franchise);

    if (book.franchise !== currentFranchise) {
      currentFranchise = book.franchise;
      currentEra = null;
      const count = booksData.filter((b) => b.franchise === book.franchise).length;

      franchiseBlock = document.createElement("section");
      franchiseBlock.className = "franchise-block";
      franchiseBlock.dataset.franchise = book.franchise;
      franchiseBlock.style.setProperty("--accent", accent);
      franchiseBlock.innerHTML = `
        <div class="franchise-head">
          <h2>${book.franchise}</h2>
          <span class="badge">${count} book${count === 1 ? "" : "s"}</span>
        </div>`;
      contentEl.appendChild(franchiseBlock);
    }

    if (book.era !== currentEra) {
      currentEra = book.era;
      const eraHead = document.createElement("div");
      eraHead.className = "era-head";
      eraHead.textContent = book.era;
      franchiseBlock.appendChild(eraHead);

      grid = document.createElement("div");
      grid.className = "grid";
      franchiseBlock.appendChild(grid);
    }

    grid.appendChild(renderCard(book, accent));
  });

  applyFilter();
}

function updateLastUpdated() {
  if (!historyData.length) {
    updatedEl.textContent = "No data yet";
    return;
  }
  updatedEl.textContent = `Updated ${historyData[historyData.length - 1].date}`;
}

/* ---------------- Data ---------------- */
async function loadData() {
  const [booksRes, historyRes] = await Promise.all([
    fetch(BOOKS_URL, { cache: "no-store" }),
    fetch(HISTORY_URL, { cache: "no-store" }),
  ]);
  if (!booksRes.ok || !historyRes.ok) throw new Error("Failed to load data");
  booksData = await booksRes.json();
  historyData = await historyRes.json();

  updateLastUpdated();
  renderStats();
  renderMovers();
  renderFilters();
  renderContent();
}

async function refreshData() {
  try {
    await loadData();
  } catch (e) {
    updatedEl.textContent = "API unreachable — restart server.py & hard-reload";
  }
}

async function runExtraction() {
  refreshBtn.classList.add("loading");
  refreshBtn.disabled = true;
  try {
    const res = await fetch("/api/refresh", { cache: "no-store" });
    if (!res.ok) throw new Error("Refresh failed");
    await loadData();
  } catch (e) {
    updatedEl.textContent = "Unable to refresh";
  } finally {
    refreshBtn.classList.remove("loading");
    refreshBtn.disabled = false;
  }
}

/* ---------------- Theme toggle ---------------- */
const themeBtn = document.getElementById("theme-btn");
const themeIcon = document.getElementById("theme-icon");
const themeLabel = document.getElementById("theme-label");

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  // Button advertises the theme you'll switch TO.
  const goingLight = theme === "dark";
  themeIcon.textContent = goingLight ? "☀" : "☾";
  themeLabel.textContent = goingLight ? "Light" : "Dark";
  localStorage.setItem("theme", theme);
}

applyTheme(localStorage.getItem("theme") || "dark");

themeBtn.addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme");
  applyTheme(current === "light" ? "dark" : "light");
});

refreshBtn.addEventListener("click", runExtraction);
refreshData();
setInterval(refreshData, AUTO_REFRESH_MS);
