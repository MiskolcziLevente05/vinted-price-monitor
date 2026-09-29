"""Vinted price monitor — core scraping, filtering, persistence and alerting.

Architecture notes
------------------
* One pooled headless Chrome instead of one process per request
  (``BrowserPool``). Selenium is needed only for the item pages, which embed
  their data in a Next.js flight payload; every Vinted JSON API is called over
  plain ``requests``.
* The ``svc-filters`` API rejects cookieless calls with 403, so a single
  browser visit seeds a cached cookie jar which the HTTP session reuses.
* Item records are read from the flight payload rather than the DOM: the
  payload carries a clean title, a numeric price, a full-size image URL and
  the size/status line, none of which survive HTML scraping reliably. The DOM
  parser is kept as a fallback.
"""

import contextlib
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

# ─── Market & API constants ─────────────────────────────────────────────────

MARKET_URL = "https://www.vinted.hu"
_API_HOST = "https://api.vinted.hu/svc-filters"
_API_FILTERS = f"{_API_HOST}/filters"
_API_FACETS = f"{_API_FILTERS}/facets"
CURRENCY = "HUF"

# The svc-filters API localises titles from the `Locale` header, not
# Accept-Language. Without it the API answers with French labels.
_LOCALE = "hu-HU"

_COMMON_FACETS = ["brand", "status", "color", "size", "material", "patterns"]

_FALLBACK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/154.0.0.0 Safari/537.36")

_API_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "hu-HU,hu;q=0.9,en;q=0.8",
    "locale": _LOCALE,
    "platform": "web",
    "referer": f"{MARKET_URL}/",
    # Vinted sits behind DataDome: a `python-requests/…` user agent is
    # rejected with 403 no matter how valid the cookies are, so the session
    # always borrows the real browser's user agent (see `_seed_session`).
    "user-agent": _FALLBACK_UA,
    "x-next-app": "marketplace-web",
    "origin": MARKET_URL,
    "x-requested-with": "XMLHttpRequest",
}

# Kept for callers that want the old global switch behaviour.
DISCORD_ENABLED = True
MIN_PRICE = 0

ORDER_OPTIONS = [
    ("relevance", "Relevancia"),
    ("newest_first", "Legújabb"),
    ("price_low_to_high", "Ár növekvő"),
    ("price_high_to_low", "Ár csökkenő"),
]

# Fallback only: the live `status` facet is preferred whenever it loads.
STATUS_OPTIONS = [
    ("1", "Új címke nélkül"),
    ("2", "Újszerű"),
    ("3", "Jó"),
    ("4", "Közepes"),
    ("6", "Új címkével"),
]

_STATUS_WORDS = {"új", "újszerű", "jo", "jó", "közepes", "kiváló",
                 "new", "new with tags", "good", "very good", "satisfactory"}
_SIZE_RE = re.compile(r"^(?:XXS|XS|S|M|L|XL|XXL|XXXL|\d{1,3}(?:[.,]\d)?"
                      r"|\d+XL|\d+XS|\d+S|\d+M|\d+L)$", re.IGNORECASE)


# ─── Logging ────────────────────────────────────────────────────────────────

_log_sink = None
_log_lock = threading.Lock()


def set_log_sink(fn):
    """Route internal messages to `fn` (the GUI log panel) instead of stdout."""
    global _log_sink
    with _log_lock:
        _log_sink = fn


def log(msg):
    """Emit a message through the active sink, falling back to stdout.

    Never raises: a narrow console encoding (cp1252 on Hungarian Windows)
    must not turn a warning into a crash, and a log call must never mask the
    error it was reporting.
    """
    with _log_lock:
        sink = _log_sink
    if sink is not None:
        try:
            sink(msg)
            return
        except Exception:
            pass
    try:
        print(msg)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        safe = msg.encode(encoding, "replace").decode(encoding, "replace")
        try:
            print(safe)
        except Exception:
            pass


# ─── Chrome driver lifecycle ────────────────────────────────────────────────

def _cached_chromedriver():
    """Locally cached chromedriver from the Selenium Manager cache, if any.

    Using it lets the app start without a network round-trip.
    """
    root = os.path.join(os.path.expanduser("~"), ".cache", "selenium",
                        "chromedriver")
    if not os.path.isdir(root):
        return None
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.lower() in ("chromedriver.exe", "chromedriver"):
                exe = os.path.join(dirpath, name)
                try:
                    found.append((os.path.getmtime(exe), exe))
                except OSError:
                    pass
    return max(found)[1] if found else None


def _driver_error(original):
    """Translate the opaque Selenium Manager error into something actionable."""
    msg = str(original)
    if "Unable to obtain driver" in msg or "driver_location" in msg:
        return RuntimeError(
            "A Chrome driver nem elérhető. A Selenium Manager a hálózatról "
            "tölti le a chromedriver-t, ezért egyszeri internetkapcsolat "
            "szükséges az első indításhoz. Kapcsolódj vissza a netre és "
            "indítsd újra — utána a gyorsítótárból dolgozik."
        )
    return original


def _create_driver():
    """Create a headless Chrome driver with stealth settings.

    Prefers a locally cached chromedriver so that an intermittent network
    outage cannot block the start; falls back to the Selenium Manager.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium_stealth import stealth

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1400,1000")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("lang=hu-HU")

    service = None
    cached = _cached_chromedriver()
    if cached:
        try:
            service = Service(executable_path=cached)
        except Exception:
            service = None

    try:
        driver = (webdriver.Chrome(service=service, options=options) if service
                  else webdriver.Chrome(options=options))
    except Exception as e:
        if service is None:
            raise _driver_error(e)
        # The cached driver may be stale -> retry through the Manager.
        try:
            driver = webdriver.Chrome(options=options)
        except Exception:
            raise _driver_error(e)

    try:
        stealth(
            driver,
            languages=["hu-HU", "hu", "en-US", "en"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True,
        )
    except Exception:
        # Stealth is a nice-to-have; a browser without it still works.
        pass
    return driver


def _safe_quit(driver):
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass


class BrowserPool:
    """Keeps a small number of headless Chrome instances alive between calls.

    Creating a browser costs ~5 s and a few hundred MB, so recycling one is
    what makes facet and page loads feel instant. `acquire()` is a context
    manager and is thread safe.
    """

    def __init__(self, max_size=2):
        self._cond = threading.Condition()
        self._idle = []
        self._leased = 0
        self._max = max_size
        self._closed = False

    @contextlib.contextmanager
    def acquire(self, timeout=300):
        deadline = time.time() + timeout
        with self._cond:
            while True:
                if self._closed:
                    raise RuntimeError("A monitor leáll, nincs új böngésző.")
                if self._idle:
                    driver = self._idle.pop()
                    self._leased += 1
                    break
                if self._leased < self._max:
                    self._leased += 1
                    driver = None
                    break
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(
                        "Nincs szabad böngésző (időtúllépés).")
                self._cond.wait(min(remaining, 2.0))

        if driver is None:
            try:
                driver = _create_driver()
            except Exception:
                with self._cond:
                    self._leased -= 1
                    self._cond.notify()
                raise

        try:
            yield driver
        finally:
            with self._cond:
                self._leased -= 1
                if self._closed:
                    _safe_quit(driver)
                else:
                    self._idle.append(driver)
                self._cond.notify()

    def shutdown(self):
        """Quit every idle browser and refuse further leases."""
        with self._cond:
            self._closed = True
            for driver in self._idle:
                _safe_quit(driver)
            self._idle.clear()
            self._cond.notify_all()

    def reset(self):
        """Allow pooling again after `shutdown()` (used when monitoring
        restarts within the same process)."""
        with self._cond:
            self._closed = False

    @property
    def idle_count(self):
        with self._cond:
            return len(self._idle)


_POOL = BrowserPool(max_size=2)


def shutdown_browsers():
    """Close every pooled browser. Call before the process exits."""
    _POOL.shutdown()


# ─── HTTP session with Vinted cookies ───────────────────────────────────────

_session = None            # (timestamp, requests.Session)
_session_lock = threading.Lock()
_SESSION_TTL = 1800.0      # re-seed the cookie jar every 30 minutes


def _seed_session():
    """Build a requests session carrying a fresh Vinted cookie jar.

    The jar and the user agent must come from the same browser: Vinted's
    DataDome layer ties the `datadome` cookie to the UA that earned it.
    """
    sess = requests.Session()
    user_agent = None
    log("[info] Vinted session létrehozása — egy böngészős látogatás kell a "
        "sütikhez (ez csak egyszer történik, 30 percig cache-elünk).")
    with _POOL.acquire() as driver:
        driver.get(f"{MARKET_URL}/catalog")
        time.sleep(3)
        try:
            user_agent = driver.execute_script("return navigator.userAgent")
        except Exception:
            pass
        for cookie in driver.get_cookies():
            name = cookie.get("name")
            if name:
                # Set both domain-scoped and host-agnostic so the cookies reach
                # api.vinted.hu whatever scope Vinted chose for them.
                sess.cookies.set(name, cookie.get("value"),
                                 domain=cookie.get("domain") or "", path="/")
        if not sess.cookies:
            raise RuntimeError("Nem sikerült Vinted sütiket szerezni.")
    sess.headers.update(_API_HEADERS)
    # applied last: the live browser UA wins over the static fallback
    sess.headers["user-agent"] = user_agent or _FALLBACK_UA
    return sess


def api_session(refresh=False):
    """Return the shared cookie-carrying session, re-seeding it when stale."""
    global _session
    with _session_lock:
        if (not refresh and _session is not None
                and time.time() - _session[0] < _SESSION_TTL):
            return _session[1]
        if _session is not None:
            _session = None
        sess = _seed_session()
        _session = (time.time(), sess)
        return sess


def api_get(path_or_url, params=None, attempts=3, session=None):
    """GET a Vinted JSON endpoint and return the decoded body.

    Retries on transport errors and on the 403/INVALID responses the API
    returns when the cookie jar has gone stale (re-seeding it once).
    """
    url = path_or_url if path_or_url.startswith("http") else _API_HOST + path_or_url
    if params:
        url = f"{url}?{urlencode(params, doseq=True)}"

    last = None
    refreshed = False
    for attempt in range(max(1, attempts)):
        sess = session or api_session(refresh=refreshed)
        try:
            resp = sess.get(url, headers=_API_HEADERS, timeout=25)
        except requests.RequestException as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code in (401, 403):
            last = RuntimeError(f"API elutasította a kérést ({resp.status_code})")
            if not refreshed:
                refreshed = True          # next loop re-seeds the cookies
                log("[warn] A Vinted elutasította a kérést — lejárt a "
                    "session, új sütiket szerzek.")
                continue
        elif not resp.ok:
            last = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
        else:
            try:
                data = resp.json()
            except ValueError as e:
                last = e
            else:
                if not isinstance(data, dict):
                    last = RuntimeError("az API válasza nem JSON objektum")
                elif data.get("code") and not isinstance(
                        data.get("filters"), list) and not isinstance(
                        data.get("options"), list):
                    last = RuntimeError(f"API hiba: {data.get('code')} "
                                        f"{data.get('message', '')}".strip())
                else:
                    return data
        time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError("API kérés sikertelen")


def get_page_source(url, wait=5):
    """Load a Vinted page in a pooled browser and return its HTML."""
    with _POOL.acquire() as driver:
        driver.get(url)
        time.sleep(wait)
        return driver.page_source


# ─── Next.js flight payload helpers ─────────────────────────────────────────

_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)')


def _flight_payload(html):
    """Concatenate the decoded RSC flight chunks of a page into one string."""
    parts = []
    for raw in _PUSH_RE.findall(html):
        try:
            parts.append(json.loads('"' + raw + '"'))
        except (ValueError, json.JSONDecodeError):
            continue
    return "".join(parts)


def _match_bracket(s, i):
    """Return the balanced bracket expression starting at s[i]."""
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


def _extract_flight_json(html, key):
    """Extract a JSON value for `key` from a page's flight payload."""
    payload = _flight_payload(html)
    marker = '"' + key + '"'
    idx = payload.find(marker)
    while idx != -1:
        pos = idx + len(marker)
        colon = payload.find(":", pos)
        if colon != -1:
            start = colon + 1
            while start < len(payload) and payload[start] in " \t\n":
                start += 1
            if start < len(payload) and payload[start] in "[{":
                seg = _match_bracket(payload, start)
                if seg:
                    try:
                        return json.loads(seg)
                    except (ValueError, json.JSONDecodeError):
                        pass
        idx = payload.find(marker, idx + 1)
    return None


def _extract_catalog_filters(html):
    """Extract the lazy facet set (code -> {title, options}) from a catalog
    page. Retained as a fallback for when the svc-filters API is unreachable."""
    payload = _flight_payload(html)
    result = {}
    if '"is_async_facet"' not in payload:
        return result
    for m in re.finditer(
            r'"id":\d+,"title":"((?:[^"\\]|\\.)*)","code":"([a-z_]+)"', payload):
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


# ─── Item normalisation ─────────────────────────────────────────────────────

def _split_size_status(line):
    """Split an itemBox second line into (size, status).

    Real values look like `XS · Kiváló`, `EU 44 · Jó`, `L / 40 / 12 · Kiváló`
    or just `Kiváló`. The reliable signal is the trailing status word, so it is
    split off first and whatever remains is the size.
    """
    parts = [p.strip() for p in (line or "").split("·") if p.strip()]
    if not parts:
        return None, None
    if len(parts) == 1:
        only = parts[0]
        if only.lower() in _STATUS_WORDS:
            return None, only
        return (only, None) if _SIZE_RE.match(only) else (None, only)
    if parts[-1].lower() in _STATUS_WORDS:
        return " / ".join(parts[:-1]), parts[-1]
    return None, " · ".join(parts)


def _amount(value):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _normalize_item(rec):
    """Turn one flight-payload item record into the app's listing dict."""
    p = rec.get("productItem") if isinstance(rec, dict) else None
    p = p if isinstance(p, dict) else (rec or {})

    item_id = p.get("id") or (rec or {}).get("id")
    if not item_id:
        return None

    title = (p.get("title") or "").strip()
    href = p.get("url") or ""
    url = (MARKET_URL + href) if href.startswith("/") else href

    price_obj = p.get("priceWithDiscount") or p.get("price") or {}
    amount = _amount(price_obj.get("amount")) if isinstance(price_obj, dict) else None
    if amount is None:
        amount = _amount(p.get("price")) or 0.0
    currency = (price_obj.get("currencyCode") if isinstance(price_obj, dict)
                else None) or CURRENCY
    price_text = f"{int(round(amount))} {currency}"

    image = p.get("thumbnailUrl")
    photos = p.get("photos") or []
    if isinstance(photos, list):
        for photo in photos:
            if isinstance(photo, dict) and photo.get("url"):
                image = photo["url"]
                break

    size, status = _split_size_status((p.get("itemBox") or {}).get("secondLine"))

    return {
        "id": str(item_id),
        "title": title,
        "price_num": round(float(amount), 2),
        "price": price_text,
        "url": url,
        "image": image,
        "size": size,
        "status": status,
        "favourites": p.get("favouriteCount") or 0,
    }


def _extract_items(html):
    """Pull the structured item array out of a catalog page's flight payload."""
    payload = _flight_payload(html)
    m = re.search(r'\{"items":\[', payload)
    if not m:
        return []
    seg = _match_bracket(payload, m.end() - 1)
    if not seg:
        return []
    try:
        raw = json.loads(seg)
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    out = []
    for rec in raw:
        item = _normalize_item(rec)
        if item:
            out.append(item)
    return out


def parse_listings(html):
    """DOM fallback for item extraction, used when the payload has no items."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.select('[data-testid^="grid-item"]'):
        try:
            anchor = card.find("a", href=re.compile(r"/items/\d+"))
            if not anchor:
                continue
            href = anchor.get("href", "")
            match = re.search(r"/items/(\d+)", href)
            if not match:
                continue

            title = (anchor.get("title") or "").split(",")[0].strip()
            if not title:
                el = card.select_one('[data-testid$="--description-title"]')
                title = el.get_text(strip=True) if el else ""
            if not title:
                img = card.find("img", alt=True)
                if img and img.get("alt"):
                    title = img["alt"].split(",")[0].strip()

            price_el = card.select_one('[data-testid$="--price-text"]')
            price_text = price_el.get_text(strip=True) if price_el else ""
            amount = parse_price_numeric(price_text)

            img = card.find("img")
            items.append({
                "id": match.group(1),
                "title": title,
                "price_num": round(amount, 2),
                "price": price_text or f"{int(amount)} {CURRENCY}",
                "url": (MARKET_URL + href) if href.startswith("/") else href,
                "image": (img.get("src") or img.get("data-src")) if img else None,
                "size": None,
                "status": None,
                "favourites": 0,
            })
        except Exception as e:
            log(f"[PARSE ERROR] Kártya kihagyva: {e}")
            continue
    return items


def extract_items(html):
    """Structured records if present, DOM records as a fallback."""
    items = _extract_items(html)
    if items:
        return items, "payload"
    dom = parse_listings(html)
    return dom, "dom"


def parse_price_numeric(price_str):
    """Extract a numeric price from text such as '1 400 Ft'."""
    if price_str is None:
        return 0.0
    clean = str(price_str).replace("\xa0", "").replace(" ", "")
    clean = clean.replace("Ft", "").replace(CURRENCY, "").strip()
    if not clean:
        return 0.0
    digits = re.sub(r"[^0-9.,]", "", clean)
    if not digits:
        return 0.0
    if "," in digits and "." in digits:
        digits = digits.replace(",", "")
    else:
        digits = digits.replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return 0.0


def fetch_page_items(url, wait=5):
    """Load one catalog page and return (items, source)."""
    html = get_page_source(url, wait=wait)
    return extract_items(html)


def build_page_url(base_url, page):
    """Add a `page` parameter to a catalog URL (page 1 is the bare URL)."""
    if page <= 1:
        return base_url
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}page={page}"


# ─── Database layer ─────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "vinted_monitor.db")
SETTINGS_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "settings.db")

_COLUMNS = ("id, title, price_num, price_text, url, image, size, status, "
            "favourites, watch_name, first_price")


def init_db():
    """Create the SQLite database and the listings table if they don't exist."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            price_num   REAL NOT NULL DEFAULT 0,
            price_text  TEXT NOT NULL DEFAULT '',
            url         TEXT NOT NULL DEFAULT '',
            image       TEXT,
            size        TEXT,
            status      TEXT,
            favourites  INTEGER NOT NULL DEFAULT 0,
            watch_name  TEXT,
            first_price REAL,
            notified    INTEGER NOT NULL DEFAULT 0,
            first_seen  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            hits        INTEGER NOT NULL DEFAULT 1
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_listings_seen ON listings(last_seen)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_listings_watch ON listings(watch_name)")
    conn.commit()
    return conn


def check_and_insert(conn, item, watch_name=""):
    """Store an item. Returns (is_new, previous_price)."""
    values = (
        str(item.get("id") or ""),
        item.get("title") or "",
        float(item.get("price_num") or 0.0),
        item.get("price") or "",
        item.get("url") or "",
        item.get("image"),
        item.get("size"),
        item.get("status"),
        int(item.get("favourites") or 0),
        watch_name or None,
        float(item.get("price_num") or 0.0),
    )
    if not values[0] or not values[1]:
        return False, None

    cur = conn.execute(
        f"INSERT OR IGNORE INTO listings ({_COLUMNS}) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
    is_new = cur.rowcount == 1

    previous = None
    if not is_new:
        row = conn.execute("SELECT price_num FROM listings WHERE id = ?",
                           (values[0],)).fetchone()
        previous = row[0] if row else None
        conn.execute(
            "UPDATE listings SET last_seen = CURRENT_TIMESTAMP, "
            "hits = hits + 1, price_num = ?, price_text = ?, image = COALESCE(?, image) "
            "WHERE id = ?", (values[2], values[3], values[5], values[0]))
    conn.commit()
    return is_new, previous


def fetch_recent(conn, limit=300, watch_name=None):
    """Most recently seen listings, newest first."""
    if watch_name:
        cur = conn.execute(
            "SELECT id, title, price_text, url, image, size, status, "
            "watch_name, first_seen, last_seen, hits "
            "FROM listings WHERE watch_name = ? "
            "ORDER BY last_seen DESC, first_seen DESC LIMIT ?",
            (watch_name, limit))
    else:
        cur = conn.execute(
            "SELECT id, title, price_text, url, image, size, status, "
            "watch_name, first_seen, last_seen, hits "
            "FROM listings ORDER BY last_seen DESC, first_seen DESC LIMIT ?",
            (limit,))
    cols = ("id", "title", "price", "url", "image", "size", "status",
            "watch", "first_seen", "last_seen", "hits")
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def count_listings(conn):
    return conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]


def delete_db_file():
    """Remove the listings database. Returns True when a file was deleted."""
    for suffix in ("", "-wal", "-shm"):
        path = DB_PATH + suffix
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
        except OSError:
            return False
    return True


# ─── Settings store ─────────────────────────────────────────────────────────

def _settings_conn():
    conn = sqlite3.connect(SETTINGS_DB_PATH, timeout=15)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    return conn


def load_setting(key, default=""):
    """Read a persisted setting; returns `default` when missing/unreadable."""
    try:
        conn = _settings_conn()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = ?",
                               (key,)).fetchone()
        finally:
            conn.close()
        return row[0] if row and row[0] is not None else default
    except sqlite3.Error as e:
        log(f"[SETTINGS] betöltési hiba ({key}): {e}")
        return default


def save_setting(key, value):
    """Persist a setting. Returns True on success."""
    try:
        conn = _settings_conn()
        try:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, "" if value is None else str(value)))
            conn.commit()
        finally:
            conn.close()
        return True
    except sqlite3.Error as e:
        log(f"[SETTINGS] mentési hiba ({key}): {e}")
        return False


def delete_setting(key):
    """Remove a persisted setting."""
    try:
        conn = _settings_conn()
        try:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            conn.commit()
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        return False


def load_bool(key, default=False):
    value = load_setting(key, None)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "on", "igen", "yes")


def save_bool(key, value):
    return save_setting(key, "1" if value else "0")


# ─── Discord notifications ──────────────────────────────────────────────────

class DiscordSender:
    """Rate-limited Discord webhook sender.

    Discord allows roughly 5 requests per 2 s per webhook; a cold category can
    surface a hundred items at once, so sends are spaced out and a per-cycle
    cap keeps the channel readable.
    """

    def __init__(self, webhook_url, min_interval=0.45):
        self.url = (webhook_url or "").strip()
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()
        self.sent = 0
        self.failed = 0

    @property
    def configured(self):
        return bool(self.url)

    def _throttle(self):
        wait = self._last + self.min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    @staticmethod
    def _embed(item):
        fields = [
            {"name": "Ár", "value": item.get("price") or "—", "inline": True},
        ]
        if item.get("size"):
            fields.append({"name": "Méret", "value": item["size"], "inline": True})
        if item.get("status"):
            fields.append({"name": "Állapot", "value": item["status"],
                           "inline": True})
        embed = {
            "title": (item.get("title") or "Vinted találat")[:256],
            "url": item.get("url"),
            "color": 0x09B1BA,
            "fields": fields,
            "footer": {"text": f"Tétel #{item.get('id')}"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if item.get("image"):
            embed["image"] = {"url": item["image"]}
        if item.get("favourites"):
            embed["footer"]["text"] += f" · {item['favourites']} ♥"
        return embed

    def send(self, item):
        """Send one alert. Returns True on success."""
        if not self.configured:
            return False
        payload = {"username": "Vinted Monitor", "embeds": [self._embed(item)]}
        with self._lock:
            self._throttle()
            try:
                resp = requests.post(self.url, json=payload, timeout=15)
            except requests.RequestException as e:
                self.failed += 1
                log(f"[err] Discord küldés sikertelen ({item.get('id')}): {e}")
                return False

        if resp.status_code == 429:
            retry_after = 1.0
            try:
                retry_after = float(resp.json().get("retry_after", 1.0))
            except Exception:
                pass
            log(f"[warn] Discord rate limit — {retry_after:.1f}s várakozás.")
            time.sleep(min(retry_after + 0.3, 30))
            return self.send(item)

        if not resp.ok:
            self.failed += 1
            log(f"[err] Discord HTTP {resp.status_code}: {resp.text[:160]}")
            return False

        self.sent += 1
        return True

    def send_note(self, text):
        """Send a short plain note (used for cycle summaries)."""
        if not self.configured:
            return False
        with self._lock:
            self._throttle()
            try:
                resp = requests.post(
                    self.url,
                    json={"username": "Vinted Monitor", "content": text[:1900]},
                    timeout=15)
            except requests.RequestException as e:
                log(f"[err] Discord jegyzet sikertelen: {e}")
                return False
        return resp.ok


def send_discord_alert(webhook_url, item):
    """Send a single alert (kept for backwards compatibility)."""
    return DiscordSender(webhook_url).send(item)


def check_webhook_format(url):
    """Validate a webhook URL's shape without touching the network.

    Returns (ok, message). Used at startup so a typo is reported before the
    first cycle instead of silently swallowing every alert.
    """
    url = (url or "").strip()
    if not url:
        return False, "A webhook URL üres."
    if "discord.com/api/webhooks/" not in url and \
            "discordapp.com/api/webhooks/" not in url:
        return False, ("Ez nem Discord webhook URL "
                       "(discord.com/api/webhooks/...).")
    if not url.rsplit("/", 1)[-1]:
        return False, "A webhook URL-ből hiányzik az azonosító."
    return True, "A webhook URL alakja rendben."


def test_webhook(webhook_url):
    """Validate a webhook URL end to end. Returns (ok, message)."""
    ok, msg = check_webhook_format(webhook_url)
    if not ok:
        return False, msg
    url = webhook_url.strip()
    try:
        resp = requests.post(
            url,
            json={"username": "Vinted Monitor",
                  "content": "✅ A Vinted Monitor webhook működik."},
            timeout=15)
    except requests.RequestException as e:
        return False, f"Nem sikerült kapcsolatot létesíteni: {e}"
    if resp.status_code == 401:
        return False, "A webhook érvénytelen vagy törölt (401)."
    if resp.status_code == 404:
        return False, "Nincs ilyen webhook (404)."
    if not resp.ok:
        return False, f"Discord hiba: HTTP {resp.status_code}"
    return True, "A webhook működik, tesztüzenet elküldve."


# ─── Search URL builder ─────────────────────────────────────────────────────

def build_search_url(params=None):
    """Build a Vinted catalog search URL from a filter params dict.

    List params become Vinted's `{code}_ids[]` query parameters, except
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
        if key in ("currency", "search_text", "order", "price_from",
                   "price_to"):
            continue
        name = "catalog[]" if key == "catalog" else f"{key}_ids[]"
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


# ─── Facets (svc-filters API) ───────────────────────────────────────────────

def _filters_query(catalog_ids=None, **extra):
    """Build the svc-filters query string.

    Vinted expects the selected attributes as `attribute_ids[<code>]` params
    plus every known facet code so the context matches the web app. Keys given
    via `extra` override the defaults — Vinted reads the first value of a
    repeated key, so duplicates must be removed.
    """
    params = [("currency", CURRENCY)]
    for code in _COMMON_FACETS:
        params.append((f"attribute_ids[{code}]", ""))
    for cid in (catalog_ids or []):
        params.append(("attribute_ids[catalog]", str(cid)))
    for key, value in extra.items():
        if value in (None, ""):
            continue
        params = [(pk, pv) for pk, pv in params if pk != key]
        params.append((key, str(value)))
    return urlencode(params)


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


def _flat_size_options(data):
    """The size facet answers with size *groups* (WOMEN-LT, MEN-S, ...) whose
    real values sit in a nested `options` list. Flatten both levels."""
    out = []
    for opt in data.get("options") or []:
        if not isinstance(opt, dict):
            continue
        nested = opt.get("options")
        pairs = _pair_options({"options": nested}) if isinstance(nested, list) else []
        if not pairs:
            pairs = _pair_options({"options": [opt]})
        for pair in pairs:
            if pair not in out:
                out.append(pair)
    return out


def _facet_pairs(code, catalog_ids=None, search_text=None, extra=None):
    """Fetch the options of one facet code."""
    query = _filters_query(catalog_ids, filter_code=code)
    extra = dict(extra or {})
    if search_text:
        extra["search_text"] = search_text
    for key, value in extra.items():
        if value in (None, ""):
            continue
        query = f"{query}&{urlencode({key: value})}"
    try:
        data = api_get(f"{_API_FACETS}?{query}", attempts=2)
    except Exception as e:
        log(f"[warn] '{code}' szűrő opciók betöltése sikertelen: {e}")
        return []
    if code == "size":
        return _flat_size_options(data)
    return _pair_options(data)


def _load_filter_groups(catalog_ids=None):
    """Load {code: {title, options}} for one catalog context."""
    groups = []
    for attempt in range(3):
        try:
            data = api_get(f"{_API_FILTERS}?{_filters_query(catalog_ids)}")
            groups = [g for g in (data.get("filters") or [])
                      if isinstance(g, dict)]
        except Exception as e:
            log(f"[warn] szűrőcsoportok lekérése sikertelen "
                f"({attempt + 1}/3): {e}")
        if groups:
            break
        time.sleep(2)
    if not groups:
        return {}

    filters = {}
    for group in groups:
        code = group.get("code")
        if not code or code == "price":
            continue
        title = group.get("title") or code
        pairs = _pair_options(group) or _facet_pairs(code, catalog_ids)
        if pairs:
            filters[code] = {"title": title, "options": pairs}
    return filters


def fetch_category_filters(cat_url=None, catalog_id=None):
    """Load a category's full live filter set from the svc-filters API.

    Returns {code: {"title": str, "options": [(id, title)]}}. Parent
    categories hold no items of their own, so they fall back to the
    platform-wide facet set to keep the chips populated.
    """
    if catalog_id is None:
        catalog_id = _catalog_id_from_url(cat_url)
    if catalog_id is None:
        return {}
    catalog_id = str(catalog_id)

    filters = _load_filter_groups([catalog_id])
    if not any(v.get("options") for v in filters.values()):
        log("[info] A kategóriának nincs saját tétele — platform szintű "
            "szűrők betöltése.")
        fallback = _load_filter_groups([])
        if any(v.get("options") for v in fallback.values()):
            filters = fallback
    return filters


def fetch_facet_options(search_text="", catalog_ids=None, facet_codes=None):
    """Best-effort fetch of facet options. Returns {code: [(id, title)]}."""
    catalog_ids = [str(c) for c in (catalog_ids or []) if c]
    codes = list(facet_codes or ()) or list(_COMMON_FACETS)
    result = {}
    for code in codes:
        pairs = _facet_pairs(code, catalog_ids, search_text=search_text)
        if pairs:
            result[code] = pairs
    return result


def fetch_brands(keyword, catalog_ids=None):
    """Search brands on Vinted; returns a list of (id, title) tuples."""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    catalog_ids = [str(c) for c in (catalog_ids or []) if c]
    return _facet_pairs("brand", catalog_ids, search_text=keyword)


def _catalog_id_from_url(cat_url):
    """Extract a catalog id from a Vinted catalog URL (path or query form)."""
    if not cat_url:
        return None
    m = re.search(r"/catalog/(\d+)", cat_url)
    if m:
        return int(m.group(1))
    m = re.search(r"catalog\[\]=(\d+)", cat_url)
    return int(m.group(1)) if m else None


def fetch_category_tree(max_age=12 * 3600):
    """Fetch the full category tree (Női, Férfi, … plus subcategories).

    The tree is static, so it is cached in settings.db. It ships in the flight
    payload of any page, which means a plain HTTP request usually suffices; a
    pooled browser is only borrowed when that fails.
    """
    cached = load_setting("category_tree")
    stamp = load_setting("category_tree_at", "0")
    try:
        fresh = (time.time() - float(stamp)) < max_age
    except (TypeError, ValueError):
        fresh = False
    if fresh and cached:
        try:
            tree = json.loads(cached)
            if tree:
                log(f"[dim] Kategóriafa a gyorsítótárból ({len(tree)} gyökér).")
                return tree
        except (ValueError, json.JSONDecodeError):
            pass

    tree = None
    try:
        resp = api_session().get(f"{MARKET_URL}/", headers={
            "accept": "text/html,application/xhtml+xml", "locale": _LOCALE,
        }, timeout=25)
        if resp.ok:
            tree = _extract_flight_json(resp.text, "catalogTree")
    except requests.RequestException as e:
        log(f"[info] HTTP kategóriafa lekérés nem sikerült ({e}), "
            "böngészővel próbálom.")

    if not tree:
        html = get_page_source(f"{MARKET_URL}/", wait=6)
        tree = _extract_flight_json(html, "catalogTree")
    if not tree:
        raise RuntimeError("a kategóriafa nem található az oldalon")

    save_setting("category_tree", json.dumps(tree, ensure_ascii=False))
    save_setting("category_tree_at", str(time.time()))
    return tree


# ─── Watches & main polling loop ────────────────────────────────────────────

@dataclass
class Watch:
    """One monitored search."""
    name: str = "Monitor"
    url: str = ""
    min_price: float = 0.0
    pages: int = 1
    discord: bool = True
    desktop: bool = False

    def to_dict(self):
        return {"name": self.name, "url": self.url, "min_price": self.min_price,
                "pages": self.pages, "discord": self.discord,
                "desktop": self.desktop}

    @classmethod
    def from_dict(cls, data):
        return cls(
            name=str(data.get("name") or "Monitor"),
            url=str(data.get("url") or ""),
            min_price=float(data.get("min_price") or 0),
            pages=max(1, int(data.get("pages") or 1)),
            discord=bool(data.get("discord", True)),
            desktop=bool(data.get("desktop", False)),
        )


@dataclass
class CycleResult:
    """What one pass over a watch produced."""
    watch: str = ""
    fetched: int = 0
    new_items: list = field(default_factory=list)
    price_drops: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def new_count(self):
        return len(self.new_items)


def run_cycle(conn, watch, on_new=None):
    """Fetch, filter and store one watch's pages exactly once.

    `on_new(item, watch)` is called for every newly seen item so the GUI can
    update its result table and raise a desktop notification. Returns a
    `CycleResult` describing what changed.
    """
    result = CycleResult(watch=watch.name)
    if not watch.url:
        result.errors.append("nincs cél-URL")
        return result

    seen_ids = set()
    for page in range(1, max(1, watch.pages) + 1):
        url = build_page_url(watch.url, page)
        try:
            items, source = fetch_page_items(url)
        except Exception as e:
            result.errors.append(f"{page}. oldal: {e}")
            log(f"[err] {watch.name} — {page}. oldal betöltése sikertelen: {e}")
            continue

        result.fetched += len(items)
        if page == 1:
            log(f"[dim] {watch.name} — {len(items)} tétel ({source})")

        for item in items:
            if item["id"] in seen_ids:
                continue
            seen_ids.add(item["id"])
            if watch.min_price > 0 and item["price_num"] < watch.min_price:
                continue

            try:
                is_new, previous = check_and_insert(conn, item, watch.name)
            except sqlite3.Error as e:
                result.errors.append(f"adatbázis: {e}")
                log(f"[err] adatbázis hiba: {e}")
                continue

            if is_new:
                result.new_items.append(item)
                if on_new:
                    try:
                        on_new(item, watch)
                    except Exception as e:
                        log(f"[warn] új találat callback hiba: {e}")
            elif previous is not None and item["price_num"] < previous - 0.01:
                result.price_drops.append((item, previous))
    return result


def main_loop(watches, log_func=print, stop_event=None,
              interval=(300, 600), webhook_url="", desktop_notify=None,
              max_alerts=15):
    """Poll every watch in turn: fetch → filter → store → alert → sleep.

    `desktop_notify(item, watch)` is an optional callback for OS-level
    notifications. Returns when `stop_event` is set.
    """
    set_log_sink(log_func)

    watches = [w for w in (watches or []) if getattr(w, "url", "")]
    if not watches:
        raise ValueError("Legalább egy figyelendő keresés szükséges.")

    sender = DiscordSender(webhook_url)
    conn = init_db()
    stamp = lambda: datetime.now().strftime("%H:%M:%S")
    say = lambda m: log(f"[{stamp()}] {m}")

    say(f"{len(watches)} figyelés indítva · Discord={'be' if sender.configured else 'ki'}")
    for w in watches:
        say(f"  • {w.name}: {w.pages} oldal, min. {int(w.min_price)} {CURRENCY}"
            + ("" if w.desktop else ", desktop értesítés kikapcsolva"))

    if not sender.configured:
        say("[warn] Nincs Discord webhook — az értesítések kimaradnak.")

    try:
        while not (stop_event and stop_event.is_set()):
            cycle_start = time.time()
            for watch in watches:
                if stop_event and stop_event.is_set():
                    break
                result = run_cycle(conn, watch,
                                   on_new=desktop_notify if watch.desktop else None)
                for err in result.errors:
                    say(f"[err] {watch.name}: {err}")
                if not result.new_items and not result.price_drops:
                    say(f"{watch.name}: nincs új tétel "
                        f"({result.fetched} ellenőrizve)")
                    continue

                if result.new_items:
                    say(f"{watch.name}: {result.new_count} új tétel "
                        f"({result.fetched} ellenőrizve)")
                    for item in result.new_items:
                        say(f"NEW [{watch.name}] {item['title'][:80]} — "
                            f"{item['price']}")
                for item, previous in result.price_drops:
                    say(f"ÁRCSÖKKENÉS [{watch.name}] {item['title'][:70]} — "
                        f"{int(previous)} → {int(item['price_num'])} {CURRENCY}")

                if sender.configured and watch.discord and result.new_items:
                    for item in result.new_items[:max_alerts]:
                        sender.send(item)
                    remaining = result.new_count - max_alerts
                    if remaining > 0:
                        sender.send_note(
                            f"…és még {remaining} új tétel a(z) "
                            f"„{watch.name}” figyelésben.")
                    elif result.new_count > 3:
                        sender.send_note(
                            f"Összesen {result.new_count} új tétel a(z) "
                            f"„{watch.name}” figyelésben.")

            if stop_event and stop_event.is_set():
                break

            delay = random.uniform(*interval)
            sleep_time = max(0.0, delay - (time.time() - cycle_start))
            lo, hi = (interval[0] / 60.0, interval[1] / 60.0)
            say(f"Következő ciklus {sleep_time / 60:.1f} perc múlva "
                f"(intervallum {lo:.0f}–{hi:.0f} perc)")

            end = time.time() + sleep_time
            while time.time() < end:
                if stop_event and stop_event.wait(min(1.0, end - time.time())):
                    break
    finally:
        try:
            conn.close()
        except Exception:
            pass
        say("Leállva, adatbázis kapcsolat bezárva.")


# ─── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("A monitor a grafikus feluleten indithato: py monitor_gui.py")
    print("A keresesi URL-ek a GUI-bol epulnek fel a kivalasztott szurokből.")
