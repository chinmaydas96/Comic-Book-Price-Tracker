"""Open large, newly detected price drops in a new Google Chrome window."""

import json
import os
import shutil
import subprocess
import sys
import tempfile


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NOTIFIED_DROPS_PATH = os.path.join(BASE_DIR, ".price_drop_notifications.json")
CHROME_ALERT_DROP_PERCENT = 8


def price_drop_percent(event):
    """Return a valid drop percentage, or None for malformed/non-drop events."""
    try:
        old_price = float(event.get("from"))
        new_price = float(event.get("to"))
    except (TypeError, ValueError):
        return None
    if old_price <= 0 or new_price >= old_price:
        return None
    return ((old_price - new_price) / old_price) * 100


def notification_key(event):
    """Identify a price transition independently of when it was detected."""
    return "|".join(
        (
            str(event.get("book_id") or event.get("name") or "unknown-book"),
            str(event.get("store") or "unknown-store"),
            f"{float(event['from']):.12g}",
            f"{float(event['to']):.12g}",
        )
    )


def load_notified_drops(path=NOTIFIED_DROPS_PATH):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return set()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid Chrome notification state: {path}") from exc
    if not isinstance(data, list):
        raise RuntimeError(f"Invalid Chrome notification state: {path}")
    return {str(key) for key in data}


def save_notified_drops(notified, path=NOTIFIED_DROPS_PATH):
    """Persist notification keys atomically so alerts survive restarts."""
    target_dir = os.path.dirname(os.path.abspath(path))
    fd, temp_path = tempfile.mkstemp(
        prefix=".price-drop-notifications-", suffix=".tmp", dir=target_dir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(sorted(notified), handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def _chrome_executable():
    candidates = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
    for candidate in candidates:
        executable = shutil.which(candidate)
        if executable:
            return executable

    if sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if not root:
                continue
            executable = os.path.join(root, "Google", "Chrome", "Application", "chrome.exe")
            if os.path.isfile(executable):
                return executable
    return None


def open_in_chrome(url):
    """Open URL in a new Chrome window. Return False when Chrome is unavailable."""
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return False

    if sys.platform == "darwin":
        result = subprocess.run(
            ["open", "-na", "Google Chrome", "--args", "--new-window", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    executable = _chrome_executable()
    if executable is None:
        return False
    try:
        subprocess.Popen(
            [executable, "--new-window", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return True


def notify_large_price_drops(
    events, state_path=NOTIFIED_DROPS_PATH, opener=open_in_chrome
):
    """Open each new >8% drop once and return the URLs successfully opened."""
    notified = load_notified_drops(state_path)
    opened_urls = []

    for event in events:
        drop_percent = price_drop_percent(event)
        url = event.get("url")
        if drop_percent is None or drop_percent <= CHROME_ALERT_DROP_PERCENT or not url:
            continue

        key = notification_key(event)
        if key in notified:
            continue
        if opener(url):
            notified.add(key)
            opened_urls.append(url)

    if opened_urls:
        save_notified_drops(notified, state_path)
    return opened_urls
