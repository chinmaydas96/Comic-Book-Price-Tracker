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
- **Today's movers** — a summary panel of price **drops** since the last snapshot.
- **Franchise filter** — filter the whole dashboard by franchise, each with its own
  accent color.
- **"Not available" cap** — any price above ₹15,000 is treated as a placeholder /
  inflated listing and shown as *Not available* rather than a real buyable price.
- **Light / dark theme** toggle.

## How it works

```
books.json ──▶ extract.py ──▶ results.json (latest run)
   (the                └────▶ history.json  (append-only daily snapshots)
 tracked list)
                                    │
                              server.py  ──▶  /api/books, /api/history, /api/refresh
                                    │
                              dashboard.html + dashboard.js + dashboard.css
```

- **`books.json`** is the master list of tracked titles (id, name, franchise, era,
  and the Bookswagon / Amazon India URLs).
- **`extract.py`** fetches each listing, parses the price and stock status, writes
  the latest run to `results.json`, and appends a dated snapshot to `history.json`.
- **`server.py`** serves the static dashboard and exposes the data as JSON APIs. It
  can trigger a fresh scrape on demand and auto-reloads when source files change.
- The **dashboard** fetches `/api/books` and `/api/history` and renders everything
  client-side.

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

To pull fresh prices, either click **Refresh** in the dashboard header (which calls
`/api/refresh`), or run the scraper directly:

```bash
python3 extract.py
```

Each run appends one dated snapshot to `history.json`, so price history builds up
over time (ideally run daily).

### API endpoints

| Endpoint        | Description                                        |
| --------------- | -------------------------------------------------- |
| `/api/books`    | The tracked book list (`books.json`).              |
| `/api/history`  | All dated price snapshots, filtered to current books. |
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
- Optional `"manual_low": { "price": 5390, "date": "2025-10-21", "store": "Amazon" }`
  seeds a known historical low.

## Configuration

- **Price cap** — the "Not available" threshold is the `MAX_PRICE` constant in
  `dashboard.js` (default `15000`).
- **Port** — set in `server.py` (default `8001`).

## Project structure

| File              | Purpose                                             |
| ----------------- | --------------------------------------------------- |
| `server.py`       | Static file + JSON API server, on-demand refresh.   |
| `extract.py`      | Price/stock scraper; writes results and history.    |
| `books.json`      | Master list of tracked titles.                      |
| `history.json`    | Append-only dated price snapshots.                  |
| `results.json`    | Latest scrape output.                               |
| `dashboard.html`  | Dashboard markup.                                   |
| `dashboard.js`    | Dashboard logic (rendering, filters, charts).       |
| `dashboard.css`   | Dashboard styling (light/dark themes).              |

## Notes

- Prices are in **₹ (INR)**; the tracker targets the Indian storefronts of both
  retailers.
- This is a personal tracking tool. Scrape responsibly and respect each retailer's
  terms of service.
