"""A tiny in-process fake of Apple's checkout flow for buyer/supervisor tests.

Serves a product → bag → checkout → payment → place-order sequence using
Apple-style data-autom selectors, so a Flightplan written against those
selectors drives it end to end with a real browser. No network, no apple.com.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_PAGES = {
    "/product": """
        <html><body><h1>Refurbished Mac Studio</h1>
        <button data-autom="add-to-cart" onclick="location.href='/bag'">Add to Bag</button>
        </body></html>
    """,
    "/bag": """
        <html><body><h1>Bag</h1>
        <button data-autom="checkout" onclick="location.href='/checkout'">Check Out</button>
        </body></html>
    """,
    "/checkout": """
        <html><body><h1>Checkout — Shipping</h1>
        <button data-autom="fulfillment-continue-button"
                onclick="location.href='/payment'">Continue to Payment</button>
        </body></html>
    """,
    "/payment": """
        <html><body><h1>Payment</h1>
        <input data-autom="card-security-code" name="cvv" type="password"/>
        <button data-autom="payment-continue-button"
                onclick="location.href='/review'">Continue</button>
        </body></html>
    """,
    "/review": """
        <html><body><h1>Review your order</h1>
        <button data-autom="place-order-button"
                onclick="location.href='/thankyou'">Place Order</button>
        </body></html>
    """,
    "/thankyou": """
        <html><body><h1>Thank you</h1><p>Your order is confirmed.</p></body></html>
    """,
    # Session check pages
    "/bag-signedin": """
        <html><body><button data-autom="sign-out">Sign out</button></body></html>
    """,
    "/bag-signedout": """
        <html><body><button data-autom="sign-in">Sign in</button></body></html>
    """,
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        body = _PAGES.get(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *a):  # silence
        pass


class FakeShop:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"
