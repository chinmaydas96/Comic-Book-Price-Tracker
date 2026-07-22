import json
import os
import re
import sys
from datetime import date
from urllib.request import Request, urlopen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOOKS_PATH = os.path.join(BASE_DIR, "books.json")


def load_books(books_path=BOOKS_PATH):
    """Load the master list. This is the single source of truth for which
    titles get tracked; removing an entry here removes it from scraping,
    the stored results, and (via the server filter) the dashboard."""
    with open(books_path, "r", encoding="utf-8") as handle:
        return json.load(handle)

def fetch_html(url):
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(req, timeout=20) as response:
        return response.read().decode("utf-8", errors="ignore")


def fetch_amazon_html(url):
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept-Encoding": "identity",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    with urlopen(req, timeout=20) as response:
        return response.read().decode("utf-8", errors="ignore")


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


def fetch_amazon_price(url):
    html = fetch_amazon_html(url)
    return normalize_price(extract_amazon_price(html))


def collect_items(snapshot_date, books=None):
    if books is None:
        books = load_books()

    items = []
    for book in books:
        url = book.get("bookswagon_url")
        amazon_url = book.get("amazon_url")

        # Scrape Bookswagon price (skip if no link yet).
        name = book.get("name")
        price_value = None
        in_stock = None
        if url:
            try:
                html = fetch_html(url)
                scraped_name = extract_book_name(html)
                if scraped_name:
                    name = scraped_name
                price, _currency, in_stock = extract_price_and_stock(html)
                price_value = normalize_price(price)
            except Exception:
                price_value = None
                in_stock = None

        # Scrape Amazon India price (skip if no link yet).
        amazon_price = None
        if amazon_url:
            try:
                amazon_price = fetch_amazon_price(amazon_url)
            except Exception:
                amazon_price = None

        items.append(
            {
                "id": book.get("id"),
                "name": name,
                "franchise": book.get("franchise"),
                "era": book.get("era"),
                "url": url,
                "price": price_value,
                "in_stock": in_stock,
                "amazon_url": amazon_url,
                "amazon_price": amazon_price,
                "snapshot_date": snapshot_date,
            }
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