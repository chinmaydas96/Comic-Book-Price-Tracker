import json
import os
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_PATH = os.path.join(BASE_DIR, "history.json")
EXTRACT_PATH = os.path.join(BASE_DIR, "extract.py")
BOOKS_PATH = os.path.join(BASE_DIR, "books.json")

RUN_LOCK = threading.Lock()

WATCH_EXTENSIONS = {".py", ".js", ".css", ".html", ".json"}


def iter_watch_files():
    for filename in os.listdir(BASE_DIR):
        path = os.path.join(BASE_DIR, filename)
        if not os.path.isfile(path):
            continue
        _, ext = os.path.splitext(filename)
        if ext in WATCH_EXTENSIONS:
            yield path


def get_latest_mtime():
    latest = 0.0
    for path in iter_watch_files():
        try:
            latest = max(latest, os.path.getmtime(path))
        except OSError:
            continue
    return latest


def load_books():
    try:
        with open(BOOKS_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def load_history():
    """Return history filtered to titles that are still in the master list.
    Anything removed from books.json disappears from the dashboard, including
    its historical data points."""
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as handle:
            history = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    books = load_books()
    allowed_ids = {book.get("id") for book in books}
    # Old snapshots predate stable ids; fall back to matching on the
    # Bookswagon url so their historical prices are still shown.
    allowed_urls = {book.get("bookswagon_url") for book in books if book.get("bookswagon_url")}

    for snapshot in history:
        snapshot["items"] = [
            item for item in snapshot.get("items", [])
            if item.get("id") in allowed_ids or item.get("url") in allowed_urls
        ]
    return history


def run_extract():
    with RUN_LOCK:
        result = subprocess.run(
            [sys.executable, EXTRACT_PATH],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
    return result.returncode == 0, result.stdout, result.stderr


class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        # Land visitors on the dashboard instead of a directory listing.
        if self.path == "/":
            self.path = "/dashboard.html"

        if self.path == "/api/books":
            payload = json.dumps(load_books()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if self.path == "/api/history":
            history = load_history()
            payload = json.dumps(history).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if self.path == "/api/refresh":
            success, stdout, stderr = run_extract()
            status = 200 if success else 500
            payload = json.dumps(
                {
                    "success": success,
                    "stdout": stdout,
                    "stderr": stderr,
                }
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        super().do_GET()


def run_server():
    # Hosts like Render assign the port via the PORT env var.
    port = int(os.environ.get("PORT", "8001"))
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"Serving on http://0.0.0.0:{port}")
    server.serve_forever()


def run_with_reloader():
    env = dict(os.environ)
    env["DASHBOARD_CHILD"] = "1"
    last_mtime = get_latest_mtime()
    process = subprocess.Popen([sys.executable, __file__], env=env, cwd=BASE_DIR)

    try:
        while True:
            time.sleep(1)
            latest = get_latest_mtime()
            if latest > last_mtime:
                last_mtime = latest
                process.terminate()
                process.wait(timeout=5)
                process = subprocess.Popen(
                    [sys.executable, __file__], env=env, cwd=BASE_DIR
                )
    except KeyboardInterrupt:
        process.terminate()
        process.wait(timeout=5)


def main():
    # Production (e.g. Render) runs the server directly. The file-watching
    # auto-reloader is a dev convenience, opt in with DASHBOARD_RELOAD=1.
    if os.environ.get("DASHBOARD_CHILD") == "1":
        run_server()
    elif os.environ.get("DASHBOARD_RELOAD") == "1":
        run_with_reloader()
    else:
        run_server()


if __name__ == "__main__":
    main()
