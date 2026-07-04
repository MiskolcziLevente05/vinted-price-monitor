import os
import re
import time
import random
import json
import sqlite3
from datetime import datetime

from dotenv import load_dotenv
from bs4 import BeautifulSoup
import requests

# ─── Global Switches ──────────────────────────────────────────────────────────
MOCK_MODE = True
DISCORD_ENABLED = True
EXPORT_TO_JSON = True

# ─── Configuration ────────────────────────────────────────────────────────────

def load_config():
    """Load DISCORD_WEBHOOK_URL and VINTED_TARGET_URL from .env file."""
    load_dotenv()
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    target_url = os.getenv("VINTED_TARGET_URL")
    if DISCORD_ENABLED and not webhook_url:
        raise ValueError("DISCORD_WEBHOOK_URL is not set in .env (or set DISCORD_ENABLED = False)")
    if not target_url:
        raise ValueError("VINTED_TARGET_URL is not set in .env (or set MOCK_MODE = false)")
    return webhook_url, target_url


# ─── Page Source Acquisition ──────────────────────────────────────────────────

def get_page_source(url):
    """Return HTML page source.
    When MOCK_MODE is True, read from mock/test_vinted_szep.html.
    When False, use Selenium + selenium-stealth to fetch live page.
    """
    if MOCK_MODE:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        mock_path = os.path.join(base_dir, "mock", "test_vinted_szep.html")
        if not os.path.isfile(mock_path):
            raise FileNotFoundError(f"Mock file not found: {mock_path}")
        with open(mock_path, "r", encoding="utf-8") as f:
            return f.read()

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium_stealth import stealth

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)
    stealth(
        driver,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        platform="Win32",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True,
    )

    try:
        driver.get(url)
        time.sleep(5)
        return driver.page_source
    finally:
        driver.quit()


# ─── HTML Parsing ─────────────────────────────────────────────────────────────

def parse_listings(html):
    """Extract item listings from Vinted HTML using BeautifulSoup.
    Targets cards via data-testid starting with 'grid-item'.
    Returns a list of dicts with keys: id, title, price, url.
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []

    cards = soup.select('[data-testid^="grid-item"]')
    for card in cards:
        try:
            anchor = card.find("a", href=re.compile(r"/items/\d+"))
            if not anchor:
                continue
            href = anchor.get("href", "")
            full_url = f"https://www.vinted.hu{href}" if href.startswith("/") else href

            match = re.search(r"/items/(\d+)", href)
            item_id = match.group(1) if match else None
            if not item_id:
                continue

            title = anchor.get("title", "").split(",")[0].strip()

            if not title:
                title_el = card.select_one('[data-testid$="--description-title"]')
                title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                img = card.find("img", alt=True)
                if img and img["alt"]:
                    title = img["alt"].split(",")[0].strip()

            price_el = card.select_one('[data-testid$="--price-text"]')
            price = price_el.get_text(strip=True) if price_el else "N/A"

            items.append({"id": item_id, "title": title, "price": price, "url": full_url})
        except Exception as e:
            print(f"[PARSE ERROR] Skipping a card: {e}")
            continue

    return items


# ─── Database Layer ───────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vinted_monitor.db")


def init_db():
    """Create SQLite database and listings table if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id         TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            price      TEXT NOT NULL,
            url        TEXT NOT NULL,
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def check_and_insert(conn, item):
    """Return True if item is new (inserted), False if already exists."""
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM listings WHERE id = ?", (item["id"],))
    if cursor.fetchone():
        return False
    cursor.execute(
        "INSERT INTO listings (id, title, price, url) VALUES (?, ?, ?, ?)",
        (item["id"], item["title"], item["price"], item["url"]),
    )
    conn.commit()
    return True


# ─── Discord Notifications ────────────────────────────────────────────────────

def send_discord_alert(webhook_url, item):
    """Send a detailed embed alert to Discord via webhook."""
    embed = {
        "embeds": [
            {
                "title": item["title"][:256],
                "url": item["url"],
                "color": 0x00D084,
                "fields": [
                    {"name": "Price", "value": item["price"], "inline": True},
                    {"name": "Link", "value": item["url"], "inline": False},
                ],
                "footer": {"text": f"Item ID: {item['id']}"},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
        ]
    }
    try:
        resp = requests.post(webhook_url, json=embed, timeout=15)
        resp.raise_for_status()
        print(f"[DISCORD] Alert sent for item {item['id']}")
    except requests.RequestException as e:
        print(f"[DISCORD] Failed to send alert: {e}")


# ─── JSON Export ──────────────────────────────────────────────────────────────

EXPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "found_items.json")


def append_to_file(item):
    """Append a new item to found_items.json as a JSON array."""
    items = []
    if os.path.isfile(EXPORT_PATH):
        try:
            with open(EXPORT_PATH, "r", encoding="utf-8") as f:
                items = json.load(f)
        except (json.JSONDecodeError, Exception):
            items = []
    items.append({
        "id": item["id"],
        "title": item["title"],
        "price": item["price"],
        "url": item["url"],
        "found_at": datetime.utcnow().isoformat() + "Z",
    })
    with open(EXPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    print(f"[FILE] Appended to {EXPORT_PATH}")


# ─── Main Polling Loop ────────────────────────────────────────────────────────

def main_loop():
    """Polling loop: fetch → parse → filter → alert → sleep with jitter."""
    webhook_url, target_url = load_config()
    conn = init_db()

    print(f"[START] Monitoring Vinted | MOCK_MODE={MOCK_MODE} | DISCORD_ENABLED={DISCORD_ENABLED} | EXPORT_TO_JSON={EXPORT_TO_JSON}")
    if not MOCK_MODE:
        print(f"[START] Target URL: {target_url}")

    while True:
        cycle_start = time.time()
        try:
            print(f"\n[CYCLE] {datetime.now().isoformat()} — Fetching page...")
            html = get_page_source(target_url)
            listings = parse_listings(html)
            print(f"[PARSE] Found {len(listings)} listing(s)")

            new_count = 0
            for item in listings:
                if check_and_insert(conn, item):
                    print(f"[NEW] Item {item['id']}: {item['title']} — {item['price']}")
                    if DISCORD_ENABLED:
                        send_discord_alert(webhook_url, item)
                    if EXPORT_TO_JSON:
                        append_to_file(item)
                    new_count += 1

            print(f"[CYCLE] {new_count} new item(s) — {len(listings)} total parsed")

        except FileNotFoundError as e:
            print(f"[FATAL] {e}")
            break
        except Exception as e:
            print(f"[ERROR] Cycle failed: {e}")

        if MOCK_MODE:
            delay = random.uniform(10, 30)
        else:
            delay = random.uniform(300, 600)
        elapsed = time.time() - cycle_start
        sleep_time = max(0, delay - elapsed)
        print(f"[SLEEP] Waiting {sleep_time:.1f}s...")

        print("\n[STOP] press Ctrl+C")
        time.sleep(sleep_time)


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C pressed — shutting down.")
