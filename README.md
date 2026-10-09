# Vinted Smart Price Monitor 🐍 🛍️

> Verzió: **0.9**

A lightweight, standalone Python automation script designed to monitor specific search queries on Vinted in real-time. The script analyzes listings, filters out duplicates using a local database, and dispatches instant rich alerts via Discord Webhooks when an item is found.

Built to demonstrate **Python-based web automation, resilient scraping practices, and efficient scripting architecture** without depending on a heavy web framework.

---

## 🛠️ Tech Stack & Architecture

* **Language:** Python 3.x
* **Automation & Scraping:** `Selenium` + `selenium-stealth` (only for the item pages, which must be rendered by a real browser)
* **APIs:** `Requests` for every Vinted JSON endpoint (filters, brands, catalog tree)
* **Data Parsing:** structured JSON out of the page's Next.js *flight payload*, with `BeautifulSoup4` as a DOM fallback
* **Database:** `SQLite` (a self-contained, zero-configuration SQL database engine to track seen items)
* **Notifications:** `Requests` library to communicate with the Discord Webhook API

### Hogyan működik

```
szűrő űrlap ──► szűrő API (requests + Vinted sütik) ──► chip-ek
                        │
                        ▼
                   kereső URL  ──►  ┌─ pooled headless Chrome ─┬─ repülő-adat (JSON) ─┐
                                     │                       └─ DOM fallback (BS4) ──┤
                                     │                                                 ▼
                                     └──────────────►  SQLite  ──►  Discord + GUI tábla
```

* **Böngészők poolban futnak** (`BrowserPool`): kettő példány, újrahasznosítva.
  Egy böngésző indítása ~5 s, ezért a szűrők és a kategóriafa betöltése már
  nem indít új Chrome-ot minden híváskor.
* **A szűrőkhöz nem kell böngésző.** Egyszer elindul egy böngésző, a Vinted
  sütit és a böngésző `User-Agent`-jét átvesszük a `requests` sessionbe, és
  utána minden szűrő-API-hívás sima HTTP. A korábbi 30 s-os betöltés **0,5 s**.
* **A tételek nem a DOM-ból jönnek.** A katalóguslap a flight payloadban
  `{"items":[…]}` blokkot tartalmaz, tele tisztább adatokkal (lásd alább).

---

## 🚀 Key Features

* **Live Category Facets:** selecting a category loads its real filter groups (brand, size, color, material, status, pattern) straight from the Vinted API, so the filter chips are never empty — in about half a second.
* **Structured item extraction:** title, numeric price, full-size image URL, size and status are read from the page's flight payload, so nothing depends on fragile CSS selectors. The DOM parser stays as a fallback.
* **Pagination:** a watch can walk up to 10 result pages per cycle (`?page=N`), which Vinted serves as fresh, nearly disjoint item sets.
* **Multiple watches:** any number of saved searches, each with its own keyword, filters, page count, minimum price and notification targets. They are polled in one rotating cycle and survive a restart. Watches can be **renamed** and given a **short description** ("mit figyel") — shown as a second line under each watch name, or auto-generated from the filters when left empty.
* **Local minimum price:** a per-watch threshold applied *after* download, so it filters without giving up the Vinted-side price range.
* **Smart de-duplication:** an item is only reported the first time it is seen; later sightings just bump a counter. Price drops on already-known items are logged separately.
* **Anti-Bot Resiliency:** randomized delay intervals (*jitter*), a real browser user agent and `selenium-stealth` to prevent IP rate-limiting.
* **Result table:** every found item appears in the GUI as it arrives, with a double-click to open it on Vinted. The table is capped at 400 rows and re-loadable from the database.
* **Desktop notifications:** an optional beep and immediate table row per new item, independent of Discord.
* **Modular, button-free navigation:** the app is built from three independent modules — **Keresés**, **Találatok**, **Napló** — switched from a left sidebar. The search form sits at the top of the first one, your saved watches right below it. No button maze, no drag, no docking, no collapse arrows. The active view is remembered across restarts, and each module is built once, so the form state, the watch list and the result table all survive switching views.
* **Everything in one header:** the top bar carries the brand, the live status (dot + text), the "new items" badge and the single primary action (**Indítás / Leállítás**). Secondary actions are quiet text links in each module's own header; watch rows use double-click to edit and the right mouse button for a small menu, instead of per-row icons.
* **Live, themeable UI:** a **Modern (dark)** theme can be switched to live from the Settings window, next to the default **Classic (light)** look — sidebar, log, result table and every widget repaint instantly, and the choice is saved.

---

## 📋 Prerequisites & Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/MiskolcziLevente05/vinted-smart-analyst.git
   cd vinted-smart-analyst
   ```

2. Install the dependencies:
   ```bash
   py -m pip install -r requirements.txt
   ```

3. Run the GUI:
   ```bash
   py monitor_gui.py
   ```

The dependency versions are pinned to the ones this was verified against.

---

## 🧩 Kategóriaszűrők (facetek)

A Vinted átalakította a szűrő-rendszerét: a korábbi
`/api/v2/catalog/filters` végpont megszűnt (404), ezért a régi kódban a
kategóriaszűrők sosem töltődtek be. Az új API:

| Célt | Végpont |
|------|---------|
| Szűrőcsoportok | `https://api.vinted.hu/svc-filters/filters` |
| Egy csoport opciói | `https://api.vinted.hu/svc-filters/filters/facets?filter_code=<code>` |
| Márkakeresés | ugyanez `search_text=<kifejezés>` paraméterrel |

A kiválasztott kategóriát és szűrőket az `attribute_ids[<code>]`
paraméterek adják át, pl. `attribute_ids[catalog]=2525&attribute_ids[brand]=…`.

Az API a címeket a **`Locale`** HTTP-fejléc alapján lokalizálja (nem
`Accept-Language` alapján), ezért a kód `Locale: hu-HU`, `Platform: web`
és `x-next-app: marketplace-web` fejléceket küld — enélkül francia
feliratokat kapnánk.

Két dolog még fontos:

* **A `svc-filters` API sütit és valódi böngésző-`User-Agent`-et igényel.**
  Süti vagy `User-Agent` nélkül a Vinted DataDome rétege `403`-mal utasítja
  el a kérést, függetlenül attól, hogy mennyire érvényes a query. Ezért a
  sessiont egyetlen böngészőélátogatás veti be (ez ~14 s, a betöltő ablak
  alatt fut), és 30 percig újrahasználandó. Utána **minden szűrő-API-hívás
  ~0,1 s**. 403 esetén a kód egyszer automatikusan újraseedeli a sütiket,
  és ezt a naplóban jelzi is.
* **A `size` facet speciális:** méretcsoportokat (`WOMEN-LT`) ad vissza,
  amelyek egy beágyazott `options` listában tartalmazzák a valódi
  méreteket; a kód ezeket lapítja.

A szülő kategóriáknál (amiknek nincs saját termékük) a betöltés
automatikusan platform-szintű facetkészletre vált. A kategóriafa statikus,
ezért 12 órára gyorsítótárazva kerül a `settings.db`-be.

---

## 📦 A tételek kinyerése: repülő-adat a DOM helyett

A Vinted katalóguslapja egy Next.js *flight payload*-ot tartalmaz, és ebben
ott van egy `{"items":[…]}` blokk. A benne lévő rekordok sokkal többet adnak,
mint a DOM:

| Mező | Forrás | Előny |
|------|--------|-------|
| `title` | `productItem.title` | nincs HTML-entity, nincs levágás |
| `price` | `productItem.price.amount` | valódi szám, nem szövegből parse-olva |
| `url` | `productItem.url` | tiszta slug URL |
| `image` | `productItem.photos[0].url` | teljes méretű kép, Discord embedhez |
| méret + állapot | `itemBox.secondLine` | `"EU 44 · Kiváló"` formában |
| `favouriteCount` | `productItem.favouriteCount` | kedvencek száma |

Ha a repülő-adatban nincs tétel-blokk (változott az oldal felépítése), a kód
visszaesik a `BeautifulSoup`-os DOM-parserre, így a monitor nem áll le.

---

## 🌐 Böngésző driver

A monitor a Vinted tételoldalait valódi headless Chrome-bal tölti be — ezek
a flight payload miatt nem kérdezhetők le `requests`-szel. A
`chromedriver`-t a **Selenium Manager** letölti az első használatkor, ezért
**az első indításhoz internetkapcsolat kell**.

A `~/.cache/selenium/chromedriver/` gyorsítótárban lévő driver-t a kód
közvetlenül használja, így a későbbi indítások nem igényelnek hálózatot.
Elavult gyorsítótár esetén automatikusan visszaesik a Selenium Managerre.
Net nélküli első indításkor a hiba magyarul magyarázza a helyzetet.

A pool legfeljebb két böngészőt tart életben. Kilépéskor a futó szál
lecsuppan, majd a pool minden példányt `quit()`-eli — nem marad kísért
`chromedriver` folyamat a háttérben.

---

## ⚙️ Konfiguráció

Nincs `.env` / dotenv konfiguráció. A Discord webhook URL-t a **Beállítások**
ablakban lehet megadni; üresen a Discord értesítés kimarad, a monitor
minden más tekintetben változatlanul fut.

A beállítások külön **`settings.db`** SQLite fájlban őrződnek meg, így a
webhook és a figyelések túléli az újraindítást:

| Fájl | Tartalom | Élettartam |
|------|----------|------------|
| `vinted_monitor.db` | a monitor által talált termékek | minden indításkor újraindul¹ |
| `settings.db` | webhook, figyelések, intervallum, előzmény-beállítás, kategóriafa-cache, téma, aktív nézet | tartós |

¹ Hacs a **Beállítások → Előzmények megőrzése** nincs bekapcsolva. Ekkor a
`vinted_monitor.db` megmarad, és a GUI a találatok előzményét is betölti a
táblázatba.

A **Kész** gomb — és az ablak X-e — azonnal elment, szerkesztés közben pedig
0,6 másodperces késleltetéssel automatikusan ment (debounce).

### Discord

* Az értesítések **rate-limitáltak** (kb. 5 kérés / 2 mp), és ciklusonként
  legfeljebb *Discord üzenet / ciklus* darab men ki; a többi
  „…és még N új tétel" összefoglalóként érkezik.
* A **Teszt** gomb élő ellenőrzést futtat, és megkülönbözteti a hálózati
  hibát, a 401-et (törölt webhook) és a 404-et (nincs ilyen webhook).
* Indításkor az URL *alakja* is ellenőrzött, hogy egy elgépelt URL ne
  csendben nyelje el az összes riasztást.

### Felépítés

Az alkalmazás három rétegből áll, mindegyik önálló felelősséggel:

| Réteg | Tartalom |
|-------|----------|
| **Téma & stílus** | `PALETTE_KEYS`, `THEMES`, `_apply_palette`, `_recolor_widgets`, `UI` — minden szín a palettából jön, ezért a téma élőben cserélhető |
| **Összetevők** | `ScrollFrame`, `ResultsPanel`, `MultiSelectPopup`, `CategoryTreePopup`, `WatchDialog`, `LoadingWindow`, `SettingsWindow` — újrahasznosítható widgetek és dialógusok |
| **Modulok** | `Module` bázis + `SearchModule`, `ResultsModule`, `LogModule` — a három nézet tartalma |

* **Három modul, egy keretváz:** a `VintedMonitorGUI` csak a héjat építi (fejléc,
  navigációs sáv, modul-tér), a nézetek tartalmát a modulok adják. Egy modul
  egyszer épül meg, a navigáció csak megmutatja/elrejti — soha nem születhet
  újra, így az űrlap és a táblázat állapota sosem vész el.
* **Navigációs sáv:** feliratok (nem gombok) a bal oldalon; az aktív nézet
  kiemeléssel látszik, a sáv alján a **Beállítások** hivatkozás. A feliratok a
  modulok `nav` értékéből jönnek, így egyetlen helyen kell módosítani.
* **Keresés és figyelések egy nézetben, egymás alatt:** felül a szűrő-űrlap,
  alatta a mentett figyelések. Egy lépésben hozzáadsz egy figyelést a lista
  címsorában lévő `＋ Új figyelés` linkkel, és nem kell nézetet váltanod, hogy
  lássad, mit mentettél.
* **A figyelések listája önállóan görget:** az űrlap fix magasságú, a lista
  kitölti alatta a maradék helyet, és **külön görgetősávon** érhető el benne a
  többi figyelés. Így a szűrők mindig látszanak, és egy hosszú lista nem
  nyeli el a nézetet — a minimum ablakméretben is három figyelés látszik.
  Csak akkor jelenik meg a sáv, ha a figyelések tényleg nem férnek bele.
* **Görgetés modulonként:** a **Keresés** két blokkból áll (űrlap + figyelés-lista),
  mindkettő saját görgetős lappal; a **Találatok** és a **Napló** a teljes
  területet kitölti és maga görget. A görgő felett a lapot az görgeti, ahol a
  widget nem görget önmaga.
* **Nincs átalakítás-mechanika:** se húzás, se dokkolás, se összecsukás, se
  találat-váltás. Ehelyett a figyelési sorokon dupla kattintás szerkeszt, a
  jobb gomb menüt ad (Szerkesztés / Törlés).
* **Emlékezet:** az aktív nézet a `settings.db` `view` kulcsában őrződik, így
  a következő indításkor ugyanott nyílik meg a program.

### Megjelenés (témák)

A **Beállítások** ablak „Megjelenés” sorában két téma közül választhatsz —
a váltás azonnal, újraindítás nélkül érvényesül, és mentődik:

| Téma | Jellemzők |
|------|-----------|
| **Klasszikus (világos)** | az alapértelmezett, világos kártyákkal és türkiz akcentussal |
| **Modern (sötét)** | sötét, lapos megjelenés kék akcentussal; a napló, a találati tábla és minden kártya átfestődik |

A színek moduláris palettából épülnek fel (`THEMES`), így egy új téma
hozzáadása a jövőben egyetlen szótár-bejegyzés.

### Billentyűparancsok

| Billentyű | Művelet |
|-----------|---------|
| `Ctrl+Enter` | indítás / leállítás |
| `Ctrl+L` | napló-nézet megjelenítése / visszatérés az előző nézetre |
| `Ctrl+,` | beállítások |
| `Ctrl+F` | kereső-nézet + fókusz a kulcsszó mezőre |
| `Esc` | beállítások bezárása, illetve a monitor leállítása |
| dupla kattintás | figyelés szerkesztése, illetve találat megnyitása böngészőben |
| jobb gomb | figyelés menü (Szerkesztés / Törlés) |
| görgő a modulon | a modul görgetése (a napló/táblázat fölött azok görgetnek) |

---

## ✅ Tesztek

Két önálló teszt-csomag van, egyik sem igényel hálózatot, böngészt, vagy a
valódi adatbázisokat:

| Csomag | Fedettség | Futtatás |
|--------|-----------|----------|
| `test_core.py` | az üzleti logika: URL-építés, repülő-adat parser, ár-normalizálás, szűrő-képzés, beállítás- és adatbázis-kezelés, a ciklus és a Discord-küldő (~130 teszt) | `py -m unittest test_core` |
| `test_gui.py` | a felület valódi Tk ablakkal: elrendezés, görgetés, modul-váltás, chip-ek, felugró ablakok, téma, indulás (~170 teszt) | `py -m unittest test_gui` |

A GUI-tesztek átlátszó ablakban futnak, a `settings.db` és a
`vinted_monitor.db` útvonalát pedig ideiglenes könyvtárra írják át, így a
két fájl tartalma a futás után érintetlen marad. A hálózati és
adatbázis-hívásokat a tesztek stubbolják — a böngésző-pool nem indul el.
