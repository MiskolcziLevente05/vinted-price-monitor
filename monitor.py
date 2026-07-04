import os
import re
import time
import random
import json
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv
from bs4 import BeautifulSoup
import requests

# ─── Global Switches ──────────────────────────────────────────────────────────
MOCK_MODE = True
DISCORD_ENABLED = True
EXPORT_TO_JSON = True
MIN_PRICE = 0

# ─── Configuration ────────────────────────────────────────────────────────────

def load_config(require_target=True):
    """Load DISCORD_WEBHOOK_URL and VINTED_TARGET_URL from .env file."""
    load_dotenv()
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    target_url = os.getenv("VINTED_TARGET_URL")
    if DISCORD_ENABLED and not webhook_url:
        raise ValueError("DISCORD_WEBHOOK_URL is not set in .env (or set DISCORD_ENABLED = False)")
    if require_target and not target_url:
        raise ValueError("VINTED_TARGET_URL is not set in .env")
    return webhook_url, target_url


# ─── Page Source Acquisition ──────────────────────────────────────────────────

def get_page_source(url, mock_mode=None):
    """Return HTML page source.
    When mock_mode is True, read from mock/test_vinted_szep.html.
    When False, use Selenium + selenium-stealth to fetch live page.
    """
    mm = mock_mode if mock_mode is not None else MOCK_MODE
    if mm:
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
    """Extract item listings from Vinted HTML using BeautifulSoup."""
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


def parse_price_numeric(price_str):
    """Extract numeric value from a Vinted price string like '1 400 Ft'."""
    if not price_str or price_str == "N/A":
        return 0
    clean = price_str.replace("\xa0", "").replace(" ", "").replace("Ft", "").strip()
    try:
        return float(clean)
    except ValueError:
        return 0


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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
        "found_at": datetime.now(timezone.utc).isoformat(),
    })
    with open(EXPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    print(f"[FILE] Appended to {EXPORT_PATH}")


# ─── Main Polling Loop ────────────────────────────────────────────────────────

def main_loop(mock_mode=None, discord_enabled=None, export_json=None, min_price=None, log_func=print, stop_event=None, target_url=None):
    """Polling loop: fetch → parse → filter → alert → sleep with jitter."""
    mm = mock_mode if mock_mode is not None else MOCK_MODE
    de = discord_enabled if discord_enabled is not None else DISCORD_ENABLED
    ej = export_json if export_json is not None else EXPORT_TO_JSON
    mp = min_price if min_price is not None else MIN_PRICE

    log = lambda msg: log_func(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    _, env_url = load_config(require_target=False)
    webhook_url, _ = load_config(require_target=not mm)
    if not target_url:
        target_url = env_url
    conn = init_db()

    log(f"Monitoring Vinted | MOCK={mm} | DISCORD={de} | JSON={ej} | MIN_PRICE={mp}")
    if not mm:
        log(f"Target URL: {target_url}")

    try:
        while not (stop_event and stop_event.is_set()):
            cycle_start = time.time()
            try:
                if stop_event and stop_event.is_set():
                    break
                log("Fetching page...")
                html = get_page_source(target_url, mock_mode=mm)
                listings = parse_listings(html)
                log(f"Found {len(listings)} listing(s)")

                new_count = 0
                for item in listings:
                    if stop_event and stop_event.is_set():
                        break
                    price_val = parse_price_numeric(item["price"])
                    if mp > 0 and price_val < mp:
                        log(f"SKIP (below min price) Item {item['id']}: {item['title']} — {item['price']}")
                        continue
                    if check_and_insert(conn, item):
                        log(f"NEW Item {item['id']}: {item['title']} — {item['price']}")
                        if de:
                            send_discord_alert(webhook_url, item)
                        if ej:
                            append_to_file(item)
                        new_count += 1

                log(f"{new_count} new item(s) — {len(listings)} total parsed")

            except FileNotFoundError as e:
                log(f"FATAL: {e}")
                break
            except Exception as e:
                log(f"ERROR: {e}")

            if stop_event and stop_event.is_set():
                break
                
            delay = random.uniform(10, 30) if mm else random.uniform(300, 600)
            elapsed = time.time() - cycle_start
            sleep_time = max(0, delay - elapsed)
            log(f"Sleeping {sleep_time:.1f}s...")
            
            for _ in range(int(sleep_time)):
                if stop_event and stop_event.is_set():
                    break
                time.sleep(1)
    finally:
        conn.close()
        log("Database connection closed gracefully.")


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C pressed — shutting down.")