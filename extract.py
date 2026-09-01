import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.request import Request, urlopen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOOKS_PATH = os.path.join(BASE_DIR, "books.json")

BOOKSWAGON_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
}

# BooksWagon becomes unreliable when a refresh opens all product pages at once.
# Serialize and gently pace requests so the storefront does not throttle them.
BOOKSWAGON_REQUEST_SLOTS = threading.BoundedSemaphore(1)
BOOKSWAGON_REQUEST_GAP_SECONDS = 1.0
_bookswagon_next_request_at = 0.0


def load_books(books_path=BOOKS_PATH):
    """Load the master list. This is the single source of truth for which
    titles get tracked; entries marked disabled are skipped by scraping and
    hidden from the dashboard without deleting their configuration."""
    with open(books_path, "r", encoding="utf-8") as handle:
        return [book for book in json.load(handle) if not book.get("disabled", False)]

def fetch_html(url, attempts=2):
    """Fetch a BooksWagon page with bounded concurrency and retries."""
    global _bookswagon_next_request_at
    for attempt in range(attempts):
        req = Request(url, headers=BOOKSWAGON_HEADERS)
        try:
            with BOOKSWAGON_REQUEST_SLOTS:
                wait_seconds = _bookswagon_next_request_at - time.monotonic()
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                try:
                    with urlopen(req, timeout=20) as response:
                        html = response.read().decode("utf-8", errors="ignore")
                finally:
                    _bookswagon_next_request_at = (
                        time.monotonic() + BOOKSWAGON_REQUEST_GAP_SECONDS
                    )
            if html:
                return html
        except Exception:
            pass
        if attempt < attempts - 1:
            time.sleep(1.5 * (attempt + 1))
    return ""


AMAZON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "identity",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "DNT": "1",
}


def looks_blocked(html):
    """True if Amazon returned a bot/CAPTCHA page instead of the product page."""
    if len(html) < 6000:
        return True
    lowered = html.lower()
    return "captcha" in lowered or "api-services-support@amazon" in lowered


def fetch_amazon_html(url, attempts=3):
    """Fetch an Amazon page, retrying past intermittent bot-block responses."""
    html = ""
    for attempt in range(attempts):
        req = Request(url, headers=AMAZON_HEADERS)
        try:
            with urlopen(req, timeout=20) as response:
                html = response.read().decode("utf-8", errors="ignore")
        except Exception:
            html = ""
        if html and not looks_blocked(html):
            return html
        if attempt < attempts - 1:
            time.sleep(1.5 * (attempt + 1))
    return html


def extract_book_name(html):
    script_match = re.search(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if script_match:
        script_body = script_match.group(1)
        name_match = re.search(r'"name"\s*:\s*"(?P<name>[^"]+)"', script_body)
        if name_match:
            return name_match.group("name").strip()

    title_match = re.search(r"<title>(?P<title>.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = re.sub(r"\s+", " ", title_match.group("title")).strip()
        if title:
            return title

    return None


def extract_price_and_stock(html):
    price_match = re.search(
        r'priceCurrency"\s*:\s*"(?P<currency>[^"]+)"\s*,\s*"price"\s*:\s*"(?P<price>[^"]+)"',
        html,
    )
    availability_match = re.search(
        r'availability"\s*:\s*"(?P<availability>[^"]+)"',
        html,
    )

    if not price_match:
        return None, None, None

    in_stock = None
    if availability_match:
        in_stock = "InStock" in availability_match.group("availability")

    return (
        price_match.group("price"),
        price_match.group("currency"),
        in_stock,
    )


def extract_amazon_price(html):
    core_price = extract_core_price(html)
    if core_price:
        return core_price

    price_to_pay_match = re.search(
        r'"priceToPay"\s*:\s*\{.*?"value"\s*:\s*(?P<amount>[0-9]+(?:\.[0-9]+)?)',
        html,
        re.DOTALL,
    )
    if price_to_pay_match:
        return f"₹{price_to_pay_match.group('amount')}"

    # Amazon's mobile book page exposes third-party buying options as
    # "Other New from ₹…" when there is no regular desktop buy box. The
    # desktop URL is also more likely to return a bot-block page, so this is
    # the live price picked up by fetch_amazon_price's mobile fallback.
    mobile_new_offer_match = re.search(
        r'aria-label="Other\s+New\s+from\s+(?P<price>₹\s?[\d,]+(?:\.\d{1,2})?)"',
        html,
        re.IGNORECASE,
    )
    if mobile_new_offer_match:
        return mobile_new_offer_match.group("price").replace(" ", "")

    mobile_format_price_match = re.search(
        r'class="slot-price".*?aria-label="from\s+(?P<price>₹\s?[\d,]+(?:\.\d{1,2})?)"',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if mobile_format_price_match:
        return mobile_format_price_match.group("price").replace(" ", "")

    json_ld_price = extract_amazon_price_from_json_ld(html)
    if json_ld_price:
        return json_ld_price

    json_price_match = re.search(
        r'"displayPrice"\s*:\s*"(?P<price>₹[^"]+)"',
        html,
    )
    if json_price_match:
        return json_price_match.group("price").strip()

    amount_match = re.search(
        r'"priceAmount"\s*:\s*(?P<amount>[0-9]+(?:\.[0-9]+)?)',
        html,
    )
    if amount_match:
        return f"₹{amount_match.group('amount')}"

    priceblock_match = re.search(
        r'id="priceblock_(?:ourprice|dealprice|saleprice)"[^>]*>\s*(?P<price>₹\s?[\d,]+(?:\.\d{1,2})?)\s*<',
        html,
    )
    if priceblock_match:
        return priceblock_match.group("price").replace(" ", "")

    return None


def extract_amazon_price_from_json_ld(html):
    scripts = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    for script in scripts:
        script_body = script.strip()
        if not script_body:
            continue
        try:
            data = json.loads(script_body)
        except json.JSONDecodeError:
            continue
        price = find_offer_price(data)
        if price:
            if isinstance(price, str) and "₹" in price:
                return price.strip()
            return f"₹{price}"
    return None


def find_offer_price(payload):
    if isinstance(payload, dict):
        if payload.get("@type") in ("Offer", "AggregateOffer"):
            currency = payload.get("priceCurrency")
            price_value = payload.get("price") or payload.get("lowPrice")
            if price_value is not None and (currency in (None, "INR")):
                return price_value
        for value in payload.values():
            price = find_offer_price(value)
            if price is not None:
                return price
    elif isinstance(payload, list):
        for item in payload:
            price = find_offer_price(item)
            if price is not None:
                return price
    return None


def extract_core_price(html):
    anchor = 'id="corePrice_feature_div"'
    start_index = html.find(anchor)
    if start_index == -1:
        return None
    snippet = html[start_index : start_index + 8000]
    if "Currently unavailable" in snippet:
        return None

    price_to_pay_match = re.search(
        r'priceToPay.*?a-price-symbol">\s*₹\s*</span>\s*<span class="a-price-whole">(?P<whole>[\d,]+)</span>(?:\s*<span class="a-price-decimal">\.</span>\s*<span class="a-price-fraction">(?P<fraction>\d{1,2})</span>)?',
        snippet,
        re.DOTALL,
    )
    if price_to_pay_match:
        whole = price_to_pay_match.group("whole")
        fraction = price_to_pay_match.group("fraction")
        if fraction:
            return f"₹{whole}.{fraction}"
        return f"₹{whole}"

    savings_offscreen_match = re.search(
        r'aok-offscreen">\s*(?P<price>₹\s?[\d,]+(?:\.\d{1,2})?)\s+with\s',
        snippet,
    )
    if savings_offscreen_match:
        return savings_offscreen_match.group("price").replace(" ", "")

    return None


def normalize_price(price_text):
    if not price_text:
        return None
    cleaned = price_text.replace("₹", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def amazon_mobile_url(url):
    asin_match = re.search(r'/(?:dp|gp/product)/(?P<asin>[A-Z0-9]{10})(?:[/?]|$)', url, re.IGNORECASE)
    if not asin_match:
        return None
    return f"https://www.amazon.in/gp/aw/d/{asin_match.group('asin').upper()}"


def fetch_amazon_price(url):
    html = fetch_amazon_html(url)
    price = normalize_price(extract_amazon_price(html))
    if price is not None:
        return price

    # The standard desktop endpoint is frequently bot-blocked. Amazon's mobile
    # product endpoint often remains readable and contains the same live buybox
    # or "Other New" offer price.
    mobile_url = amazon_mobile_url(url)
    if mobile_url and mobile_url != url:
        mobile_html = fetch_amazon_html(mobile_url, attempts=1)
        return normalize_price(extract_amazon_price(mobile_html))
    return None


FLIPKART_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "identity",
}


def extract_flipkart_price(html):
    """Main product's standard selling price (the "fsp" in Flipkart's "ppd" block).
    Returns None when out of stock. Flipkart keeps showing an "fsp" price even on
    sold-out listings (with a "Notify Me" button), so we also require the buybox
    availability flag to be true; otherwise the price is not actually buyable."""
    match = re.search(r'"ppd":\s*\{[^}]*?"fsp":(?P<price>\d{2,7})', html)
    if not match:
        return None
    avail = re.search(r'"available":(true|false),"isPreBook"', html)
    if avail and avail.group(1) == "false":
        return None
    return float(match.group("price"))


def fetch_flipkart_price(url, attempts=3):
    for attempt in range(attempts):
        try:
            req = Request(url, headers=FLIPKART_HEADERS)
            with urlopen(req, timeout=25) as response:
                html = response.read().decode("utf-8", errors="ignore")
        except Exception:
            html = ""
        price = extract_flipkart_price(html)
        if price is not None:
            return price
        if attempt < attempts - 1:
            time.sleep(1.5 * (attempt + 1))
    return None


def load_last_known(history_path="history.json"):
    """Most recent non-null store prices per book, scanning history newest-first.
    Used to carry prices forward when a scrape is transiently blocked."""
    try:
        with open(history_path, "r", encoding="utf-8") as handle:
            history = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    known = {}
    for snapshot in reversed(history):
        for item in snapshot.get("items", []):
            key = item.get("id") or item.get("url")
            if not key:
                continue
            entry = known.setdefault(key, {})
            if "price" not in entry and item.get("price") is not None:
                entry["price"] = item["price"]
                entry["in_stock"] = item.get("in_stock")
            if "amazon_price" not in entry and item.get("amazon_price") is not None:
                entry["amazon_price"] = item["amazon_price"]
            if "flipkart_price" not in entry and item.get("flipkart_price") is not None:
                entry["flipkart_price"] = item["flipkart_price"]
    return known


def scrape_book(book, snapshot_date, last_known):
    """Scrape one book's price across all three stores. Runs in a worker thread."""
    url = book.get("bookswagon_url")
    amazon_url = book.get("amazon_url")
    flipkart_url = book.get("flipkart_url")
    key = book.get("id") or url

    # Scrape Bookswagon price (skip if no link yet).
    name = book.get("name")
    price_value = None
    in_stock = None
    if url:
        try:
            html = fetch_html(url)
            scraped_name = extract_book_name(html)
            # The generic homepage has misleading JSON-LD (name "Bookswagon"
            # and price ₹1). Treat it as a failed product response.
            if scraped_name and scraped_name.strip().casefold() == "bookswagon":
                raise ValueError("BooksWagon returned its homepage")
            if scraped_name:
                name = scraped_name
            price, _currency, in_stock = extract_price_and_stock(html)
            price_value = normalize_price(price)
        except Exception:
            price_value = None
            in_stock = None
        # A timeout or throttled response should not blank a previously known
        # BooksWagon price. Actual out-of-stock responses retain in_stock=False.
        if price_value is None and in_stock is None:
            fallback = last_known.get(key, {})
            if fallback.get("price") is not None:
                price_value = fallback["price"]
                in_stock = fallback.get("in_stock")

    # Scrape Amazon India price (skip if no link yet).
    amazon_price = None
    if amazon_url:
        try:
            amazon_price = fetch_amazon_price(amazon_url)
        except Exception:
            amazon_price = None
        # Amazon intermittently serves a bot page; if this run couldn't get a
        # price, keep the last known one instead of blanking it out.
        if amazon_price is None:
            fallback = last_known.get(key, {}).get("amazon_price")
            if fallback is not None:
                amazon_price = fallback

    # Scrape Flipkart price (skip if no link yet). A missing price here means
    # out of stock (the page still loads), so it's left as None, not carried
    # forward — otherwise an out-of-stock book would show a stale price.
    flipkart_price = None
    if flipkart_url:
        try:
            flipkart_price = fetch_flipkart_price(flipkart_url)
        except Exception:
            flipkart_price = None

    return {
        "id": book.get("id"),
        "name": name,
        "franchise": book.get("franchise"),
        "era": book.get("era"),
        "url": url,
        "price": price_value,
        "in_stock": in_stock,
        "amazon_url": amazon_url,
        "amazon_price": amazon_price,
        "flipkart_url": flipkart_url,
        "flipkart_price": flipkart_price,
        "snapshot_date": snapshot_date,
    }


def collect_items(snapshot_date, books=None, max_workers=8):
    if books is None:
        books = load_books()

    last_known = load_last_known()
    # Scrape books concurrently; each book still hits its stores sequentially,
    # but many books run in parallel. ThreadPoolExecutor.map preserves order.
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        items = list(
            executor.map(lambda b: scrape_book(b, snapshot_date, last_known), books)
        )
    return items


def update_history(snapshot_date, items, history_path="history.json"):
    try:
        with open(history_path, "r", encoding="utf-8") as handle:
            history = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        history = []

    if history and history[-1].get("date") == snapshot_date:
        history[-1] = {"date": snapshot_date, "items": items}
    else:
        history.append({"date": snapshot_date, "items": items})

    with open(history_path, "w", encoding="utf-8") as handle:
        json.dump(history, handle, indent=2)


def main():
    args = sys.argv[1:]
    if args:
        url = args[0]
        price = fetch_amazon_price(url)
        if price:
            print(f"{url} | price: {price}")
        else:
            print(f"{url} | price: not found")
        return

    snapshot_date = date.today().isoformat()
    items = collect_items(snapshot_date)
    for item in items:
        label = item.get("name") or item.get("id")
        if not item.get("url") and not item.get("amazon_url"):
            print(f"{label} | link pending")
            continue

        bookswagon = item["price"] if item["price"] is not None else "not found"
        amazon = item["amazon_price"] if item["amazon_price"] is not None else "not found"
        print(f"{label} | bookswagon: {bookswagon} | amazon: {amazon}")

    with open("results.json", "w", encoding="utf-8") as handle:
        json.dump(items, handle, indent=2)
    update_history(snapshot_date, items)


if __name__ == "__main__":
    main()
