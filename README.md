# Comic Book Price Tracker

A self-hosted dashboard that tracks and compares prices for DC omnibus hardcovers
across **Bookswagon** and **Amazon India**, so you always know the cheapest place
to buy — and whether a title is currently at its all-time low.

Built around a curated reading-order list of DC omnibuses (Batman, Justice League,
Superman, Green Lantern, Flash, and line-wide Crisis / event books), it scrapes
current prices, keeps a daily history, and renders it all in a single-page
dashboard.

## Features

- **Store comparison** — Bookswagon vs Amazon India side by side for every title,
  with the cheaper store flagged and a direct "Buy" link. Both store listings are
  individually clickable.
- **Price history** — a per-book sparkline of price movement over time (Chart.js).
- **Record low** — the lowest price ever recorded per book (across both stores),
  with an "at low now" badge when the current price matches it. Supports a manual
  historical low seed (e.g. looked up on Keepa) per book.
- **Today's price drops** — a current-day event feed that preserves every detected
  intraday drop of at least 1%, including lows that later rebound, with an exact
  timestamp.
- **Automatic refresh** — the local macOS background job scrapes fresh prices
  every 20 minutes even when the dashboard, server, and Chrome are closed. An open
  dashboard picks up changes in place without a browser reload.
- **Large-drop Chrome alert** — a newly detected drop greater than 8% opens its
  store page in a new Chrome window once per unique price transition.
- **Franchise filter** — filter the whole dashboard by franchise, each with its own
  accent color.
- **Hide Rebirth+ toggle** — exclude Rebirth and every later publishing era
  (including Infinite Frontier and Dawn of DC), then recalculate the summary
  metrics, franchise counts, and price-drop panel for the remaining set.
- **"Not available" cap** — any price above ₹15,000 is treated as a placeholder /
  inflated listing and shown as *Not available* rather than a real buyable price.
- **Light / dark theme** toggle.

## How it works

```
books.json ──▶ extract.py ──▶ results.json      (latest run)
   (the                ├────▶ history.json      (daily snapshots)
 tracked list)         └────▶ price_events.json (durable intraday drops)
                                    │
                              server.py  ──▶  /api/books, /api/history,
                                               /api/events, /api/refresh
                                    │
                              dashboard.html + dashboard.js + dashboard.css
```

- **`books.json`** is the master list of tracked titles (id, name, franchise, era,
  and the Bookswagon / Amazon India URLs).
- **`extract.py`** fetches each listing, parses the price and stock status, writes
  the latest run to `results.json`, updates the daily snapshot in `history.json`,
  and appends qualifying drops to `price_events.json` with an exact timestamp.
- **`server.py`** serves the static dashboard and exposes the data as JSON APIs. It
  can trigger a fresh scrape on demand and auto-reloads when source files change.
- The **dashboard** fetches `/api/books`, `/api/history`, and `/api/events` and
  renders everything client-side.

No third-party Python packages are required — the scraper and server use only the
Python standard library. Chart.js is loaded from a CDN in the browser.

## Getting started

Requires **Python 3**.

```bash
# 1. Start the dashboard server
python3 server.py
# Serving on http://localhost:8001

# 2. Open the dashboard
open http://localhost:8001/dashboard.html
```

The installed macOS background job refreshes prices every 20 minutes—even when the
dashboard, server, and Chrome are closed. To pull fresh prices immediately, either
click **Refresh** in the dashboard header (which calls `/api/refresh`), or run the
scraper directly:

```bash
python3 extract.py
```

Each run updates that day's snapshot in `history.json` and permanently appends any
new price drops to `price_events.json`, so intraday alerts are not overwritten.

### API endpoints

| Endpoint        | Description                                        |
| --------------- | -------------------------------------------------- |
| `/api/books`    | The tracked book list (`books.json`).              |
| `/api/history`  | All dated price snapshots, filtered to current books. |
| `/api/events`   | Durable timestamped price-drop events.                |
| `/api/refresh`  | Runs `extract.py` and returns the fresh data.      |

## Deployment (Render)

This runs as a plain Python web service — **not** a WSGI/gunicorn app — so use
these settings when creating the Render Web Service:

| Setting          | Value                              |
| ---------------- | ---------------------------------- |
| Language         | `Python 3`                         |
| Build Command    | `pip install -r requirements.txt`  |
| **Start Command**| `python3 server.py`                |

The server automatically binds to the port Render provides via the `PORT`
environment variable, and serves the dashboard at the service root (`/`).

> **Note:** ignore Render's default `gunicorn your_application.wsgi` start command —
> this app uses Python's standard-library HTTP server, so `python3 server.py` is the
> correct start command.

**Caveat — data persistence:** Render's filesystem is ephemeral. The **Refresh**
button (and `extract.py`) will update prices on a running instance, but those writes
to `history.json` / `results.json` are lost on the next deploy or restart. For
durable history, attach a Render Disk or run the scraper on a schedule that commits
back to the repo.

## Adding or editing tracked books

Add an entry to `books.json`:

```json
{
  "id": "unique-slug",
  "name": "Book Title",
  "franchise": "Superman",
  "era": "Rebirth (2016–2018)",
  "bookswagon_url": "https://www.bookswagon.com/book/x/<ISBN-13>",
  "amazon_url": "https://www.amazon.in/dp/<ISBN-10>"
}
```

- Books are grouped by **franchise**, then by **era**, in the order they appear in
  the file. New franchises get a default accent color; add one to
  `FRANCHISE_ACCENTS` in `dashboard.js` for a custom color.
- Either store URL may be `null` (e.g. a pre-order not yet listed on one store) —
  that store simply shows no price.
- Set `"disabled": true` to keep a book's configuration while excluding it from
  scraping, dashboard results, and displayed history.
- Set `"release_date": "YYYY-MM-DD"` for forthcoming books. The dashboard's
  top **Hide unreleased** toggle is enabled by default and excludes them until
  that date. Turn it off to display them. Use `"unreleased": true` only when
  the release date is TBA.
- Optional `"manual_low": { "price": 5390, "date": "2025-10-21", "store": "Amazon" }`
  seeds a known historical low.

## Configuration

- **Price cap** — the "Not available" threshold is the `MAX_PRICE` constant in
  `dashboard.js` (default `15000`).
- **Price-drop threshold** — new drops below 1% are not archived, and any older
  sub-1% events are excluded from the API and dashboard.
- **Chrome alert threshold** — `CHROME_ALERT_DROP_PERCENT` in
  `price_drop_notifier.py` defaults to `8`. Chrome alerts run on the machine that
  hosts `server.py`; headless cloud hosts cannot open a browser on your computer.
- **Automatic refresh interval** — `INTERVAL_SECONDS` and the LaunchAgent's
  `StartInterval` are set to 20 minutes in the local `auto-refresh` helper.
- **Port** — set in `server.py` (default `8001`).

## Project structure

| File              | Purpose                                             |
| ----------------- | --------------------------------------------------- |
| `server.py`       | Static file + JSON API server, on-demand refresh.   |
| `extract.py`      | Price/stock scraper; writes results and history.    |
| `price_drop_notifier.py` | Opens each unique >8% drop in Chrome once.    |
| `books.json`      | Master list of tracked titles.                      |
| `history.json`    | Daily price snapshots.                              |
| `price_events.json` | Timestamped price-drop event archive.             |
| `results.json`    | Latest scrape output.                               |
| `dashboard.html`  | Dashboard markup.                                   |
| `dashboard.js`    | Dashboard logic (rendering, filters, charts).       |
| `dashboard.css`   | Dashboard styling (light/dark themes).              |

## Notes

- Prices are in **₹ (INR)**; the tracker targets the Indian storefronts of both
  retailers.
- This is a personal tracking tool. Scrape responsibly and respect each retailer's
  terms of service.

### Independent seller quotes

The fourth seller row uses `independent_price` in `books.json`: the supplied final INR price after 32% off. These manual quotes participate in best-price comparisons and totals, retain paise, and persist across scraper refreshes. No purchase URL was supplied, so a winning quote displays “Contact independent seller”. Online price history remains based on recorded scraper observations.

`independent_seller_offers.json` preserves all 49 supplied offers, including the three combos. Only exact ISBN matches are attached to the existing wishlist; combo prices are not assigned to individual volumes. Quotes were supplied on 12 September 2026 and are not refreshed automatically.
