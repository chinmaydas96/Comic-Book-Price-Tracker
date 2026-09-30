"""Read explicit INR reference prices, never recommendation-card prices."""
import re
from html.parser import HTMLParser


class _ReferencePriceParser(HTMLParser):
    def __init__(self, store):
        super().__init__(convert_charrefs=True)
        self.store = store
        self.stack = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        ident = attrs.get("id", "").lower()
        capture = (
            self.store == "bookswagon" and ident.endswith("productdetail_lbllistprice")
        ) or (
            self.store == "amazon" and ident in {
                "coreprice_feature_div", "coreprice_desktop", "coreprice_mobile_feature_div",
                "coreprice_desktop_feature_div", "coreprice_display_desktop_feature_div",
                "corepricedisplay_desktop_feature_div", "corepricedisplay_desktop",
                "apex_desktop", "apex_mobile",
            }
        )
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, capture))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if any(capture for _, capture in self.stack) and not any(tag in {"script", "style"} for tag, _ in self.stack):
            self.text.append(data)


def extract_mrp(html, store):
    if store == "flipkart":
        match = re.search(r'"ppd"\s*:\s*\{[^{}]*?"mrp"\s*:\s*(\d+(?:\.\d+)?)', html)
    else:
        parser = _ReferencePriceParser(store)
        parser.feed(html)
        text = " ".join(parser.text)
        match = re.search(r'M\.?\s*R\.?\s*P\.?\s*:?\s*(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d{1,2})?)', text, re.I)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    return value if value > 0 else None


CANONICAL_MRP_STORE = "Bookswagon"
US_PRICE_STORE = "US price × 90"
# Indian MRP for DC books = US cover price × this rate, rounded to the rupee.
USD_TO_INR = 90
PRH_BOOK_URL = "https://prhcomics.com/book/?isbn={isbn}"


def book_isbn(book):
    """ISBN-13 from the book's Bookswagon URL, else from its Amazon ISBN-10."""
    match = re.search(r"(97[89]\d{10})", book.get("bookswagon_url") or "")
    if match:
        return match.group(1)
    match = re.search(r"/dp/(\d{9}[\dX])", book.get("amazon_url") or "")
    if not match:
        return None
    core = "978" + match.group(1)[:9]
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core))
    return core + str((10 - total % 10) % 10)


def extract_us_price(html):
    """The book's own US price on its Penguin Random House Comics page;
    recommendation carousels on the same page are ignored."""
    match = re.search(
        r'data-component="book-detail-meta-price-numbers"[^>]*>\s*([\d,]+(?:\.\d{1,2})?)\s*<', html
    )
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    return value if value > 0 else None


def fetch_us_price(isbn, timeout=30):
    from urllib.request import Request, urlopen

    request = Request(PRH_BOOK_URL.format(isbn=isbn), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    return extract_us_price(html)


def apply_canonical_mrps(books, items, checked_at):
    """Give each book one MRP, the same for every seller.

    A book with a saved `us_price` (its US cover price) gets
    round(us_price × USD_TO_INR). Otherwise it falls back to Bookswagon's INR
    list price: a book whose fresh scrape found none keeps its saved Bookswagon
    value, and a value from any other source is removed. Sellers quote
    different reference prices for the same edition, so per-seller MRPs are
    never used. Returns True if any book changed."""
    fresh = {item.get("id"): item["bookswagon_mrp"] for item in items if item.get("bookswagon_mrp")}
    changed = False
    for book in books:
        if book.get("us_price"):
            update = {
                "mrp": float(round(book["us_price"] * USD_TO_INR)),
                "mrp_store": US_PRICE_STORE,
                "mrp_source": PRH_BOOK_URL.format(isbn=book_isbn(book)),
            }
            if any(book.get(key) != value for key, value in update.items()):
                book.update(update, mrp_checked_at=checked_at)
                changed = True
        elif book.get("id") in fresh:
            book.update(
                mrp=fresh[book["id"]],
                mrp_store=CANONICAL_MRP_STORE,
                mrp_source=book.get("bookswagon_url"),
                mrp_checked_at=checked_at,
            )
            changed = True
        elif "mrp" in book and book.get("mrp_store") != CANONICAL_MRP_STORE:
            for key in ("mrp", "mrp_store", "mrp_source", "mrp_checked_at"):
                book.pop(key, None)
            changed = True
    return changed
