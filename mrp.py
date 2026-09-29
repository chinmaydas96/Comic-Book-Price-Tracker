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


def apply_canonical_mrps(books, items, checked_at):
    """Give each book one MRP, taken only from Bookswagon's INR list price.

    Sellers quote different reference prices for the same edition, so a
    per-seller MRP would make discounts inconsistent. A book whose fresh scrape
    found no Bookswagon MRP keeps its saved Bookswagon value; a saved value from
    any other source is removed. Returns True if any book changed."""
    fresh = {item.get("id"): item["bookswagon_mrp"] for item in items if item.get("bookswagon_mrp")}
    changed = False
    for book in books:
        if book.get("id") in fresh:
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
