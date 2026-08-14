import os
import re
import time
import random
import json
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlencode

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

    driver = _create_driver()
    try:
        driver.get(url)
        time.sleep(5)
        return driver.page_source
    finally:
        driver.quit()


def _create_driver():
    """Create a Selenium Chrome driver with stealth settings."""
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
    return driver


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


# ─── Search URL Builder & Filter Options ────────────────────────────────────

MARKET_URL = "https://www.vinted.hu"
CURRENCY = "HUF"

ORDER_OPTIONS = [
    ("relevance", "Relevancia"),
    ("newest_first", "Legújabb"),
    ("price_low_to_high", "Ár növekvő"),
    ("price_high_to_low", "Ár csökkenő"),
]

STATUS_OPTIONS = [
    ("1", "Új címke nélkül"),
    ("2", "Újszerű"),
    ("3", "Jó"),
    ("4", "Közepes"),
    ("6", "Új címkével"),
]


def build_search_url(params=None):
    """Build a Vinted catalog search URL from a filter params dict.

    List params are mapped to Vinted's `{code}_ids[]` query parameters
    (e.g. `size_ids[]`, `color_ids[]`, `brand_collection_ids[]`), except
    `catalog` which becomes `catalog[]`.
    """
    params = params or {}
    currency = str(params.get("currency") or CURRENCY)
    qs = [("currency", currency)]

    for key in ("search_text", "order", "price_from", "price_to"):
        val = params.get(key)
        if val not in (None, ""):
            qs.append((key, str(val)))

    for key, vals in params.items():
        if not isinstance(vals, (list, tuple)):
            continue
        if key == "catalog":
            name = "catalog[]"
        else:
            name = f"{key}_ids[]"
        for v in vals:
            if str(v).strip():
                qs.append((name, str(v)))

    if params.get("discount"):
        qs.append(("discount_ids[]", "4"))
    if params.get("favourite"):
        qs.append(("favourite", "true"))
    if params.get("handicraft"):
        qs.append(("is_handicraft", "true"))
    if params.get("give_away"):
        qs.append(("is_for_give_away", "true"))

    url = f"{MARKET_URL}/catalog"
    if qs:
        url += "?" + urlencode(qs, doseq=True)
    return url


def fetch_category_tree():
    """Fetch the full category tree (Női, Férfi, ... plus subcategories) from the Vinted main page."""
    driver = _create_driver()
    try:
        driver.get(f"{MARKET_URL}/")
        time.sleep(6)
        tree = _extract_flight_json(driver.page_source, "catalogTree")
        if not tree:
            raise RuntimeError("catalogTree not found on main page")
        return tree
    finally:
        driver.quit()


def _extract_flight_json(html, key):
    """Extract a JSON value for `key` from a Next.js RSC flight payload in the page HTML."""
    pattern = re.compile(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)')
    for raw in pattern.findall(html):
        try:
            payload = json.loads('"' + raw + '"')
        except (ValueError, json.JSONDecodeError):
            continue
        marker = '"' + key + '"'
        if marker not in payload:
            continue
        idx = payload.index(marker) + len(marker)
        idx = payload.index(":", idx) + 1
        start = idx
        while start < len(payload) and payload[start] in " \t\n":
            start += 1
        if payload[start] not in "[{":
            continue
        val = _match_bracket(payload, start)
        if val is None:
            continue
        try:
            return json.loads(val)
        except (ValueError, json.JSONDecodeError):
            continue
    return None


def _match_bracket(s, i):
    """Return the balanced bracket expression starting at s[i] (must be '[' or '{')."""
    depth = 0
    in_str = False
    j = i
    n = len(s)
    while j < n:
        c = s[j]
        if in_str:
            if c == "\\":
                j += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return s[i:j + 1]
        j += 1
    return None


def _extract_catalog_facets(html):
    """Extract filter facets (code -> [(id, title)]) from the RSC flight payload
    of a Vinted catalog page. Lazy facets (empty 'options') are skipped here;
    callers may fall back to the facet API for those codes."""
    return {code: meta["options"]
            for code, meta in _extract_catalog_filters(html).items()
            if meta["options"]}


def _extract_catalog_filters(html):
    """Extract the full category filter set (code -> {"title", "options"}) from
    the RSC flight payload of a Vinted catalog page. Includes lazy facets whose
    options are empty (loaded when their chip is expanded)."""
    pattern = re.compile(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)')
    result = {}
    for raw in pattern.findall(html):
        try:
            payload = json.loads('"' + raw + '"')
        except (ValueError, json.JSONDecodeError):
            continue
        if '"is_async_facet"' not in payload:
            continue
        for m in re.finditer(r'"id":\d+,"title":"((?:[^"\\]|\\.)*)","code":"([a-z_]+)"',
                             payload):
            title, code = m.group(1), m.group(2)
            after = payload[m.end():]
            opt = after.find('"options"')
            iaf = after.find('"is_async_facet"')
            if opt < 0 or iaf < 0 or opt > iaf:
                continue
            arr = after.find("[", opt + 9)
            if arr < 0:
                continue
            seg = _match_bracket(after, arr)
            pairs = []
            if seg:
                try:
                    opts = json.loads(seg)
                except (ValueError, json.JSONDecodeError):
                    opts = None
                for o in opts or []:
                    if isinstance(o, dict) and o.get("id") is not None:
                        pairs.append((o["id"], o.get("title") or ""))
            if code not in result:
                result[code] = {"title": title, "options": pairs}
    return result


_API_FILTERS = f"{MARKET_URL}/api/v2/catalog/filters"
_API_FACETS = f"{MARKET_URL}/api/v2/catalog/filters/facets"
_COMMON_FACETS = ["brand", "status", "color", "size", "material", "patterns",
                  "brand_collection", "device_size", "car_brand"]


def _api_json(driver, url, attempts=3):
    """Navigate to a Vinted JSON API URL and return the parsed response.
    Retries on transient bot/rate-limit responses."""
    last = None
    for _ in range(max(1, attempts)):
        try:
            driver.get(url)
            time.sleep(2)
            body = driver.find_element("tag name", "body").text.strip()
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("api response is not a JSON object")
            return data
        except Exception as e:
            last = e
            time.sleep(3)
    raise last if last else RuntimeError("api failed")


def _pair_options(data, node="options"):
    """Normalise an API 'options' array to [(id, title)] pairs."""
    pairs = []
    for o in (data.get(node) or []):
        if not isinstance(o, dict):
            continue
        oid = o.get("id")
        title = o.get("title") or o.get("name")
        if oid is not None and title:
            pairs.append((oid, title))
    return pairs


def _catalog_id_from_url(cat_url):
    """Extract the catalog id from a Vinted catalog URL (path or query form)."""
    if not cat_url:
        return None
    m = re.search(r"/catalog/(\d+)", cat_url)
    if m:
        return int(m.group(1))
    m = re.search(r"catalog\[\]=(\d+)", cat_url)
    return int(m.group(1)) if m else None


def fetch_category_filters(cat_url=None, catalog_id=None):
    """Load a category's full live filter set.

    Group metadata comes from /api/v2/catalog/filters; the options of every
    filter group (static or lazy) are loaded through /api/v2/catalog/filters/
    facets?filter_code=<code>, which serves the truly non-static facets too.
    Returns {code: {"title": str, "options": [(id, title)]}}.
    """
    if catalog_id is None:
        catalog_id = _catalog_id_from_url(cat_url)
    if catalog_id is None:
        return {}
    catalog_id = str(catalog_id)

    driver = _create_driver()
    try:
        filter_meta = {}
        filters = {}

        # ── 1) csoport-metadata (code, title, is_lazy) ─────────────────────
        # A Vinted néha üres listát ad; ilyenkor újrapróbáljuk a csoportlekérést.
        groups = []
        for _ in range(3):
            data = _api_json(driver, _API_FILTERS + "?page=1&per_page=96&currency="
                             + CURRENCY + "&catalog_ids=" + catalog_id)
            groups = [g for g in (data.get("filters") or []) if isinstance(g, dict)]
            if groups:
                break
            time.sleep(3)
        if not groups:
            return {}

        lazy = []
        for g in groups:
            code = g.get("code")
            if not code or code == "price":
                continue
            title = g.get("title") or code
            filter_meta[code] = title
            opts = _pair_options(g)
            if opts:
                filters[code] = {"title": title, "options": opts}
            elif g.get("is_lazy"):
                lazy.append(code)

        # ── 2) nem-statikus (lazy) facetek opciói ──────────────────────────
        for code in lazy:
            url = (_API_FACETS + "?catalog_ids=" + catalog_id
                   + "&filter_code=" + code)
            pairs = []
            for _ in range(2):
                try:
                    pairs = _pair_options(_api_json(driver, url))
                except Exception:
                    pairs = []
                if pairs:
                    break
                time.sleep(2)
            if pairs:
                filters[code] = {"title": filter_meta.get(code, code),
                                 "options": pairs}

        # ── 3) függő facetek (pl. Modell) márka-próbákkal ───────────────────
        _expand_dependent_facets(driver, catalog_id, filter_meta, filters)
    finally:
        driver.quit()
    return filters


def _expand_dependent_facets(driver, catalog_id, filter_meta, filters):
    """Some facets (e.g. brand_collection/Modell) only appear once another
    filter (e.g. a brand) is selected. Probe a few popular brands; whenever a
    brand selection surfaces a NEW lazy group, load its options and merge it.
    """
    probes = [str(bid) for bid, _ in (filters.get("brand", {}).get("options") or [])][:3]
    for bid in probes:
        try:
            url = (_API_FILTERS + "?page=1&per_page=96&currency=" + CURRENCY
                   + "&catalog_ids=" + catalog_id + "&brand_ids=" + bid)
            data = _api_json(driver, url)
        except Exception:
            continue
        for g in (data.get("filters") or []):
            if not isinstance(g, dict):
                continue
            code = g.get("code")
            if not code or code in filters or code == "price":
                continue
            opts = _pair_options(g)
            if not opts:
                try:
                    fdata = _api_json(driver, _API_FACETS + "?catalog_ids="
                                      + catalog_id + "&filter_code=" + code
                                      + "&brand_ids=" + bid)
                    opts = _pair_options(fdata)
                except Exception:
                    continue
            if opts:
                filters[code] = {"title": filter_meta.get(code, code),
                                 "options": opts}


def fetch_facet_options(search_text="", catalog_ids=None, facet_codes=None):
    """Best-effort fetch of facet options for given codes via
    /api/v2/catalog/filters/facets. Returns {code: [(id, title)]}."""
    catalog_ids = [str(c) for c in (catalog_ids or []) if c]
    facet_codes = list(facet_codes or ())
    qs = {"page": "1", "per_page": "96", "currency": CURRENCY}
    if search_text:
        qs["search_text"] = search_text
    if catalog_ids:
        qs["catalog_ids"] = catalog_ids
    base = _API_FACETS + "?" + urlencode(qs, doseq=True)

    codes = facet_codes or _COMMON_FACETS
    driver = _create_driver()
    result = {}
    try:
        driver.get(f"{MARKET_URL}/catalog")
        time.sleep(2)
        for code in codes:
            try:
                pairs = _pair_options(_api_json(driver, base + "&filter_code=" + code))
            except Exception:
                pairs = []
            if pairs:
                result[code] = pairs
    finally:
        driver.quit()
    return result


def fetch_brands(keyword):
    """Search brands on Vinted; returns a list of (id, title) tuples."""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    url = f"{MARKET_URL}/api/v2/brands?{urlencode({'search_text': keyword, 'per_page': '20'})}"

    driver = _create_driver()
    try:
        driver.get(f"{MARKET_URL}/catalog")
        time.sleep(2)
        driver.get(url)
        time.sleep(2)
        body = driver.find_element("tag name", "body").text
        data = json.loads(body)
    finally:
        driver.quit()

    if isinstance(data, dict):
        data = data.get("brands", data.get("items", []))
    pairs = []
    seen = set()
    for b in data or []:
        if not isinstance(b, dict):
            continue
        bid = b.get("id")
        title = b.get("title")
        if bid is not None and title and bid not in seen:
            pairs.append((bid, title))
            seen.add(bid)
    return pairs


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