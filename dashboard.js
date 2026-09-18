const HISTORY_URL = "/api/history";
const BOOKS_URL = "/api/books";
const EVENTS_URL = "/api/events";
const DATA_POLL_MS = 60 * 1000;

// Per-franchise accent colors — the UI recolors itself around the era/franchise.
const FRANCHISE_ACCENTS = {
  "Batman": "#f5c518",
  "Nightwing / Red Hood": "#6366f1",
  "Justice League": "#3b82f6",
  "Crisis / Universe Events": "#a855f7",
  "Green Lantern": "#22c55e",
  "Green Arrow": "#16a34a",
  "Superman": "#1d4ed8",
  "Flash": "#ef4444",
  "Young Justice / Teen Titans": "#ec4899",
  "Justice League Dark": "#14b8a6",
  "Elseworld": "#8b5cf6",
};
const DEFAULT_ACCENT = "#3b82f6";

// Prices above this are treated as "not available" — inflated third-party or
// placeholder listings, not a real buyable price.
const MAX_PRICE = 15000;
const MIN_PRICE_DROP_PERCENT = 1;
const availablePrice = (v) => (v != null && v <= MAX_PRICE ? v : null);

const STORE_LABELS = { bookswagon: "Bookswagon", amazon: "Amazon", flipkart: "Flipkart", independent: "Independent seller" };

const accentFor = (franchise) => FRANCHISE_ACCENTS[franchise] || DEFAULT_ACCENT;

const updatedEl = document.getElementById("last-updated");
const refreshBtn = document.getElementById("refresh-btn");
const rebirthFilterBtn = document.getElementById("rebirth-filter-btn");
const unreleasedFilterBtn = document.getElementById("unreleased-filter-btn");
const statsRow = document.getElementById("stats-row");
const filtersEl = document.getElementById("filters");
const moversEl = document.getElementById("movers");
const contentEl = document.getElementById("content");

let chartInstances = new Map();
let booksData = [];
let historyData = [];
let priceEvents = [];
let activeFilter = "All";
let hideRebirth = false;
let extractionInProgress = false;
// Start with forthcoming books hidden every time the dashboard is opened.
let hideUnreleased = true;

// The toggle is a cutoff: it hides Rebirth itself and every later era.
const isRebirthOrLaterEra = (book) =>
  /\b(rebirth|infinite frontier|dawn of dc|dc all[- ]in|all[- ]in)\b/i.test(book.era || "");
const eraScopedBooks = () =>
  booksData.filter((book) => !hideRebirth || !isRebirthOrLaterEra(book));

function todayISO() {
  const today = new Date();
  const year = today.getFullYear();
  const month = String(today.getMonth() + 1).padStart(2, "0");
  const day = String(today.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

// Confirmed dates age out automatically. The explicit flag covers announced
// books whose publisher has not assigned a date yet.
const isUnreleased = (book) =>
  book.unreleased === true || Boolean(book.release_date && book.release_date > todayISO());

const visibleBooks = () =>
  eraScopedBooks().filter((book) => !hideUnreleased || !isUnreleased(book));

function filteredBooks() {
  const books = visibleBooks();
  if (activeFilter === "All") return books;
  return books.filter((book) => book.franchise === activeFilter);
}

function formatCurrency(value) {
  if (value === null || value === undefined) return null;
  return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
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
    [
      ["Bookswagon", item.price],
      ["Amazon", item.amazon_price],
      ["Flipkart", book.flipkart_url ? item.flipkart_price : null],
    ].forEach(([store, val]) => {
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
  const flipkart = [];
  historyData.forEach((snapshot) => {
    const item = snapshot.items.find((entry) => itemMatchesBook(entry, book));
    if (!item) return;
    dates.push(snapshot.date);
    bookswagon.push(availablePrice(item.price));
    amazon.push(availablePrice(item.amazon_price));
    flipkart.push(book.flipkart_url ? availablePrice(item.flipkart_price) : null);
  });
  return { dates, bookswagon, amazon, flipkart };
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
  const flipkartRaw = book.flipkart_url && item && item.flipkart_price != null ? item.flipkart_price : null;
  const bookswagon = availablePrice(bookswagonRaw);
  const amazon = availablePrice(amazonRaw);
  const flipkart = availablePrice(flipkartRaw);
  const independent = Number.isFinite(book.independent_price) && book.independent_price > 0
    ? book.independent_price : null;
  // A store whose listing exists but is priced above the cap: mark it "not available".
  const bookswagonOver = bookswagonRaw != null && bookswagon == null;
  const amazonOver = amazonRaw != null && amazon == null;
  const flipkartOver = flipkartRaw != null && flipkart == null;

  // Cheapest available store wins; savings = gap to the next-cheapest store.
  const stores = [
    { key: "bookswagon", price: bookswagon, link: book.bookswagon_url },
    { key: "amazon", price: amazon, link: book.amazon_url },
    { key: "flipkart", price: flipkart, link: book.flipkart_url },
    { key: "independent", price: independent, link: null },
  ].filter((s) => s.price != null);
  stores.sort((a, b) => a.price - b.price);

  const best = stores.length ? stores[0].key : null;
  const bestPrice = stores.length ? stores[0].price : null;
  const bestLink = stores.length ? stores[0].link : null;
  const savings = stores.length >= 2 ? stores[1].price - stores[0].price : 0;

  return {
    bookswagon, amazon, flipkart, independent,
    bookswagonOver, amazonOver, flipkartOver,
    best, bestPrice, bestLink, savings,
    tracked: Boolean(book.bookswagon_url || book.amazon_url || book.flipkart_url),
  };
}

/* ---------------- Today's price drops ---------------- */
// Drop events are persisted at scrape time, so an intraday low remains visible
// even if a later refresh on the same day records a rebound.
function computeMovers() {
  const books = new Map(filteredBooks().map((book) => [book.id, book]));
  const today = todayISO();

  const movers = priceEvents
    .filter((event) => {
      const from = Number(event.from);
      const to = Number(event.to);
      const dropPercent = ((from - to) / from) * 100;
      return (
        from > 0 &&
        to < from &&
        Number.isFinite(dropPercent) &&
        dropPercent >= MIN_PRICE_DROP_PERCENT &&
        event.date === today &&
        books.has(event.book_id) &&
        (event.store !== "Flipkart" || Boolean(books.get(event.book_id).flipkart_url))
      );
    })
    .map((event) => {
      const book = books.get(event.book_id);
      const href =
        event.store === "Bookswagon"
          ? book.bookswagon_url
          : event.store === "Amazon"
            ? book.amazon_url
            : book.flipkart_url;
      return {
        book,
        store: event.store,
        href,
        from: event.from,
        to: event.to,
        delta: event.delta,
        pct: ((event.to - event.from) / event.from) * 100,
        capturedAt: event.captured_at || event.date,
      };
    });

  movers.sort((x, y) => y.capturedAt.localeCompare(x.capturedAt) || x.delta - y.delta);
  return movers;
}

function formatEventTime(value) {
  const d = new Date(value.length === 10 ? `${value}T00:00:00` : value);
  if (Number.isNaN(d.getTime())) return value;
  const date = d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  if (value.length === 10) return date;
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return `${date}, ${time}`;
}

function renderMovers() {
  const movers = computeMovers();
  if (!movers.length) {
    moversEl.innerHTML = "";
    moversEl.classList.add("hidden");
    return;
  }
  moversEl.classList.remove("hidden");

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
        <span class="mover-store"><i class="dot ${m.store.toLowerCase()}"></i>${m.store} · ${formatEventTime(m.capturedAt)}</span>
        <span class="mover-prices">${formatCurrency(m.from)} <span class="arrow">→</span> ${formatCurrency(m.to)}</span>
        <span class="mover-delta">${arrow} ${Math.abs(m.pct).toFixed(1)}%</span>
      </${tag}>`;
    })
    .join("");

  moversEl.innerHTML = `
    <div class="movers-head">
      <h2>Today's price drops</h2>
      <span class="movers-sub">${movers.length} drop${movers.length === 1 ? "" : "s"} · ${formatDate(todayISO())}</span>
    </div>
    <div class="movers-list">${rows}</div>
    <p class="movers-empty muted small hidden">No price drops in this franchise today.</p>`;
}

/* ---------------- Stat tiles ---------------- */
function renderStats() {
  const scopedBooks = filteredBooks();
  const infos = scopedBooks.map(priceInfo);
  const tracked = infos.filter((i) => i.tracked).length;
  const priced = infos.filter((i) => i.bestPrice != null);
  const cheapest = priced.reduce(
    (min, i) => (min == null || i.bestPrice < min.bestPrice ? i : min),
    null
  );
  const totalBest = priced.reduce((sum, i) => sum + i.bestPrice, 0);

  const tiles = [
    { label: "Titles to collect", value: `${scopedBooks.length}` },
    { label: "Live tracked", value: `${tracked}<small> / ${scopedBooks.length}</small>` },
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
  const scopedBooks = visibleBooks();
  const franchises = [];
  scopedBooks.forEach((b) => {
    if (!franchises.includes(b.franchise)) franchises.push(b.franchise);
  });

  // If hiding Rebirth removes the selected franchise, fall back to all of the
  // remaining books instead of leaving the dashboard blank.
  if (activeFilter !== "All" && !franchises.includes(activeFilter)) {
    activeFilter = "All";
  }

  const makeChip = (name, accent) => {
    const count =
      name === "All"
        ? scopedBooks.length
        : scopedBooks.filter((b) => b.franchise === name).length;
    const active = name === activeFilter ? " active" : "";
    return `<button class="chip${active}" data-filter="${name}" style="--chip-accent:${accent}">
      <span class="swatch"></span>${name}<span class="count">${count}</span>
    </button>`;
  };

  filtersEl.innerHTML =
    makeChip("All", DEFAULT_ACCENT) +
    franchises.map((f) => makeChip(f, accentFor(f))).join("");

  filtersEl.querySelectorAll(".chip[data-filter]").forEach((chip) => {
    chip.addEventListener("click", () => {
      activeFilter = chip.dataset.filter;
      renderDashboard();
    });
  });
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
  const releaseHtml = isUnreleased(book)
    ? `<div class="release-status">${book.release_date ? `Releases ${formatDate(book.release_date)}` : "Release date TBA"}</div>`
    : "";

  if (!info.tracked && info.independent == null) {
    card.innerHTML = `<h3>${book.name}</h3>${releaseHtml}<p class="pending">Link pending — not tracked yet.</p>`;
    return card;
  }

  const savingsText =
    info.savings > 0
      ? `<span class="savings">Save ${formatCurrency(info.savings)}</span>`
      : `<span class="savings none">—</span>`;

  const buy = info.bestLink
    ? `<a class="buy-btn" href="${info.bestLink}" target="_blank" rel="noopener noreferrer">Buy on ${STORE_LABELS[info.best]}</a>`
    : info.best === "independent" ? '<span class="muted small">Contact independent seller</span>' : "";

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
    ${releaseHtml}
    <div class="price-rows">
      ${priceRow("Bookswagon", info.bookswagon, "bookswagon", info.best === "bookswagon", book.bookswagon_url, info.bookswagonOver)}
      ${priceRow("Amazon", info.amazon, "amazon", info.best === "amazon", book.amazon_url, info.amazonOver)}
      ${book.flipkart_url ? priceRow("Flipkart", info.flipkart, "flipkart", info.best === "flipkart", book.flipkart_url, info.flipkartOver) : ""}
      ${priceRow("Independent seller", info.independent, "independent", info.best === "independent", null, false)}
    </div>
    ${info.independent != null ? '<p class="muted small">Seller quote · final price after 32% off</p>' : ""}
    ${lowHtml}
    <div class="card-foot">${savingsText}${buy}</div>
    <div class="spark"><canvas></canvas></div>
  `;

  // Only draw a sparkline when there is history worth showing.
  const series = buildSeries(book);
  const points = series.bookswagon
    .concat(series.amazon)
    .concat(series.flipkart)
    .filter((v) => v != null);
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
    {
      label: "Flipkart",
      data: series.flipkart,
      borderColor: "#a855f7",
      backgroundColor: "rgba(168,85,247,0.10)",
      borderWidth: 2,
      tension: 0.3,
      pointRadius: 0,
      pointHoverRadius: 4,
      pointHoverBackgroundColor: "#a855f7",
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
  const scopedBooks = filteredBooks();

  if (!scopedBooks.length) {
    contentEl.innerHTML = `<div class="empty-state">No books match these filters.</div>`;
    return;
  }

  let franchiseBlock = null;
  let grid = null;
  let currentFranchise = null;
  let currentEra = null;

  scopedBooks.forEach((book) => {
    const accent = accentFor(book.franchise);

    if (book.franchise !== currentFranchise) {
      currentFranchise = book.franchise;
      currentEra = null;
      const count = scopedBooks.filter((b) => b.franchise === book.franchise).length;

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
}

function updateLastUpdated(status = "") {
  if (!historyData.length) {
    updatedEl.textContent = status || "No data yet";
    return;
  }
  const latest = historyData[historyData.length - 1];
  const lastRefresh = `Last refreshed ${
    latest.captured_at ? formatEventTime(latest.captured_at) : formatDate(latest.date)
  }`;
  updatedEl.textContent = status ? `${status} · ${lastRefresh}` : lastRefresh;
}

function renderDashboard() {
  // Render filters first because a visibility toggle can invalidate the active
  // franchise and reset it to All.
  updateUnreleasedToggle();
  renderFilters();
  renderStats();
  renderMovers();
  renderContent();
}

function updateRebirthToggle() {
  rebirthFilterBtn.setAttribute("aria-checked", String(hideRebirth));
  rebirthFilterBtn.title = hideRebirth
    ? "Rebirth and later era books are hidden"
    : "Hide Rebirth and later era books";
}

function updateUnreleasedToggle() {
  const count = eraScopedBooks().filter(isUnreleased).length;
  unreleasedFilterBtn.setAttribute("aria-checked", String(hideUnreleased));
  unreleasedFilterBtn.title = `${count} unreleased book${count === 1 ? "" : "s"} ${hideUnreleased ? "hidden" : "shown"}`;
}

/* ---------------- Data ---------------- */
async function loadData() {
  const [booksRes, historyRes, eventsRes] = await Promise.all([
    fetch(BOOKS_URL, { cache: "no-store" }),
    fetch(HISTORY_URL, { cache: "no-store" }),
    fetch(EVENTS_URL, { cache: "no-store" }),
  ]);
  if (!booksRes.ok || !historyRes.ok || !eventsRes.ok) throw new Error("Failed to load data");
  booksData = await booksRes.json();
  historyData = await historyRes.json();
  priceEvents = await eventsRes.json();

  updateLastUpdated();
  renderDashboard();
}

async function refreshData() {
  try {
    await loadData();
  } catch (e) {
    updateLastUpdated("API unreachable — retrying automatically");
  }
}

async function runExtraction() {
  if (extractionInProgress) return;
  extractionInProgress = true;
  refreshBtn.classList.add("loading");
  refreshBtn.disabled = true;
  updateLastUpdated("Refreshing prices…");
  try {
    const res = await fetch("/api/refresh", { cache: "no-store" });
    if (!res.ok) throw new Error("Refresh failed");
    await loadData();
  } catch (e) {
    updateLastUpdated("Unable to refresh");
  } finally {
    extractionInProgress = false;
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

rebirthFilterBtn.addEventListener("click", () => {
  hideRebirth = !hideRebirth;
  activeFilter = "All";
  updateRebirthToggle();
  renderDashboard();
});

unreleasedFilterBtn.addEventListener("click", () => {
  hideUnreleased = !hideUnreleased;
  renderDashboard();
});

updateRebirthToggle();
updateUnreleasedToggle();
refreshBtn.addEventListener("click", runExtraction);
refreshData();
setInterval(refreshData, DATA_POLL_MS);
