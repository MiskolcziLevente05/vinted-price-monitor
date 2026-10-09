"""A `monitor.py` magjának tesztjei.

Futtatás:  `python -m unittest test_core -v`

Nincs hálózat, nincs böngésző és nincs Tk: a hálózati réteget
`monkeypatch`-szel (unittest.mock) cseréljük, az adatbázisok pedig egy
ideiglenes könyvtárba mutatnak, így a teszt a valódi `settings.db`-t és
`vinted_monitor.db`-t sosem érinti.
"""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import monitor
from monitor import (
    CURRENCY,
    CycleResult,
    DiscordSender,
    Watch,
    build_page_url,
    build_search_url,
    check_and_insert,
    check_webhook_format,
    count_listings,
    fetch_recent,
    init_db,
    load_bool,
    load_setting,
    main_loop,
    parse_price_numeric,
    run_cycle,
    save_bool,
    save_setting,
    _catalog_id_from_url,
    _extract_flight_json,
    _filters_query,
    _flat_size_options,
    _match_bracket,
    _normalize_item,
    _pair_options,
    _split_size_status,
)

sys.stdout.reconfigure(encoding="utf-8")


def setUpModule():
    """A napló ne írjon ki a konzolra — minden teszt maga fogadja, ami kell."""
    monitor.set_log_sink(lambda msg: None)


def tearDownModule():
    monitor.set_log_sink(None)


# ─── Segéd: ideiglenes adatbázisok ─────────────────────────────────────────

class TempDbMixin:
    """A modul adatbázis-útvonalait egy temp könyvtárba irányítja."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = os.path.join(self._tmp.name, "vinted_monitor.db")
        settings = os.path.join(self._tmp.name, "settings.db")
        for name, value in (("DB_PATH", db), ("SETTINGS_DB_PATH", settings)):
            patcher = mock.patch.object(monitor, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


def item(**over):
    """Egy minimális, a `check_and_insert` által elfogyadott tétel."""
    base = {"id": "1", "title": " pulovka ",
            "price": "2500 HUF", "price_num": 2500.0,
            "url": "https://www.vinted.hu/items/1",
            "image": None, "size": "M", "status": "Jó", "favourites": 3}
    base.update(over)
    return base


# ─── URL-építés ────────────────────────────────────────────────────────────

class BuildSearchUrlTests(unittest.TestCase):

    def test_only_the_currency_is_appended_when_nothing_is_set(self):
        # a `currency` kötelező a Vintednek, ezért üres paraméterek mellett is
        # a `?currency=HUF` a lépésvétel vége
        self.assertEqual(build_search_url(),
                         f"{monitor.MARKET_URL}/catalog?currency={CURRENCY}")
        self.assertEqual(build_search_url({}), build_search_url())
        self.assertEqual(build_search_url(None), build_search_url({}))

    def test_currency_is_always_present(self):
        self.assertEqual(build_search_url({}),
                         f"{monitor.MARKET_URL}/catalog?currency={CURRENCY}")

    def test_scalar_params_keep_their_own_name(self):
        url = build_search_url({"search_text": "kabát", "order": "newest_first",
                                "price_from": "1000", "price_to": "5000"})
        self.assertIn("search_text=kab%C3%A1t", url)
        self.assertIn("order=newest_first", url)
        self.assertIn("price_from=1000", url)
        self.assertIn("price_to=5000", url)

    def test_empty_scalars_are_skipped(self):
        url = build_search_url({"search_text": "", "order": None,
                                "price_from": "", "price_to": None})
        self.assertNotIn("search_text", url)
        self.assertNotIn("order", url)
        self.assertNotIn("price_from", url)
        self.assertNotIn("price_to", url)

    def test_catalog_uses_brackets_but_other_lists_use_ids(self):
        url = build_search_url({"catalog": ["1234"], "color": [7, 8]})
        self.assertIn("catalog%5B%5D=1234", url)
        self.assertEqual(url.count("catalog%5B%5D="), 1)
        self.assertIn("color_ids%5B%5D=7", url)
        self.assertIn("color_ids%5B%5D=8", url)

    def test_blank_list_members_are_dropped(self):
        url = build_search_url({"color": [7, "", "  ", 8]})
        self.assertEqual(url.count("color_ids%5B%5D="), 2)

    def test_bool_switches_map_to_vinted_flag_values(self):
        url = build_search_url({"discount": True, "favourite": True,
                                "handicraft": True, "give_away": True})
        self.assertIn("discount_ids%5B%5D=4", url)
        self.assertIn("favourite=true", url)
        self.assertIn("is_handicraft=true", url)
        self.assertIn("is_for_give_away=true", url)

    def test_falsy_switches_add_nothing(self):
        url = build_search_url({"discount": False, "favourite": 0,
                                "handicraft": None, "give_away": ""})
        self.assertEqual(url, f"{monitor.MARKET_URL}/catalog?currency={CURRENCY}")

    def test_a_scalar_in_a_list_param_is_not_duplicated(self):
        # a `currency` listként nem kerülhet át a `{code}_ids[]` ágra
        url = build_search_url({"currency": ["HUF"]})
        self.assertEqual(url.count("currency"), 1)
        self.assertNotIn("currency_ids", url)


class BuildPageUrlTests(unittest.TestCase):

    def test_first_page_is_the_bare_url(self):
        self.assertEqual(build_page_url("https://x.hu/catalog?a=1", 1),
                         "https://x.hu/catalog?a=1")
        self.assertEqual(build_page_url("https://x.hu/catalog?a=1", 0),
                         "https://x.hu/catalog?a=1")

    def test_later_pages_append_the_page_parameter(self):
        self.assertEqual(build_page_url("https://x.hu/catalog?a=1", 3),
                         "https://x.hu/catalog?a=1&page=3")

    def test_queryless_url_gets_a_question_mark(self):
        self.assertEqual(build_page_url("https://x.hu/catalog", 2),
                         "https://x.hu/catalog?page=2")


class CatalogIdFromUrlTests(unittest.TestCase):

    def test_path_form(self):
        self.assertEqual(_catalog_id_from_url("https://www.vinted.hu/catalog/1605"),
                         1605)

    def test_query_form(self):
        # a kategória-fa URL-je nyers, nem percent-kódolt formában érkezik
        self.assertEqual(
            _catalog_id_from_url("https://www.vinted.hu/catalog?catalog[]=42"),
            42)

    def test_unrelated_and_empty_urls(self):
        self.assertIsNone(_catalog_id_from_url("https://www.vinted.hu/"))
        self.assertIsNone(_catalog_id_from_url(""))
        self.assertIsNone(_catalog_id_from_url(None))


# ─── Ár- és szövegértelmezés ──────────────────────────────────────────────

class PriceTests(unittest.TestCase):

    def test_hungarian_formatting(self):
        self.assertEqual(parse_price_numeric("1 400 Ft"), 1400.0)
        self.assertEqual(parse_price_numeric("1 400 Ft".replace(" ", "\xa0")),
                         1400.0)
        self.assertEqual(parse_price_numeric("999 HUF"), 999.0)

    def test_decimals_with_comma_and_dot(self):
        self.assertEqual(parse_price_numeric("1 234,56 Ft"), 1234.56)
        self.assertEqual(parse_price_numeric("1,234.56 Ft"), 1234.56)

    def test_free_text_still_yields_the_digits(self):
        self.assertEqual(parse_price_numeric("Kedvezményes 2 500 Ft"), 2500.0)

    def test_unusable_values_become_zero(self):
        for value in (None, "", "ingyen", "Ft", "-"):
            self.assertEqual(parse_price_numeric(value), 0.0, value)


class SplitSizeStatusTests(unittest.TestCase):

    def test_size_and_status(self):
        self.assertEqual(_split_size_status("M · Jó"), ("M", "Jó"))

    def test_status_only(self):
        self.assertEqual(_split_size_status("Kiváló"), (None, "Kiváló"))

    def test_multi_part_size_is_joined_with_slash(self):
        self.assertEqual(_split_size_status("L / 40 / 12 · Kiváló"),
                         ("L / 40 / 12", "Kiváló"))

    def test_size_only(self):
        self.assertEqual(_split_size_status("EU 44"), (None, "EU 44"))

    def test_unknown_trailing_word_becomes_the_status(self):
        self.assertEqual(_split_size_status("M · valami"), (None, "M · valami"))

    def test_empty_line(self):
        self.assertEqual(_split_size_status(""), (None, None))
        self.assertEqual(_split_size_status(None), (None, None))
        self.assertEqual(_split_size_status("  ·  "), (None, None))


# ─── Flight payload elemzés ────────────────────────────────────────────────

def push_chunk(payload):
    """Egy `self.__next_f.push([1, "…"])` darab nyers tartalma.

    A darabban a szöveg JSON-escape-elt, idézőjel nélkül — ezt a
    `_flight_payload` `json.loads`-szal oldja vissza. A szétválasztó
    szóköz nélkül kell, mert az `_extract_items` a tömör `{"items":[`
    nyitást keresi, ahogy a valódi oldalon is.
    """
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False,
                             separators=(",", ":"))
    return payload.replace("\\", "\\\\").replace('"', '\\"')


def flight_page(*payloads):
    """Több push-darabból álló, valódi HTML-t ad vissza."""
    return "".join('<script>self.__next_f.push([1,"%s"])</script>'
                   % push_chunk(p) for p in payloads)


class FlightPayloadTests(unittest.TestCase):
    """A `self.__next_f.push([1, "…"])` darabokból épülő payload."""

    @staticmethod
    def page(*payloads):
        return flight_page(*payloads)

    def test_match_bracket_respects_strings_and_nesting(self):
        s = '[{"a": "]}"}], 2]'
        self.assertEqual(_match_bracket(s, 0), '[{"a": "]}"}]')

    def test_match_bracket_returns_none_when_unbalanced(self):
        self.assertIsNone(_match_bracket("[1, 2", 0))

    def test_escaped_quote_does_not_end_the_string(self):
        # a `\"` nem zárja a stringet, így a záró `]` a 9. helyen van
        self.assertEqual(_match_bracket(r'["a\"]", 1]', 0), r'["a\"]", 1]')

    def test_push_chunks_are_joined_into_one_payload(self):
        self.assertEqual(monitor._flight_payload(self.page("a", "b")), "ab")

    def test_broken_push_chunks_are_skipped(self):
        html = ('self.__next_f.push([1,"ok"])'
                'self.__next_f.push([1,"also"])')
        self.assertEqual(monitor._flight_payload(html), "okalso")

    def test_extract_flight_json_finds_the_key(self):
        html = self.page("prefix", {"tree": {"a": 1}}, "suffix")
        self.assertEqual(_extract_flight_json(html, "tree"), {"a": 1})

    def test_extract_flight_json_handles_arrays(self):
        html = self.page({"tree": [1, 2]})
        self.assertEqual(_extract_flight_json(html, "tree"), [1, 2])

    def test_extract_flight_json_returns_none_when_absent(self):
        self.assertIsNone(_extract_flight_json(self.page({"other": 1}), "tree"))
        self.assertIsNone(_extract_flight_json("<html></html>", "tree"))

    def test_scalar_value_is_not_a_json_document(self):
        self.assertIsNone(_extract_flight_json(self.page({"tree": 7}), "tree"))

    def test_later_occurrence_wins_when_the_first_is_broken(self):
        html = self.page("tree", "not json at all")
        self.assertIsNone(_extract_flight_json(html, "tree"))

    def test_a_key_split_across_two_chunks_is_still_found(self):
        # a repülő darabonként jön, ezért a kulcs szétválhat
        html = self.page("x", '"tre', 'e": [1, 2]')
        self.assertEqual(_extract_flight_json(html, "tree"), [1, 2])


class NormalizeItemTests(unittest.TestCase):

    def test_nested_product_item_is_unwrapped(self):
        got = _normalize_item({"id": "outer", "productItem": {
            "id": 99, "title": "  Kabát  ",
            "url": "/items/99", "price": {"amount": "2500.00",
                                          "currencyCode": "HUF"},
            "favouriteCount": 7, "thumbnailUrl": "t.jpg",
            "itemBox": {"secondLine": "L · Kiváló"}}})
        self.assertEqual(got["id"], "99")
        self.assertEqual(got["title"], "Kabát")
        self.assertEqual(got["url"], f"{monitor.MARKET_URL}/items/99")
        self.assertEqual(got["price_num"], 2500.0)
        self.assertEqual(got["price"], "2500 HUF")
        self.assertEqual(got["image"], "t.jpg")
        self.assertEqual((got["size"], got["status"]), ("L", "Kiváló"))
        self.assertEqual(got["favourites"], 7)

    def test_a_grouped_price_is_still_understood(self):
        # a magyar formátum vékony szóközt és tizedesvesszőt használ
        for amount, expected in (("2 500,00", 2500.0), ("2 500,00", 2500.0),
                                 ("2500", 2500.0), ("2500.50", 2500.5),
                                 ("2 500,50", 2500.5)):
            got = _normalize_item({"id": 1, "title": "x",
                                   "price": {"amount": amount}})
            self.assertEqual(got["price_num"], expected, amount)

    def test_flat_record_without_product_item_works(self):
        got = _normalize_item({"id": 5, "title": "Nadrág", "price": 1200})
        self.assertEqual(got["id"], "5")
        self.assertEqual(got["price_num"], 1200.0)
        self.assertEqual(got["price"], "1200 HUF")

    def test_discounted_price_wins_over_the_original(self):
        got = _normalize_item({"id": 1, "title": "x", "price": {"amount": 3000},
                               "priceWithDiscount": {"amount": 2000}})
        self.assertEqual(got["price_num"], 2000.0)

    def test_photos_take_precedence_over_the_thumbnail(self):
        got = _normalize_item({"id": 1, "title": "x", "price": 1,
                               "thumbnailUrl": "t.jpg",
                               "photos": [{"url": "p1.jpg"}, {"url": "p2.jpg"}]})
        self.assertEqual(got["image"], "p1.jpg")

    def test_absolute_url_is_left_alone(self):
        got = _normalize_item({"id": 1, "title": "x", "price": 1,
                               "url": "https://other.hu/items/1"})
        self.assertEqual(got["url"], "https://other.hu/items/1")

    def test_record_without_id_is_dropped(self):
        self.assertIsNone(_normalize_item({"title": "x"}))
        self.assertIsNone(_normalize_item({}))
        self.assertIsNone(_normalize_item(None))

    def test_unparsable_price_becomes_zero(self):
        got = _normalize_item({"id": 1, "title": "x", "price": "ingyen"})
        self.assertEqual(got["price_num"], 0.0)
        self.assertEqual(got["price"], f"0 {CURRENCY}")


class ExtractItemsTests(unittest.TestCase):

    @staticmethod
    def page(records):
        return flight_page({"items": records})

    def test_records_are_normalized(self):
        html = self.page([{"id": 1, "title": "A", "price": 100},
                          {"id": 2, "title": "B", "price": 200}])
        items = monitor._extract_items(html)
        self.assertEqual([i["title"] for i in items], ["A", "B"])
        self.assertEqual([i["price_num"] for i in items], [100.0, 200.0])

    def test_broken_records_are_skipped(self):
        html = self.page([{"title": "nincs id"},
                          {"id": 2, "title": "B", "price": 200}])
        self.assertEqual(len(monitor._extract_items(html)), 1)

    def test_truncated_payload_gives_an_empty_list(self):
        html = self.page([{"id": 1, "title": "A", "price": 1}])[:-40]
        self.assertEqual(monitor._extract_items(html), [])

    def test_page_without_items_gives_an_empty_list(self):
        self.assertEqual(monitor._extract_items("<html></html>"), [])

    def test_extract_items_falls_back_to_the_dom(self):
        html = ('<div data-testid="grid-item"><a href="/items/7" '
                'title="Nadrág, 42"></a>'
                '<span data-testid="grid-item--price-text">2 500 Ft</span>'
                '</div>')
        items, source = monitor.extract_items(html)
        self.assertEqual(source, "dom")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "7")
        self.assertEqual(items[0]["title"], "Nadrág")
        self.assertEqual(items[0]["price_num"], 2500.0)
        self.assertEqual(items[0]["url"],
                         f"{monitor.MARKET_URL}/items/7")

    def test_the_structured_payload_wins_over_the_dom(self):
        html = ('<div data-testid="grid-item"><a href="/items/7" '
                'title="DOM"></a></div>')
        html += self.page([{"id": 1, "title": "JSON", "price": 1}])
        items, source = monitor.extract_items(html)
        self.assertEqual(source, "payload")
        self.assertEqual([i["title"] for i in items], ["JSON"])

    def test_a_page_with_nothing_gives_an_empty_list(self):
        items, source = monitor.extract_items("<html></html>")
        self.assertEqual(items, [])
        self.assertEqual(source, "dom")


# ─── Facet-válaszok ───────────────────────────────────────────────────────

class FacetOptionTests(unittest.TestCase):

    def test_pairs_need_both_id_and_title(self):
        data = {"options": [{"id": 1, "title": "Piros"},
                            {"id": 2},
                            {"title": "Nincs id"},
                            {"id": 3, "name": "Név helyett"},
                            "nem dict"]}
        self.assertEqual(_pair_options(data), [(1, "Piros"), (3, "Név helyett")])

    def test_missing_options_gives_an_empty_list(self):
        self.assertEqual(_pair_options({}), [])
        self.assertEqual(_pair_options({"options": None}), [])

    def test_size_groups_are_flattened(self):
        data = {"options": [
            {"id": 10, "title": "WOMEN-LT",
             "options": [{"id": 100, "title": "XS"}, {"id": 101, "title": "S"}]},
            {"id": 11, "title": "MEN-S",
             "options": [{"id": 102, "title": "M"}]},
        ]}
        self.assertEqual(_flat_size_options(data),
                         [(100, "XS"), (101, "S"), (102, "M")])

    def test_group_without_children_stays_flat(self):
        data = {"options": [{"id": 10, "title": "Egyedi méret"}]}
        self.assertEqual(_flat_size_options(data), [(10, "Egyedi méret")])

    def test_duplicate_values_are_collapsed(self):
        data = {"options": [
            {"id": 10, "title": "G", "options": [{"id": 1, "title": "M"}]},
            {"id": 11, "title": "G2", "options": [{"id": 1, "title": "M"}]},
        ]}
        self.assertEqual(_flat_size_options(data), [(1, "M")])


class FiltersQueryTests(unittest.TestCase):

    def test_every_common_facet_is_present_and_empty(self):
        q = _filters_query()
        for code in monitor._COMMON_FACETS:
            self.assertIn(f"attribute_ids%5B{code}%5D=", q)

    def test_catalog_ids_are_appended(self):
        q = _filters_query([1605, "12"])
        self.assertEqual(q.count("attribute_ids%5Bcatalog%5D="), 2)
        self.assertIn("attribute_ids%5Bcatalog%5D=1605", q)

    def test_extra_replaces_the_default_of_the_same_key(self):
        # a Vinted az ismétlődő kulcs első értékét olvassa, ezért a
        # `attribute_ids[color]`-nak csak egyszer szabad szerepelnie
        default = _filters_query()
        q = _filters_query([], **{"attribute_ids[color]": "7"})
        self.assertEqual(q.count("attribute_ids%5Bcolor%5D="), 1)
        self.assertIn("attribute_ids%5Bcolor%5D=7", q)
        self.assertIn("attribute_ids%5Bcolor%5D=", default)

    def test_extra_key_is_appended(self):
        q = _filters_query([], filter_code="size", search_text="kabát")
        self.assertIn("filter_code=size", q)
        self.assertIn("search_text=kab%C3%A1t", q)

    def test_empty_extra_values_are_ignored(self):
        self.assertEqual(_filters_query([], filter_code="", other=None),
                         _filters_query())


# ─── Beállítás-tároló ─────────────────────────────────────────────────────

class SettingsStoreTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(
            monitor, "SETTINGS_DB_PATH",
            os.path.join(self._tmp.name, "settings.db"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_key_returns_the_default(self):
        self.assertEqual(load_setting("nincs", "x"), "x")
        self.assertEqual(load_setting("nincs"), "")

    def test_roundtrip(self):
        self.assertTrue(save_setting("k", "érték"))
        self.assertEqual(load_setting("k"), "érték")

    def test_overwrite(self):
        save_setting("k", "1")
        save_setting("k", "2")
        self.assertEqual(load_setting("k"), "2")

    def test_none_is_stored_as_empty_string(self):
        save_setting("k", None)
        self.assertEqual(load_setting("k", "x"), "")

    def test_bool_helpers_accept_the_legacy_spellings(self):
        for truthy in ("1", "true", "True", "on", "igen", "YES", " 1 "):
            save_setting("b", truthy)
            self.assertTrue(load_bool("b"), truthy)
        for falsy in ("0", "false", "off", "no", "ban", ""):
            save_setting("b", falsy)
            self.assertFalse(load_bool("b"), falsy)

    def test_bool_default_is_used_for_a_missing_key(self):
        self.assertTrue(load_bool("nincs", True))
        self.assertFalse(load_bool("nincs"))

    def test_save_bool_roundtrip(self):
        save_bool("b", True)
        self.assertEqual(load_setting("b"), "1")
        save_bool("b", False)
        self.assertEqual(load_setting("b"), "0")


# ─── Adatbázis ────────────────────────────────────────────────────────────

class DatabaseTests(TempDbMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.conn = init_db()
        self.addCleanup(self.conn.close)

    def test_init_db_is_idempotent(self):
        other = init_db()
        self.addCleanup(other.close)
        self.assertEqual(count_listings(other), 0)

    def test_insert_reports_new_and_returns_no_previous_price(self):
        self.assertEqual(check_and_insert(self.conn, item()), (True, None))
        self.assertEqual(count_listings(self.conn), 1)

    def test_second_sight_reports_the_previous_price(self):
        check_and_insert(self.conn, item(price_num=2500.0))
        is_new, previous = check_and_insert(self.conn, item(price_num=2000.0))
        self.assertFalse(is_new)
        self.assertEqual(previous, 2500.0)
        self.assertEqual(count_listings(self.conn), 1)

    def test_repeat_sight_updates_price_but_keeps_the_first_one(self):
        check_and_insert(self.conn, item(price_num=2500.0))
        check_and_insert(self.conn, item(price_num=2000.0))
        row = self.conn.execute(
            "SELECT price_num, first_price, hits FROM listings").fetchone()
        self.assertEqual(row[0], 2000.0)
        self.assertEqual(row[1], 2500.0)
        self.assertEqual(row[2], 2)

    def test_missing_image_keeps_the_stored_one(self):
        check_and_insert(self.conn, item(image="a.jpg"))
        check_and_insert(self.conn, item(image=None, price_num=1.0))
        row = self.conn.execute("SELECT image FROM listings").fetchone()
        self.assertEqual(row[0], "a.jpg")

    def test_the_title_is_stored_as_given(self):
        # a trimmelés a `_normalize_item` dolga, a DB-réteg mindent eltárol,
        # amit kap — így egy kézzel betett tétel sem vész el
        check_and_insert(self.conn, item())
        row = self.conn.execute("SELECT title FROM listings").fetchone()
        self.assertEqual(row[0], " pulovka ")

    def test_incomplete_items_are_rejected(self):
        self.assertEqual(check_and_insert(self.conn, item(id="")), (False, None))
        self.assertEqual(check_and_insert(self.conn, item(title="")), (False, None))
        self.assertEqual(check_and_insert(self.conn, item(id=None)), (False, None))
        self.assertEqual(count_listings(self.conn), 0)

    def test_non_numeric_price_defaults_to_zero(self):
        check_and_insert(self.conn, item(price_num=None, favourites=None))
        row = self.conn.execute(
            "SELECT price_num, favourites FROM listings").fetchone()
        self.assertEqual(row[0], 0.0)
        self.assertEqual(row[1], 0)

    def test_fetch_recent_orders_by_last_seen(self):
        for i in (1, 2, 3):
            check_and_insert(self.conn, item(id=str(i), title=f"t{i}"))
            time.sleep(0.01)
        ids = [r["id"] for r in fetch_recent(self.conn)]
        self.assertEqual(ids, ["3", "2", "1"])

    def test_fetch_recent_respects_the_limit(self):
        for i in range(5):
            check_and_insert(self.conn, item(id=str(i), title=f"t{i}"))
        self.assertEqual(len(fetch_recent(self.conn, limit=2)), 2)

    def test_fetch_recent_can_filter_by_watch(self):
        check_and_insert(self.conn, item(id="1"), watch_name="A")
        check_and_insert(self.conn, item(id="2"), watch_name="B")
        rows = fetch_recent(self.conn, watch_name="A")
        self.assertEqual([r["id"] for r in rows], ["1"])
        self.assertEqual(rows[0]["watch"], "A")

    def test_fetch_recent_exposes_the_documented_keys(self):
        check_and_insert(self.conn, item())
        self.assertEqual(set(fetch_recent(self.conn)[0]),
                         {"id", "title", "price", "url", "image", "size",
                          "status", "watch", "first_seen", "last_seen", "hits"})

    def test_delete_db_file_removes_all_three_files(self):
        check_and_insert(self.conn, item())
        self.conn.close()
        # a WAL és a -shm fájl csak kérésre jön létre; itt a fő fájl a lényeg
        self.assertTrue(os.path.isfile(monitor.DB_PATH))
        self.assertTrue(monitor.delete_db_file())
        self.assertFalse(os.path.isfile(monitor.DB_PATH))

    def test_delete_db_file_on_a_missing_database_is_not_an_error(self):
        self.conn.close()
        monitor.delete_db_file()
        self.assertTrue(monitor.delete_db_file())


# ─── Egy ciklus futtatása ─────────────────────────────────────────────────

class RunCycleTests(TempDbMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.conn = init_db()
        self.addCleanup(self.conn.close)
        self.pages = {}

    def serve(self, mapping):
        """`fetch_page_items` helyett a megadott tételeket adja vissza."""
        def fake(url, wait=5):
            return list(mapping.get(url, [])), "teszt"
        return mock.patch.object(monitor, "fetch_page_items", fake)

    def watch(self, **over):
        kw = {"name": "W", "url": "https://x.hu/catalog", "pages": 1,
              "min_price": 0.0}
        kw.update(over)
        return Watch(**kw)

    def test_watch_without_url_fails_cleanly(self):
        result = run_cycle(self.conn, self.watch(url=""))
        self.assertEqual(result.errors, ["nincs cél-URL"])
        self.assertEqual(result.fetched, 0)

    def test_new_items_are_reported_once(self):
        url = "https://x.hu/catalog"
        with self.serve({url: [item(id="1", title="A"),
                               item(id="2", title="B")]}):
            result = run_cycle(self.conn, self.watch(url=url))
        self.assertEqual(result.new_count, 2)
        self.assertEqual([i["title"] for i in result.new_items], ["A", "B"])
        self.assertEqual(result.fetched, 2)
        self.assertEqual(result.errors, [])

        with self.serve({url: [item(id="1", title="A"),
                               item(id="2", title="B")]}):
            again = run_cycle(self.conn, self.watch(url=url))
        self.assertEqual(again.new_count, 0)
        self.assertEqual(count_listings(self.conn), 2)

    def test_duplicate_inside_one_page_is_ignored(self):
        url = "https://x.hu/catalog"
        with self.serve({url: [item(id="1", title="A"), item(id="1", title="A")]}):
            result = run_cycle(self.conn, self.watch(url=url))
        self.assertEqual(result.new_count, 1)
        self.assertEqual(result.fetched, 2)

    def test_min_price_filters_items_out(self):
        url = "https://x.hu/catalog"
        cheap = item(id="1", price_num=500.0)
        dear = item(id="2", price_num=5000.0)
        with self.serve({url: [cheap, dear]}):
            result = run_cycle(self.conn, self.watch(url=url, min_price=1000.0))
        self.assertEqual([i["id"] for i in result.new_items], ["2"])

    def test_min_price_zero_keeps_everything(self):
        url = "https://x.hu/catalog"
        with self.serve({url: [item(id="1", price_num=1.0)]}):
            result = run_cycle(self.conn, self.watch(url=url, min_price=0.0))
        self.assertEqual(result.new_count, 1)

    def test_price_drop_is_reported_with_both_prices(self):
        url = "https://x.hu/catalog"
        with self.serve({url: [item(id="1", price_num=5000.0)]}):
            run_cycle(self.conn, self.watch(url=url))
        with self.serve({url: [item(id="1", price_num=4000.0)]}):
            result = run_cycle(self.conn, self.watch(url=url))
        self.assertEqual(len(result.price_drops), 1)
        dropped, previous = result.price_drops[0]
        self.assertEqual((dropped["price_num"], previous), (4000.0, 5000.0))

    def test_a_rising_price_is_not_a_drop(self):
        url = "https://x.hu/catalog"
        with self.serve({url: [item(id="1", price_num=1000.0)]}):
            run_cycle(self.conn, self.watch(url=url))
        with self.serve({url: [item(id="1", price_num=1000.0)]}):
            result = run_cycle(self.conn, self.watch(url=url))
        self.assertEqual(result.price_drops, [])

    def test_one_cent_is_not_a_drop(self):
        url = "https://x.hu/catalog"
        with self.serve({url: [item(id="1", price_num=1000.0)]}):
            run_cycle(self.conn, self.watch(url=url))
        with self.serve({url: [item(id="1", price_num=999.99)]}):
            result = run_cycle(self.conn, self.watch(url=url))
        self.assertEqual(result.price_drops, [])

    def test_every_page_is_fetched_with_its_own_url(self):
        base = "https://x.hu/catalog?a=1"
        pages = {base: [item(id="1")],
                 base + "&page=2": [item(id="2")]}
        with self.serve(pages):
            result = run_cycle(self.conn, self.watch(url=base, pages=2))
        self.assertEqual(result.fetched, 2)
        self.assertEqual(result.new_count, 2)

    def test_a_failing_page_does_not_stop_the_others(self):
        base = "https://x.hu/catalog?a=1"
        calls = []

        def fake(url, wait=5):
            calls.append(url)
            if url.endswith("page=2"):
                raise RuntimeError("403")
            return [item(id="1")], "teszt"

        with mock.patch.object(monitor, "fetch_page_items", fake):
            result = run_cycle(self.conn, self.watch(url=base, pages=2))
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.new_count, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("403", result.errors[0])

    def test_on_new_is_called_for_every_new_item_only(self):
        url = "https://x.hu/catalog"
        seen = []
        with self.serve({url: [item(id="1", title="A"), item(id="2", title="B")]}):
            run_cycle(self.conn, self.watch(url=url),
                      on_new=lambda i, w: seen.append((i["title"], w.name)))
        self.assertEqual(seen, [("A", "W"), ("B", "W")])

        with self.serve({url: [item(id="1", title="A"), item(id="2", title="B")]}):
            run_cycle(self.conn, self.watch(url=url),
                      on_new=lambda i, w: seen.append((i["title"], w.name)))
        self.assertEqual(len(seen), 2)

    def test_a_broken_callback_does_not_break_the_cycle(self):
        url = "https://x.hu/catalog"
        with self.serve({url: [item(id="1")]}):
            result = run_cycle(self.conn, self.watch(url=url),
                               on_new=lambda i, w: 1 / 0)
        self.assertEqual(result.new_count, 1)

    def test_cycle_result_counts(self):
        result = CycleResult(watch="W")
        self.assertEqual(result.new_count, 0)
        result.new_items.append(item())
        self.assertEqual(result.new_count, 1)


# ─── A fő ciklus ──────────────────────────────────────────────────────────

class MainLoopTests(TempDbMixin, unittest.TestCase):
    """Egy-egy ciklus fut le tesztenként, a várakozás ki van kapcsolva."""

    def setUp(self):
        super().setUp()
        self.lines = []
        self.sent = []
        self.notes = []
        # a napló ne menjen ki a konzolra, hanem a sorba
        self.sink = mock.patch.object(
            monitor, "set_log_sink",
            side_effect=lambda fn=None: setattr(
                monitor, "_log_sink", self.lines.append if fn else None))
        self.sink.start()
        self.addCleanup(self.sink.stop)
        self.no_wait = mock.patch.object(monitor.random, "uniform",
                                         return_value=0.0)
        self.no_wait.start()
        self.addCleanup(self.no_wait.stop)

    def _sender(self, configured=True):
        sender = mock.MagicMock()
        sender.configured = configured
        sender.send.side_effect = lambda it: self.sent.append(it) or True
        sender.send_note.side_effect = \
            lambda t: self.notes.append(t) or True
        return sender

    def loop_once(self, watches, result_of, cycles=1, **kw):
        """`cycles` ciklust futtat, aztán leáll — a várakozás ki van kapcsolva.

        Az első ciklus után nem marad várakozó sor: a `Következő ciklus`
        üzenet csak akkor jelenik meg, ha a ciklus tényleg befejeződött.
        """
        stop = threading.Event()
        state = {"n": 0}

        def counting(_conn, watch, on_new=None):
            result = result_of(_conn, watch, on_new)
            state["n"] += 1
            if state["n"] >= cycles:
                stop.set()
            return result

        sender = self._sender(configured=bool(kw.get("webhook_url")))
        with mock.patch.object(monitor, "run_cycle", counting), \
                mock.patch.object(monitor, "DiscordSender",
                                  return_value=sender):
            monitor.main_loop(watches, log_func=self.lines.append,
                              stop_event=stop, **kw)
        self.assertEqual(state["n"], cycles)

    def fresh(self, **over):
        def result_of(_conn, watch, on_new=None):
            kw = {"watch": watch.name, "fetched": 1,
                  "new_items": [item(id=watch.name, title="x")]}
            kw.update(over)
            return CycleResult(**kw)
        return result_of

    def test_no_watches_is_an_error(self):
        for watches in ([], None, [Watch(name="W", url="")]):
            with self.assertRaises(ValueError):
                monitor.main_loop(watches, log_func=self.lines.append)

    def test_startup_summary_lists_every_watch(self):
        self.loop_once([Watch(name="A", url="u1"), Watch(name="B", url="u2")],
                       self.fresh())
        joined = "\n".join(self.lines)
        self.assertIn("2 figyelés indítva", joined)
        self.assertIn("A: 1 oldal", joined)
        self.assertIn("B: 1 oldal", joined)
        self.assertIn("desktop értesítés kikapcsolva", joined)

    def test_the_loop_runs_until_the_stop_event(self):
        self.loop_once([Watch(name="A", url="u1")], self.fresh())
        summary = [m for m in self.lines
                   if "új tétel" in m and "ellenőrizve" in m]
        self.assertEqual(len(summary), 1)
        self.assertIn("Leállva, adatbázis kapcsolat bezárva.", self.lines[-1])

    def test_two_cycles_run_when_the_event_is_late(self):
        self.loop_once([Watch(name="A", url="u1")], self.fresh(), cycles=2)
        summary = [m for m in self.lines
                   if "új tétel" in m and "ellenőrizve" in m]
        self.assertEqual(len(summary), 2)

    def test_the_next_cycle_is_announced_between_cycles(self):
        self.loop_once([Watch(name="A", url="u1")], self.fresh(), cycles=2)
        # a két ciklus között, nem pedig a leállás után
        announced = [i for i, m in enumerate(self.lines)
                     if "Következő ciklus" in m]
        self.assertEqual(len(announced), 1)
        self.assertLess(announced[0], len(self.lines) - 2)

    def test_no_waiting_line_after_the_stop(self):
        self.loop_once([Watch(name="A", url="u1")], self.fresh(), cycles=1)
        self.assertFalse([m for m in self.lines
                          if "Következő ciklus" in m])

    def test_a_quiet_watch_says_so(self):
        self.loop_once([Watch(name="A", url="u1")],
                       self.fresh(new_items=[]))
        self.assertTrue(any("nincs új tétel" in m for m in self.lines))

    def test_new_items_are_sent_when_discord_is_on(self):
        self.loop_once([Watch(name="A", url="u1", discord=True)],
                       self.fresh(), webhook_url="https://x")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.notes, [])

    def test_discord_off_means_no_sends(self):
        self.loop_once([Watch(name="A", url="u1", discord=False)],
                       self.fresh(), webhook_url="https://x")
        self.assertEqual(self.sent, [])

    def test_no_webhook_means_no_sends(self):
        self.loop_once([Watch(name="A", url="u1")], self.fresh(),
                       webhook_url="")
        self.assertEqual(self.sent, [])

    def test_errors_are_reported_per_watch(self):
        self.loop_once([Watch(name="A", url="u1")],
                       self.fresh(errors=["403", "timeout"], new_items=[]))
        reported = [m for m in self.lines if "[err] A:" in m]
        self.assertEqual(len(reported), 2)
        self.assertIn("403", reported[0])
        self.assertIn("timeout", reported[1])

    def test_price_drop_is_logged(self):
        self.loop_once(
            [Watch(name="A", url="u1")],
            self.fresh(new_items=[],
                       price_drops=[(item(price_num=800.0), 1200.0)]))
        self.assertTrue(any("ÁRCSÖKKENÉS [A]" in m and "1200" in m
                            and "800" in m for m in self.lines))

    def test_max_alerts_caps_the_sends_and_leaves_a_note(self):
        five = [item(id=str(i)) for i in range(5)]
        self.loop_once([Watch(name="A", url="u1")],
                       self.fresh(new_items=five, fetched=5),
                       webhook_url="https://x", max_alerts=2)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(len(self.notes), 1)
        self.assertIn("3 új tétel", self.notes[0])

    def test_a_long_tail_of_new_items_gets_a_summary_note(self):
        four = [item(id=str(i)) for i in range(4)]
        self.loop_once([Watch(name="A", url="u1")],
                       self.fresh(new_items=four, fetched=4),
                       webhook_url="https://x", max_alerts=15)
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(len(self.notes), 1)
        self.assertIn("Összesen 4 új tétel", self.notes[0])

    def test_a_few_new_items_need_no_note(self):
        self.loop_once([Watch(name="A", url="u1")],
                       self.fresh(new_items=[item(id=str(i)) for i in range(3)]),
                       webhook_url="https://x", max_alerts=15)
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.notes, [])

    def test_desktop_notify_only_for_watches_that_ask_for_it(self):
        calls = []

        def result_of(_conn, watch, on_new=None):
            if on_new:
                calls.append(watch.name)
            return CycleResult(watch=watch.name, fetched=0)

        self.loop_once([Watch(name="A", url="u1", desktop=True),
                        Watch(name="B", url="u2", desktop=False)],
                       result_of, desktop_notify=lambda i, w: None)
        self.assertEqual(calls, ["A"])


# ─── Discord ──────────────────────────────────────────────────────────────

class WebhookFormatTests(unittest.TestCase):

    def test_accepts_both_hosts(self):
        for url in ("https://discord.com/api/webhooks/1/abc",
                    "https://discordapp.com/api/webhooks/1/abc"):
            self.assertTrue(check_webhook_format(url)[0], url)

    def test_rejects_empty_and_foreign_urls(self):
        for url in ("", "   ", "https://example.com/hook", None):
            ok, msg = check_webhook_format(url)
            self.assertFalse(ok, url)
            self.assertTrue(msg)

    def test_rejects_a_url_without_an_id(self):
        ok, _ = check_webhook_format("https://discord.com/api/webhooks/1/")
        self.assertFalse(ok)

    def test_surrounding_whitespace_is_ignored(self):
        self.assertTrue(check_webhook_format(
            "  https://discord.com/api/webhooks/1/abc  ")[0])


class DiscordSenderTests(unittest.TestCase):

    def test_configured_needs_a_url(self):
        self.assertFalse(DiscordSender("").configured)
        self.assertFalse(DiscordSender("   ").configured)
        self.assertTrue(DiscordSender("https://discord.com/api/webhooks/1/a")
                        .configured)

    def test_embed_fields_reflect_the_item(self):
        embed = DiscordSender._embed(item(size="M", status="Jó",
                                          favourites=4))
        self.assertEqual(embed["fields"][0], {"name": "Ár", "value": "2500 HUF",
                                              "inline": True})
        self.assertEqual([f["name"] for f in embed["fields"]],
                         ["Ár", "Méret", "Állapot"])
        self.assertIn("4", embed["footer"]["text"])
        self.assertIn("Tétel #1", embed["footer"]["text"])

    def test_embed_falls_back_for_a_sparse_item(self):
        embed = DiscordSender._embed({"id": 7})
        self.assertEqual(embed["title"], "Vinted találat")
        self.assertEqual([f["name"] for f in embed["fields"]], ["Ár"])
        self.assertEqual(embed["fields"][0]["value"], "—")
        self.assertIn("Tétel #7", embed["footer"]["text"])

    def test_long_title_is_truncated(self):
        embed = DiscordSender._embed({"id": 1, "title": "x" * 400})
        self.assertLessEqual(len(embed["title"]), 256)

    def test_send_does_nothing_without_a_url(self):
        self.assertFalse(DiscordSender("").send(item()))
        self.assertFalse(DiscordSender("").send_note("hi"))

    def test_send_posts_the_embed(self):
        sender = DiscordSender("https://discord.com/api/webhooks/1/a")
        resp = mock.MagicMock(status_code=200, ok=True)
        with mock.patch.object(monitor.requests, "post",
                               return_value=resp) as post:
            self.assertTrue(sender.send(item()))
        self.assertIn("embeds", post.call_args.kwargs["json"])

    def test_send_note_posts_plain_content(self):
        sender = DiscordSender("https://discord.com/api/webhooks/1/a")
        resp = mock.MagicMock(status_code=204, ok=True)
        with mock.patch.object(monitor.requests, "post",
                               return_value=resp) as post:
            self.assertTrue(sender.send_note("összegzés"))
        self.assertEqual(post.call_args.kwargs["json"]["content"], "összegzés")

    def test_long_note_is_truncated(self):
        sender = DiscordSender("https://discord.com/api/webhooks/1/a")
        resp = mock.MagicMock(status_code=200, ok=True)
        with mock.patch.object(monitor.requests, "post", return_value=resp) as post:
            sender.send_note("y" * 5000)
        self.assertEqual(len(post.call_args.kwargs["json"]["content"]), 1900)

    def test_http_error_is_logged_not_raised(self):
        sender = DiscordSender("https://discord.com/api/webhooks/1/a")
        resp = mock.MagicMock(status_code=500, ok=False, text="boom")
        with mock.patch.object(monitor.requests, "post", return_value=resp):
            self.assertFalse(sender.send(item()))

    def test_network_error_is_logged_not_raised(self):
        sender = DiscordSender("https://discord.com/api/webhooks/1/a")
        err = monitor.requests.RequestException("nincs net")
        with mock.patch.object(monitor.requests, "post", side_effect=err):
            self.assertFalse(sender.send(item()))
            self.assertFalse(sender.send_note("x"))

    def test_rate_limit_is_retried_after_the_sleep(self):
        sender = DiscordSender("https://discord.com/api/webhooks/1/a")
        limited = mock.MagicMock(status_code=429, ok=False)
        limited.json.return_value = {"retry_after": 2.5}
        good = mock.MagicMock(status_code=200, ok=True)
        with mock.patch.object(monitor.requests, "post",
                               side_effect=[limited, good]) as post, \
                mock.patch.object(monitor.time, "sleep") as slept:
            self.assertTrue(sender.send(item()))
        self.assertEqual(post.call_count, 2)
        # a `retry_after` + 0.3 s biztonsági rés, a throttle külön tétel
        self.assertAlmostEqual(slept.call_args_list[0].args[0], 2.8, places=2)

    def test_rate_limit_sleep_is_capped(self):
        sender = DiscordSender("https://discord.com/api/webhooks/1/a")
        limited = mock.MagicMock(status_code=429, ok=False)
        limited.json.return_value = {"retry_after": 9999}
        good = mock.MagicMock(status_code=200, ok=True)
        with mock.patch.object(monitor.requests, "post",
                               side_effect=[limited, good]), \
                mock.patch.object(monitor.time, "sleep") as slept:
            sender.send(item())
        self.assertLessEqual(slept.call_args_list[0].args[0], 30.0)

    def test_unparsable_retry_after_falls_back_to_one_second(self):
        sender = DiscordSender("https://discord.com/api/webhooks/1/a")
        limited = mock.MagicMock(status_code=429, ok=False)
        limited.json.side_effect = ValueError
        good = mock.MagicMock(status_code=200, ok=True)
        with mock.patch.object(monitor.requests, "post",
                               side_effect=[limited, good]), \
                mock.patch.object(monitor.time, "sleep") as slept:
            sender.send(item())
        self.assertAlmostEqual(slept.call_args_list[0].args[0], 1.3, places=2)

    def test_the_first_send_waits_for_nothing(self):
        sender = DiscordSender("https://discord.com/api/webhooks/1/a",
                               min_interval=5.0)
        resp = mock.MagicMock(status_code=200, ok=True)
        with mock.patch.object(monitor.time, "sleep") as slept, \
                mock.patch.object(monitor.requests, "post", return_value=resp):
            sender.send(item())
        self.assertFalse(slept.called)

    def test_consecutive_sends_are_spaced_out(self):
        sender = DiscordSender("https://discord.com/api/webhooks/1/a",
                               min_interval=0.5)
        resp = mock.MagicMock(status_code=200, ok=True)
        with mock.patch.object(monitor.time, "sleep") as slept, \
                mock.patch.object(monitor.requests, "post",
                                  return_value=resp):
            sender.send(item())                 # az első soha nem vár
            self.assertFalse(slept.called)
            # a küldés után csak 0.1 s múlva jön a következő
            sender._last = time.time() - 0.1
            sender.send(item())
            self.assertEqual(len(slept.call_args_list), 1)
            self.assertAlmostEqual(slept.call_args_list[0].args[0], 0.4,
                                   delta=0.05)

    def test_enough_time_has_passed_means_no_wait(self):
        sender = DiscordSender("https://discord.com/api/webhooks/1/a",
                               min_interval=0.5)
        resp = mock.MagicMock(status_code=200, ok=True)
        with mock.patch.object(monitor.time, "sleep") as slept, \
                mock.patch.object(monitor.requests, "post",
                                  return_value=resp):
            sender._last = time.time() - 5.0
            sender.send(item())
        self.assertFalse(slept.called)

    def test_concurrent_sends_are_serialised(self):
        sender = DiscordSender("https://discord.com/api/webhooks/1/a")
        resp = mock.MagicMock(status_code=200, ok=True)
        with mock.patch.object(monitor.time, "sleep"), \
                mock.patch.object(monitor.requests, "post", return_value=resp):
            threads = [threading.Thread(target=sender.send, args=(item(),))
                       for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(5)
        self.assertTrue(all(not t.is_alive() for t in threads))


if __name__ == "__main__":
    unittest.main(verbosity=2)
