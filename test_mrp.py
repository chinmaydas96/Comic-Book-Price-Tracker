import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import extract
import server
from mrp import apply_canonical_mrps, extract_mrp, extract_us_price


class MrpTests(unittest.TestCase):
    def test_bookswagon_uses_product_label(self):
        html = '<div>M.R.P.: ₹9,999</div><label id="ctl00_phBody_ProductDetail_lblListPrice">M.R.P. :<del>₹4,250</del><br/></label>'
        self.assertEqual(extract_mrp(html, "bookswagon"), 4250)

    def test_amazon_ignores_recommendations(self):
        recommendation = '<div>M.R.P.: <span>₹9,999</span></div>'
        self.assertIsNone(extract_mrp(recommendation, "amazon"))
        product = '<div id="corePriceDisplay_desktop_feature_div">M.R.P.: ₹123</div>'
        self.assertEqual(extract_mrp(product, "amazon"), 123)
        product = '<div id="corePriceDisplay_desktop_feature_div"></div><div id="corePrice_display_desktop_feature_div"><span>M.R.P.:</span><span>₹5,000.50</span></div>'
        self.assertEqual(extract_mrp(recommendation + product, "amazon"), 5000.5)

    def test_missing_and_zero_mrp(self):
        for html in ['', '<div id="corePrice_feature_div">₹2,000</div>', '<div id="corePrice_feature_div">M.R.P.: ₹0</div>']:
            self.assertIsNone(extract_mrp(html, "amazon"))

    def test_flipkart_reference_is_not_selling_price(self):
        self.assertEqual(extract_mrp('{"ppd":{"fsp":3000,"mrp":5000}}', "flipkart"), 5000)
        self.assertIsNone(extract_mrp('{"ppd":{"fsp":3000}}', "flipkart"))

    def test_amazon_mobile_price_and_reference_stay_together(self):
        mobile = '<div id="corePrice_feature_div">M.R.P.: ₹5,000</div>'
        with patch.object(extract, 'fetch_amazon_html', side_effect=['blocked', mobile]), patch.object(extract, 'extract_amazon_price', side_effect=[None, '₹3,000']):
            self.assertEqual(extract.fetch_amazon_price('https://www.amazon.in/dp/1779528191', include_mrp=True), (3000, 5000))

    def test_transient_failure_preserves_price_reference_pair(self):
        book = {'id': 'test', 'amazon_url': 'https://www.amazon.in/dp/1779528191'}
        with patch.object(extract, 'fetch_amazon_price', return_value=(None, None)):
            item = extract.scrape_book(book, '2026-09-21', {'test': {'amazon_price': 3000, 'amazon_mrp': 5000}})
        self.assertEqual((item['amazon_price'], item['amazon_mrp']), (3000, 5000))
        with patch.object(extract, 'fetch_amazon_price', return_value=(2500, None)):
            item = extract.scrape_book(book, '2026-09-21', {'test': {'amazon_price': 3000, 'amazon_mrp': 5000}})
        self.assertIsNone(item['amazon_mrp'])

    def test_history_keeps_reference_for_same_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history.json'
            path.write_text(json.dumps([
                {'items': [{'id': 'test', 'amazon_price': 3000, 'amazon_mrp': 5000}]},
                {'items': [{'id': 'test', 'amazon_price': 2500}]},
            ]))
            known = extract.load_last_known(path)
            self.assertEqual(known['test']['amazon_price'], 2500)
            self.assertIsNone(known['test']['amazon_mrp'])

    def test_seller_quote_requires_matching_isbn_and_final_price(self):
        with tempfile.TemporaryDirectory() as folder:
            books = Path(folder) / 'books.json'
            offers = Path(folder) / 'offers.json'
            books.write_text(json.dumps([
                {'id': 'match', 'bookswagon_url': 'https://example.com/9781779521194', 'independent_price': 68},
                {'id': 'changed', 'bookswagon_url': 'https://example.com/9781779521194', 'independent_price': 60},
            ]))
            offers.write_text(json.dumps({'offers': [{'isbn': '9781779521194', 'original_price': 100, 'final_price': 68}]}))
            with patch.object(server, 'BOOKS_PATH', books), patch.object(server, 'SELLER_OFFERS_PATH', offers):
                result = server.load_books()
            self.assertEqual(result[0]['independent_original_price'], 100)
            self.assertNotIn('independent_original_price', result[1])

    def test_canonical_mrp_uses_only_bookswagon(self):
        books = [
            {'id': 'a', 'bookswagon_url': 'https://bw/a', 'mrp': 4250, 'mrp_store': 'Amazon'},
            {'id': 'b', 'bookswagon_url': 'https://bw/b', 'mrp': 100, 'mrp_store': 'Bookswagon', 'mrp_checked_at': 'old'},
            {'id': 'c', 'mrp': 9, 'mrp_store': 'Amazon', 'mrp_source': 'x'},
        ]
        items = [
            {'id': 'a', 'bookswagon_mrp': 4850, 'amazon_mrp': 4250},
            {'id': 'b', 'bookswagon_mrp': None, 'amazon_mrp': 90},
            {'id': 'c', 'bookswagon_mrp': None, 'amazon_mrp': 9},
        ]
        self.assertTrue(apply_canonical_mrps(books, items, 'now'))
        self.assertEqual((books[0]['mrp'], books[0]['mrp_store'], books[0]['mrp_source'], books[0]['mrp_checked_at']), (4850, 'Bookswagon', 'https://bw/a', 'now'))
        self.assertEqual((books[1]['mrp'], books[1]['mrp_checked_at']), (100, 'old'))
        self.assertFalse({'mrp', 'mrp_store', 'mrp_source'} & books[2].keys())

    def test_flipkart_out_of_stock_listing_has_no_price(self):
        ppd = '"ppd":{"fsp":8017,"finalPrice":8017,"mrp":12750}'
        sold_out = ppd + ',"available":false,"serviceable":false,"isPreBook":false'
        in_stock = ppd + ',"available":true,"serviceable":false,"isPreBook":false'
        legacy = ppd + ',"available":false,"isPreBook":false'
        self.assertIsNone(extract.extract_flipkart_price(sold_out))
        self.assertIsNone(extract.extract_flipkart_price(legacy))
        self.assertEqual(extract.extract_flipkart_price(in_stock), 8017)
        # Without the flag, the page's own stock markers still decide.
        self.assertIsNone(extract.extract_flipkart_price(ppd + '"availability":"https://schema.org/OutOfStock"'))
        self.assertIsNone(extract.extract_flipkart_price(ppd + '"productActionButtonType":"NOTIFY_ME_V4"'))
        self.assertEqual(extract.extract_flipkart_price(ppd), 8017)

    def test_bookswagon_ship_days_and_damaged_flag(self):
        parse = extract.extract_bookswagon_ship_days
        label = '<label id="ctl00_phBody_ProductDetail_lblBusiness">{}</label>'
        self.assertEqual(parse(label.format('Ships within <b>10-12 Business Days</b>')), [10, 12])
        self.assertEqual(parse(label.format('Ships within <b>1-2 Business Days</b>')), [1, 2])
        self.assertEqual(parse(label.format('Ships in 2 Days')), [2, 2])
        # Shipping text outside the product label (recommendations, banners) is ignored.
        self.assertIsNone(parse('<div>Ships within 1-2 Business Days</div>'))
        self.assertIsNone(parse(''))
        book = {'id': 't', 'bookswagon_url': 'https://bw/t'}
        page = '"priceCurrency":"INR","price":"500","availability":"InStock" <h1>T</h1> <label id="ctl00_phBody_ProductDetail_lblBusiness">Ships within <b>{}</b> Business Days</label>'
        for window, damaged in (('1-2', True), ('10-12', False), ('3-5', False)):
            with patch.object(extract, 'fetch_html', return_value=page.format(window)):
                item = extract.scrape_book(book, 'd', {})
            self.assertEqual(item['bookswagon_damaged'], damaged, window)
        # Out of stock: nothing to flag.
        with patch.object(extract, 'fetch_html', return_value=page.format('1-2').replace('InStock', 'OutOfStock')):
            self.assertFalse(extract.scrape_book(book, 'd', {})['bookswagon_damaged'])
        # A failed fetch keeps the ship window paired with the carried-forward price.
        with patch.object(extract, 'fetch_html', return_value=''):
            item = extract.scrape_book(book, 'd', {'t': {'price': 500, 'in_stock': True, 'bookswagon_ship_days': [1, 2]}})
        self.assertEqual((item['price'], item['bookswagon_damaged']), (500, True))

    def test_us_price_times_rate_wins_over_bookswagon(self):
        books = [
            {'id': 'a', 'bookswagon_url': 'https://bw/9781799508045', 'us_price': 49.99, 'mrp': 4850, 'mrp_store': 'Bookswagon'},
            {'id': 'b', 'bookswagon_url': 'https://bw/9781779525932', 'us_price': 150.0},
        ]
        items = [{'id': 'a', 'bookswagon_mrp': 4850}, {'id': 'b', 'bookswagon_mrp': 14550}]
        self.assertTrue(apply_canonical_mrps(books, items, 'now'))
        self.assertEqual((books[0]['mrp'], books[0]['mrp_store']), (4499, 'US price × 90'))
        self.assertEqual(books[1]['mrp'], 13500)
        self.assertIn('9781779525932', books[1]['mrp_source'])
        # Nothing changes on a re-run, so books.json is not rewritten every scrape.
        self.assertFalse(apply_canonical_mrps(books, items, 'later'))
        self.assertEqual(books[1]['mrp_checked_at'], 'now')

    def test_us_price_reads_book_not_carousel(self):
        page = ('<div class="book-detail-price"><div class="price-usd">$<span class="price-numbers" '
                'data-component="book-detail-meta-price-numbers">100.00</span> US</div></div>'
                '<span class="price-usa">$17.99 US</span>')
        self.assertEqual(extract_us_price(page), 100.0)
        self.assertIsNone(extract_us_price('<span class="price-usa">$17.99 US</span>'))


if __name__ == '__main__':
    unittest.main()
