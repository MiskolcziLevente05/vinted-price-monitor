"""A `monitor_gui.py` felületének ellenőrzése valódi Tk ablakkal.

Futtatás:  `python -m unittest test_gui`

Hálózat, böngésző és a valódi `settings.db` nem kell hozzá: a hálózati
és adatbázis-hívásokat stubboljuk, az adatbázis-útvonalak pedig egy
ideiglenes könyvtárba mutatnak, így a teszt semmit nem ír a valódi
beállítások közé. A bukó tesztek nem-nulla kilépési kóddal jeleznek.
"""

import json
import os
import sys
import tempfile
import time
import tkinter as tk
import unittest
from tkinter import ttk
from unittest import mock

import monitor
import monitor_gui
from monitor_gui import (
    MIN_WATCH_AREA_H,
    THEMES,
    WATCH_HEAD_H,
    WATCH_ROW_H,
    VintedMonitorGUI,
)

sys.stdout.reconfigure(encoding="utf-8")

# az indulás valódi belépőpontja: a stub-ok előtt el kell menteni, különben
# a `setUp` már a helyettesítőt kapná vissza
_REAL_STARTUP_LOAD = VintedMonitorGUI._startup_load

# a hálózati és adatbázis-réteg teljes kikapcsolása a GUI-ban
_OFFLINE = ("fetch_category_tree", "fetch_category_filters", "fetch_brands",
            "test_webhook", "init_db", "fetch_recent", "count_listings",
            "main_loop", "delete_db_file")


def setUpModule():
    """A napló ne írjon ki a konzolra — a GUI a saját sorába veszi fel."""
    monitor.set_log_sink(lambda msg: None)


def tearDownModule():
    monitor.set_log_sink(None)


def watch(name, desc="", **params):
    """Egy figyelés a `settings.db`-ben tárolt alakban."""
    return {"name": name, "desc": desc, "params": dict(params),
            "url": monitor.build_search_url(dict(params)),
            "min_price": 0, "pages": 1, "discord": True, "desktop": False}


def canvas_height_option(scroll_frame):
    """A vászon magasság-opciója pixelben.

    A Tk az alapértelmezett értéket `7c` alakban adja vissza, ezért a
    centiméter-jelölget le kell venni, mielőtt szám lehet.
    """
    return int(float(str(scroll_frame.canvas.cget("height")).rstrip("c")))


def result(**over):
    item = {"id": "1", "title": "Kabát", "price": "2500 HUF",
            "url": "https://www.vinted.hu/items/1",
            "size": "M", "status": "Jó"}
    item.update(over)
    return item


class GuiTestCase(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for name in ("DB_PATH", "SETTINGS_DB_PATH"):
            patcher = mock.patch.object(
                monitor, name, os.path.join(self._tmp.name, name.lower()))
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in _OFFLINE:
            patcher = mock.patch.object(monitor_gui, name,
                                        return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)
        # az indulási betöltés külön tesztelendő, ne fusson minden alkalommal
        patcher = mock.patch.object(VintedMonitorGUI, "_startup_load",
                                    lambda self: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(monitor.set_log_sink, lambda msg: None)

        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        # átlátszó ablak: a mérésnek valódi geometria kell, de nem villanjon
        try:
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        self.gui = VintedMonitorGUI(self.root)
        self.root.update_idletasks()
        self.show("search")

    def _destroy(self):
        for widget in self.root.winfo_children():
            try:
                widget.destroy()
            except tk.TclError:
                pass
        try:
            self.gui._cancel_jobs()
        except Exception:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ── segédek ────────────────────────────────────────────────────────────

    def settle(self, times=3):
        for _ in range(times):
            self.root.update_idletasks()
            self.root.update()

    def wait_for(self, predicate, tries=80, pause=0.02):
        """Addig járja az eseményhurokot, amíg az állapot nem beáll.

        A munkaszálak nem szólnak nekünk, ezért a várakozást nem lehet
        `sleep` helyett csupán `update` hívásokkal megoldani.
        """
        for _ in range(tries):
            self.settle(1)
            if predicate():
                return True
            time.sleep(pause)
        return False

    def show(self, key):
        self.gui._show_view(key)
        self.settle()

    def resize(self, width, height):
        self.root.geometry(f"{width}x{height}")
        self.wait_for(lambda: (self.root.winfo_width(),
                               self.root.winfo_height()) == (width, height),
                      tries=25)

    def add_watches(self, count, start=1):
        self.gui.watches = [watch(f"Figyelés {i}", desc=f"{i}. leírás")
                            for i in range(start, start + count)]
        self.gui._render_watches()
        self.settle()

    def rows(self):
        return self.gui.watch_box.winfo_children()

    def labels(self, widget, cls=tk.Label):
        return [str(w.cget("text")) for w in self.widgets(widget, cls)]

    def widgets(self, widget, cls):
        """A `widget` alatti összes `cls` típusú leszármazott."""
        out = []

        def walk(w):
            for child in w.winfo_children():
                if isinstance(child, cls):
                    out.append(child)
                walk(child)
        walk(widget)
        return out

    def scroll_frames(self):
        return self.widgets(self.root, monitor_gui.ScrollFrame)

    def form_scroll(self):
        """Az űrlap görgetőkerete — a figyelés-lista másik `ScrollFrame`."""
        return next(sf for sf in self.scroll_frames()
                    if sf is not self.gui._watch_scroll)

    def text_of(self, widget):
        return str(widget.cget("text")) if widget.winfo_exists() else ""

    def log_text(self):
        return self.gui.log_text.get("1.0", tk.END)


# ─── Az ablak és a navigáció ──────────────────────────────────────────────

class ShellTests(GuiTestCase):

    def test_all_three_modules_exist(self):
        self.assertEqual([m.key for m in VintedMonitorGUI.MODULES],
                         ["search", "results", "log"])
        self.assertEqual(sorted(self.gui.modules), ["log", "results", "search"])

    def test_the_window_fits_on_the_screen(self):
        self.assertGreaterEqual(self.root.winfo_width(), 860)
        self.assertGreaterEqual(self.root.winfo_height(), 560)
        self.assertLessEqual(self.root.winfo_height(),
                             self.root.winfo_screenheight())
        self.assertLessEqual(self.root.winfo_width(),
                             self.root.winfo_screenwidth())

    def test_the_window_cannot_be_shrunk_past_the_useful_size(self):
        w, h = self.root.minsize()
        self.assertGreaterEqual(w, 860)
        self.assertGreaterEqual(h, 560)
        self.assertLessEqual(w, self.root.winfo_screenwidth())
        self.assertLessEqual(h, self.root.winfo_screenheight())

    def test_the_navigation_lists_every_module(self):
        self.assertEqual(sorted(self.gui._nav_items),
                         ["log", "results", "search"])
        for key, item in self.gui._nav_items.items():
            nav = next(m.nav for m in VintedMonitorGUI.MODULES if m.key == key)
            self.assertEqual(str(item.cget("text")), nav)

    def test_only_the_active_module_is_mapped(self):
        for key in ("results", "log", "search"):
            self.show(key)
            for name, module in self.gui.modules.items():
                self.assertEqual(bool(module.frame.winfo_ismapped()),
                                 name == key, name)

    def test_the_active_navigation_row_is_highlighted(self):
        self.show("log")
        active = [k for k, item in self.gui._nav_items.items()
                  if str(item.cget("bg")) == monitor_gui.SIDEBAR_ACTIVE]
        self.assertEqual(active, ["log"])

    def test_the_view_is_persisted_and_restored(self):
        self.show("results")
        self.assertEqual(monitor.load_setting(VintedMonitorGUI.VIEW_KEY),
                         "results")
        root = tk.Tk()
        self.addCleanup(root.destroy)
        gui = VintedMonitorGUI(root)
        self.addCleanup(gui._cancel_jobs)
        self.assertEqual(gui._view, "results")

    def test_an_unknown_saved_view_falls_back_to_search(self):
        monitor.save_setting(VintedMonitorGUI.VIEW_KEY, "nincs ilyen")
        root = tk.Tk()
        self.addCleanup(root.destroy)
        gui = VintedMonitorGUI(root)
        self.addCleanup(gui._cancel_jobs)
        self.assertEqual(gui._view, "search")

    def test_starting_without_a_watch_or_filter_is_refused(self):
        self.add_watches(0)
        self.gui.toggle_monitoring()
        self.settle()
        self.assertIsNone(self.gui.worker_thread)
        self.assertIn("Indítás", str(self.gui.start_btn.cget("text")))
        self.gui._poll_log_queue()
        self.settle()
        self.assertIn("Nincs szűrő kiválasztva", self.log_text())

    def test_starting_runs_the_monitor_and_comes_back(self):
        self.add_watches(1)
        self.show("search")
        self.gui.toggle_monitoring()
        self.assertEqual(self.gui._view, "results")
        self.assertIn("Leállítás", str(self.gui.start_btn.cget("text")))
        self.assertTrue(self.wait_for(
            lambda: self.gui.worker_thread is None))
        self.assertIn("Indítás", str(self.gui.start_btn.cget("text")))

    def test_a_second_press_stops_the_monitor(self):
        self.add_watches(1)

        def spin():
            while True:
                time.sleep(0.05)

        worker = mock.MagicMock()
        worker.is_alive.return_value = True
        self.gui.worker_thread = worker
        self.gui.start_btn.configure(text="Leállítás", state="normal")
        self.gui.toggle_monitoring()
        self.assertTrue(self.gui.stop_event.is_set())
        self.assertEqual(str(self.gui.start_btn.cget("state")), "disabled")
        # nem indít új szálat, csak a leállást kéri
        self.gui._poll_control()
        self.settle()
        self.assertIn("Leállítás", str(self.gui.start_btn.cget("text")))
        self.gui.stop_event.clear()
        self.gui.worker_thread = None

    def test_a_webhook_without_a_url_is_only_a_warning(self):
        self.add_watches(1)
        self.gui.webhook_var.set("")
        self.gui.discord_var.set(True)
        self.gui.toggle_monitoring()
        self.assertTrue(self.wait_for(
            lambda: self.gui.worker_thread is None))
        self.gui._poll_log_queue()
        self.settle()
        self.assertIn("nincs webhook URL", self.log_text())

    def test_a_wrong_webhook_is_reported_as_an_error(self):
        self.add_watches(1)
        self.gui.webhook_var.set("https://example.com/nem-webhook")
        self.gui.discord_var.set(True)
        self.gui.toggle_monitoring()
        self.assertTrue(self.wait_for(
            lambda: self.gui.worker_thread is None))
        self.gui._poll_log_queue()
        self.settle()
        self.assertIn("Discord:", self.log_text())
        self.assertIn("[err]", self.log_text())

    def test_the_settings_are_saved(self):
        self.gui.theme_var.set("modern")
        self.gui._save_settings()
        self.assertEqual(monitor.load_setting(
            VintedMonitorGUI.THEME_KEY), "modern")

    def test_a_new_result_updates_the_badge(self):
        self.gui._append_log("2026-01-01 10:00:00 NEW [Figyelés 1] Kabát × 2500")
        self.settle()
        self.assertEqual(str(self.gui.badge.cget("text")), "1 új találat")


# ─── A keresési nézet két blokkja ─────────────────────────────────────────

class SearchLayoutTests(GuiTestCase):

    def test_the_form_sits_directly_above_the_watch_list(self):
        form, watches = self.form_scroll(), self.gui._watch_scroll
        self.assertLessEqual(form.winfo_rooty() + form.winfo_height(),
                             watches.winfo_rooty() + 1)

    def test_both_blocks_have_room(self):
        form, watches = self.form_scroll(), self.gui._watch_scroll
        self.assertGreater(form.winfo_height(), 100)
        self.assertGreater(watches.winfo_height(), 100)

    def test_three_watches_fit_without_a_scrollbar(self):
        self.add_watches(3)
        self.gui._watch_scroll.refresh(settle=True)
        self.settle()
        sf = self.gui._watch_scroll
        self.assertEqual(sf.canvas.yview(), (0.0, 1.0))
        self.assertFalse(sf.vsb.winfo_manager())

    def test_three_watches_fit_at_the_smallest_window(self):
        self.resize(860, 560)
        self.add_watches(3)
        sf = self.gui._watch_scroll
        self.assertGreaterEqual(sf.canvas.winfo_height(), 3 * WATCH_ROW_H)

    def test_the_constants_match_the_rendered_rows(self):
        # ha a blokk felépítése változik, a konstansok is azok
        self.add_watches(3)
        sf = self.gui._watch_scroll
        overhead = sf.master.winfo_height() - sf.winfo_height()
        self.assertEqual(overhead, WATCH_HEAD_H)
        self.assertEqual(sf.inner.winfo_reqheight(), 3 * WATCH_ROW_H)
        self.assertEqual(MIN_WATCH_AREA_H, 3 * WATCH_ROW_H + WATCH_HEAD_H)

    def test_many_watches_get_a_scrollbar(self):
        self.add_watches(40)
        sf = self.gui._watch_scroll
        self.assertTrue(sf.vsb.winfo_manager())
        self.assertLess(sf.canvas.yview()[1], 0.2)

    def test_the_scrollbar_is_on_the_right_of_the_frame(self):
        self.add_watches(40)
        sf = self.gui._watch_scroll
        self.assertGreaterEqual(sf.vsb.winfo_rootx(),
                                sf.canvas.winfo_rootx() +
                                sf.canvas.winfo_width() - 24)

    def test_the_end_of_the_list_is_reachable(self):
        self.add_watches(40)
        sf = self.gui._watch_scroll
        sf.canvas.yview_moveto(1.0)
        self.settle()
        first, last = sf.canvas.yview()
        self.assertAlmostEqual(last, 1.0, places=3)
        self.assertGreaterEqual(first, 0.9)
        # az utolsó sor teljesen a nézetben van
        canvas_bottom = sf.canvas.winfo_rooty() + sf.canvas.winfo_height()
        last_row = self.rows()[-1]
        self.assertLessEqual(last_row.winfo_rooty() + last_row.winfo_height(),
                             canvas_bottom + 2)

    def test_the_first_row_is_visible_at_the_top_of_the_range(self):
        self.add_watches(40)
        sf = self.gui._watch_scroll
        canvas_top = sf.canvas.winfo_rooty()
        first_row = self.rows()[0]
        self.assertGreaterEqual(first_row.winfo_rooty(), canvas_top - 2)
        # a lista aljára görgetve az első sor kigörgött a nézetből
        sf.canvas.yview_moveto(1.0)
        self.settle()
        self.assertGreater(sf.canvas.yview()[0], 0.9)

    def test_scrolling_the_list_does_not_move_the_form(self):
        self.add_watches(40)
        form = self.form_scroll()
        before = (form.winfo_rooty(), form.inner.winfo_rooty())
        self.gui._watch_scroll.canvas.yview_moveto(0.7)
        self.settle()
        self.assertEqual((form.winfo_rooty(), form.inner.winfo_rooty()),
                         before)

    def test_deleting_every_watch_hides_the_scrollbar(self):
        self.add_watches(40)
        self.assertTrue(self.gui._watch_scroll.vsb.winfo_manager())
        self.gui.watches = []
        self.gui._render_watches()
        self.settle()
        self.assertFalse(self.gui._watch_scroll.vsb.winfo_manager())
        self.assertEqual(self.gui._watch_scroll.canvas.yview(), (0.0, 1.0))

    def test_deleting_one_watch_shrinks_the_content(self):
        self.add_watches(20)
        sf = self.gui._watch_scroll
        before = sf.canvas.bbox("all")[3]
        del self.gui.watches[-1]
        self.gui._render_watches()
        self.settle()
        self.assertLess(sf.canvas.bbox("all")[3], before)
        # a tartalom újramérése után a sáv sem ragad be
        sf.canvas.yview_moveto(1.0)
        self.settle()
        self.assertAlmostEqual(sf.canvas.yview()[1], 1.0, places=3)

    def test_the_form_keeps_its_natural_height(self):
        form = self.form_scroll()
        self.assertTrue(form.fit_height)
        self.settle()
        self.assertEqual(canvas_height_option(form),
                         form.inner.winfo_reqheight())

    def test_the_form_does_not_scroll_when_it_fits(self):
        self.resize(1280, 900)
        self.add_watches(3)
        form = self.form_scroll()
        self.assertEqual(form.canvas.yview(), (0.0, 1.0))
        self.assertFalse(form.vsb.winfo_manager())

    def test_the_form_scrolls_when_it_cannot_fit(self):
        # a `fit_height` viselkedés önmagában: a tartalommagasságot kapó
        # vászon szűk helyen görget, sávot mutat, és nem vág le semmit
        host = tk.Frame(self.root, bg=monitor_gui.CARD)
        host.place(x=0, y=0, width=300, height=200)
        self.addCleanup(host.destroy)
        sf = monitor_gui.ScrollFrame(host)
        sf.fit_height = True
        sf.pack(fill=tk.BOTH, expand=True)
        for i in range(20):
            tk.Label(sf.inner, text=f"mező {i}", bg=monitor_gui.CARD).pack(
                fill=tk.X)
        self.settle(6)
        self.assertEqual(int(sf.canvas.cget("height")),
                         sf.inner.winfo_reqheight())
        self.assertLess(sf.canvas.winfo_height(), sf.inner.winfo_reqheight())
        sf.refresh(settle=True)
        self.settle(6)
        self.assertLess(sf.canvas.yview()[1], 1.0)
        self.assertTrue(sf.vsb.winfo_manager())
        sf.canvas.yview_moveto(1.0)
        self.settle()
        self.assertAlmostEqual(sf.canvas.yview()[1], 1.0, places=3)

    def test_a_growing_content_makes_the_canvas_grow(self):
        # a `fit_height` lényege: a vászon a tartalom magasságát kapja,
        # tehát az űrlap nő, ahogy tartalom kerül bele
        host = tk.Frame(self.root, bg=monitor_gui.CARD)
        host.place(x=0, y=0, width=300, height=600)
        self.addCleanup(host.destroy)
        sf = monitor_gui.ScrollFrame(host)
        sf.fit_height = True
        sf.pack(fill=tk.BOTH, expand=True)
        self.settle(4)
        empty = canvas_height_option(sf)
        for i in range(20):
            tk.Label(sf.inner, text=f"mező {i}", bg=monitor_gui.CARD).pack(
                fill=tk.X)
        self.settle(6)
        self.assertGreater(canvas_height_option(sf), empty)
        self.assertEqual(canvas_height_option(sf),
                         sf.inner.winfo_reqheight())
        self.assertFalse(sf.vsb.winfo_manager())

    def test_the_watch_area_keeps_its_minimum_at_the_smallest_window(self):
        self.resize(860, 560)
        self.add_watches(40)
        self.assertGreaterEqual(self.gui._watch_scroll.master.winfo_height(),
                                MIN_WATCH_AREA_H)

    def test_a_wider_window_keeps_the_form_the_same(self):
        self.add_watches(3)
        before = self.form_scroll().winfo_height()
        self.resize(1600, 900)
        self.settle(4)
        self.assertEqual(self.form_scroll().winfo_height(), before)

    def test_the_narrowest_window_keeps_the_form_readable(self):
        self.resize(860, 560)
        self.add_watches(3)
        form, watches = self.form_scroll(), self.gui._watch_scroll
        self.assertGreaterEqual(form.winfo_height(), 100)
        self.assertLessEqual(form.winfo_rooty() + form.winfo_height(),
                             watches.winfo_rooty() + 1)


# ─── A figyelések listája ─────────────────────────────────────────────────

class WatchListTests(GuiTestCase):

    def test_one_row_per_watch(self):
        self.add_watches(3)
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(self.text_of(self.rows()[0].winfo_children()[0]
                                      .winfo_children()[0]), "Figyelés 1")

    def test_the_counter_follows_the_list(self):
        self.add_watches(2)
        self.assertEqual(str(self.gui.watch_count.cget("text")), "(2)")
        self.add_watches(0)
        self.assertEqual(str(self.gui.watch_count.cget("text")), "(0)")

    def test_the_description_is_shown(self):
        self.add_watches(2)
        text = " ".join(self.labels(self.gui.watch_box))
        self.assertIn("1. leírás", text)
        self.assertIn("2. leírás", text)

    def test_the_details_say_pages_and_the_local_minimum(self):
        self.add_watches(1)
        self.gui.watches[0].update(pages=3, min_price=1500.0, discord=False)
        self.gui._render_watches()
        self.settle()
        text = " ".join(self.labels(self.rows()[0]))
        self.assertIn("3 oldal", text)
        self.assertIn("min. 1500 Ft", text)
        self.assertIn("discord: ki", text)

    def test_a_zero_minimum_is_left_out(self):
        self.add_watches(1)
        self.gui.watches[0].update(min_price=0.0)
        self.gui._render_watches()
        self.settle()
        # a nulla Ft nem hírértékű, ezért nem kerül a sorra
        self.assertNotIn("0 Ft", " ".join(self.labels(self.rows()[0])))

    def test_an_empty_list_explains_itself(self):
        self.add_watches(0)
        self.assertIn("Még nincs figyelés",
                      " ".join(self.labels(self.gui.watch_box)))

    def test_a_watch_without_a_description_falls_back_to_the_filters(self):
        self.gui.watches = [watch("W", search_text="kabát",
                                  price_from="1000", price_to="5000",
                                  discount=True)]
        self.gui._render_watches()
        self.settle()
        text = " ".join(self.labels(self.rows()[0]))
        self.assertIn("kabát", text)
        self.assertIn("1 000–5 000 Ft", text)
        self.assertIn("kedvezmény", text)

    def test_a_watch_without_filters_says_so(self):
        self.gui.watches = [watch("W")]
        self.gui._render_watches()
        self.settle()
        self.assertIn("nem szűrt keresés",
                      " ".join(self.labels(self.rows()[0])))

    def test_the_new_watch_link_lives_in_the_list_header(self):
        module = self.gui.modules["search"]
        link = next(w for w in self.widgets(module.inner, ttk.Button)
                    if "Új figyelés" in str(w.cget("text")))
        # a lista címsorában van, a modul fejlécében nincs
        head = module.inner.winfo_children()[0]
        self.assertNotIn(link, self.widgets(head, ttk.Button))
        self.assertIn("Figyelések", self.labels(link.master, ttk.Label)
                      + self.labels(link.master))

    def test_the_new_watch_link_saves_a_watch(self):
        self.gui.search_text_var.set("kabát")
        self.gui.min_price_var.set("1000")
        self.add_watches(0)
        self.gui.add_watch()               # a név/leírás dialógus nyílik meg
        self.settle()
        dialog = next(w for w in self.root.winfo_children()
                      if isinstance(w, monitor_gui.WatchDialog))
        dialog.name_var.set("Saját pulóver")
        dialog.desc_var.set("téli")
        dialog._save()
        self.settle()
        self.assertEqual(len(self.gui.watches), 1)
        saved = self.gui.watches[0]
        self.assertEqual(saved["name"], "Saját pulóver")
        self.assertEqual(saved["desc"], "téli")
        self.assertIn("search_text=kab%C3%A1t", saved["url"])
        self.assertEqual(saved["min_price"], 1000.0)
        self.assertEqual(saved["pages"], 1)
        # a dialógus bezárult
        self.assertFalse(dialog.winfo_exists())

    def test_an_empty_form_warns_instead_of_opening_a_dialog(self):
        self.add_watches(0)
        self.gui.add_watch()
        self.settle()
        self.assertEqual(self.widgets(self.root, monitor_gui.WatchDialog), [])
        self.assertEqual(self.gui.watches, [])
        self.gui._poll_log_queue()
        self.settle()
        self.assertIn("Nincs szűrő", self.log_text())

    def test_a_blank_name_falls_back_to_the_next_free_number(self):
        self.add_watches(1)
        self.gui.search_text_var.set("kabát")
        self.gui.add_watch()
        self.settle()
        dialog = next(w for w in self.root.winfo_children()
                      if isinstance(w, monitor_gui.WatchDialog))
        dialog.name_var.set("   ")
        dialog._save()
        self.settle()
        self.assertEqual(self.gui.watches[-1]["name"], "Figyelés 2")

    def test_cancelling_the_new_watch_dialog_saves_nothing(self):
        self.add_watches(1)
        self.gui.search_text_var.set("kabát")
        self.gui.add_watch()
        self.settle()
        dialog = next(w for w in self.root.winfo_children()
                      if isinstance(w, monitor_gui.WatchDialog))
        dialog._cancel()
        self.settle()
        self.assertEqual(len(self.gui.watches), 1)

    def test_the_watch_is_written_to_the_settings(self):
        self.add_watches(2)
        self.gui._save_watches()
        self.assertEqual([w["name"] for w in self.gui._load_watches()],
                         ["Figyelés 1", "Figyelés 2"])

    def test_names_do_not_collide_after_a_deletion(self):
        self.add_watches(3)
        del self.gui.watches[1]            # a középső törlődik
        self.assertEqual(self.gui._next_watch_name(), "Figyelés 2")
        del self.gui.watches[0]
        self.assertEqual(self.gui._next_watch_name(), "Figyelés 1")

    def test_a_renamed_watch_frees_its_number(self):
        self.add_watches(2)
        self.gui.watches[1]["name"] = "Saját név"
        self.assertEqual(self.gui._next_watch_name(), "Figyelés 2")

    def test_a_custom_name_does_not_stop_the_numbering(self):
        self.add_watches(1)
        self.gui.watches[0]["name"] = "Saját név"
        self.assertEqual(self.gui._next_watch_name(), "Figyelés 1")

    def test_deleting_a_watch_persists_and_reindexes_the_editor(self):
        self.add_watches(3)
        self.gui._editing = 2
        self.gui.remove_watch(0)
        self.settle()
        self.assertEqual([w["name"] for w in self.gui.watches],
                         ["Figyelés 2", "Figyelés 3"])
        self.assertEqual(self.gui._editing, 1)
        self.assertEqual([w["name"] for w in self.gui._load_watches()],
                         ["Figyelés 2", "Figyelés 3"])

    def test_deleting_an_unknown_row_is_ignored(self):
        self.add_watches(1)
        self.gui.remove_watch(7)
        self.gui.remove_watch(-1)
        self.assertEqual(len(self.gui.watches), 1)

    def test_deleting_is_blocked_while_the_monitor_runs(self):
        self.add_watches(2)
        self.gui.worker_thread = mock.MagicMock()
        self.gui.worker_thread.is_alive.return_value = True
        with mock.patch.object(monitor_gui, "messagebox") as box:
            self.gui.remove_watch(0)
        box.showwarning.assert_called_once()
        self.assertEqual(len(self.gui.watches), 2)
        self.gui.worker_thread = None

    def test_editing_loads_the_filters_into_the_form(self):
        self.gui.watches = [watch("W", search_text="kabát", color=["7"],
                                  discount=True, give_away=True)]
        self.gui.watches[0].update(pages=4, min_price=2000.0)
        self.gui.filter_options = {"color": [(7, "Piros")]}
        self.gui.facet_titles = {"color": "Szín"}
        self.gui.facet_codes = ["color"]
        self.gui._load_params_into_form(self.gui.watches[0]["params"])
        self.settle()
        self.assertEqual(self.gui.search_text_var.get(), "kabát")
        self.assertTrue(self.gui.discount_var.get())
        self.assertTrue(self.gui.give_away_var.get())
        self.assertEqual(self.gui.selected["color"], ["7"])
        self.gui.min_price_var.set("2000")
        self.gui.pages_var.set(4)
        self.gui.edit_watch(0)
        self.settle()
        dialog = next(w for w in self.root.winfo_children()
                      if isinstance(w, monitor_gui.WatchDialog))
        self.assertEqual(dialog.name_var.get(), "W")
        self.assertEqual(self.gui._editing, 0)
        dialog._save()
        self.settle()
        self.assertEqual(self.gui.watches[0]["pages"], 4)
        self.assertEqual(self.gui.watches[0]["min_price"], 2000.0)
        self.assertIsNone(self.gui._editing)

    def test_editing_keeps_the_other_watches_untouched(self):
        self.add_watches(3)
        self.gui.watches[1]["desc"] = "régi"
        self.gui.search_text_var.set("kabát")
        self.gui.edit_watch(1)
        self.settle()
        dialog = next(w for w in self.root.winfo_children()
                      if isinstance(w, monitor_gui.WatchDialog))
        dialog.desc_var.set("új")
        dialog._save()
        self.settle()
        self.assertEqual(len(self.gui.watches), 3)
        self.assertEqual(self.gui.watches[1]["desc"], "új")
        self.assertEqual(self.gui.watches[2]["desc"], "3. leírás")

    def test_the_edited_row_is_highlighted_while_editing(self):
        self.add_watches(2)
        self.gui.search_text_var.set("kabát")
        self.gui.edit_watch(0)
        self.settle()
        self.assertEqual(str(self.rows()[0].cget("bg")), monitor_gui.ACCENT_LT)
        self.assertEqual(str(self.rows()[1].cget("bg")), monitor_gui.CARD)
        next(w for w in self.root.winfo_children()
             if isinstance(w, monitor_gui.WatchDialog))._cancel()
        self.settle()

    def test_cancelling_the_editor_clears_the_highlight(self):
        self.add_watches(1)
        self.gui._editing = 0
        self.gui._render_watches()
        self.gui._cancel_edit()
        self.settle()
        self.assertIsNone(self.gui._editing)
        self.assertEqual(str(self.rows()[0].cget("bg")), monitor_gui.CARD)

    def test_the_row_menu_offers_edit_and_remove(self):
        self.add_watches(1)
        shown = {}

        def grab(_menu, *_args):
            shown["called"] = True

        with mock.patch.object(tk.Menu, "tk_popup", grab), \
                mock.patch.object(tk.Menu, "grab_release"):
            self.gui._watch_menu(mock.Mock(x_root=10, y_root=10), 0)
        self.assertTrue(shown.get("called"))

    def test_the_menu_lists_only_the_two_actions(self):
        self.add_watches(1)
        seen = {}

        def grab(menu, *_args):
            seen["bg"] = str(menu.cget("bg"))
            seen["entries"] = [menu.entrycget(i, "label")
                               for i in range(menu.index("end") + 1)
                               if menu.type(i) == "command"]

        with mock.patch.object(tk.Menu, "tk_popup", grab), \
                mock.patch.object(tk.Menu, "grab_release"):
            self.gui._watch_menu(mock.Mock(x_root=10, y_root=10), 0)
        self.assertEqual(seen["bg"], monitor_gui.CARD)
        self.assertEqual(seen["entries"], ["Szerkesztés…", "Törlés"])

    def test_the_menu_of_the_last_row_is_not_clipped(self):
        # a 40. sor menüje is a 40. figyelésre mutasson (a sorszám a
        # görgetés után sem csúszik el)
        self.add_watches(40)
        seen = {}

        def grab(menu, *_args):
            seen["labels"] = [menu.entrycget(i, "label")
                              for i in range(menu.index("end") + 1)
                              if menu.type(i) == "command"]

        with mock.patch.object(tk.Menu, "tk_popup", grab), \
                mock.patch.object(tk.Menu, "grab_release"):
            self.gui._watch_menu(mock.Mock(x_root=10, y_root=10), 39)
        self.assertEqual(seen["labels"], ["Szerkesztés…", "Törlés"])
        self.assertEqual(len(self.rows()), 40)

    def test_a_row_reacts_to_the_mouse(self):
        self.add_watches(1)
        row = self.rows()[0]
        self.assertTrue(row.bind("<Double-Button-1>"))
        self.assertTrue(row.bind("<Button-3>"))


# ─── A szűrő-űrlap ────────────────────────────────────────────────────────

class FormTests(GuiTestCase):

    def test_the_hint_appears_only_when_the_order_hides_new_items(self):
        self.assertEqual(self.gui.order_var.get(), "Relevancia")
        self.assertTrue(self.gui.order_hint.winfo_manager())
        self.gui.order_var.set("Legújabb")
        self.settle()
        self.assertFalse(self.gui.order_hint.winfo_manager())
        self.gui.order_var.set("Ár növekvő")
        self.settle()
        self.assertTrue(self.gui.order_hint.winfo_manager())

    def test_the_hint_rewraps_to_the_available_width(self):
        widths = []
        for width in (1280, 1000, 880):
            self.resize(width, 800)
            self.gui._check_order_hint()
            widths.append(int(self.gui.order_hint.cget("wraplength")))
        self.assertEqual(widths, sorted(widths, reverse=True))
        self.assertLess(widths[-1], widths[0])

    def test_the_hint_sits_between_the_form_and_the_filters(self):
        self.assertLess(self.gui.order_hint.winfo_rooty(),
                        self.gui.chips.master.winfo_rooty())

    def test_the_clear_button_follows_the_filters(self):
        self.assertEqual(str(self.gui.clear_btn.cget("state")), "disabled")
        self.gui.search_text_var.set("kabát")
        self.settle()
        self.assertEqual(str(self.gui.clear_btn.cget("state")), "normal")
        self.gui.clear_filters()
        self.settle()
        self.assertEqual(str(self.gui.clear_btn.cget("state")), "disabled")
        self.assertEqual(self.gui.search_text_var.get(), "")

    def test_clearing_removes_the_selected_facets_too(self):
        self.gui.selected = {"catalog": ["1605"], "color": ["7"]}
        self.gui.filter_options = {"color": [(7, "Piros")]}
        self.gui.facet_titles = {"color": "Szín"}
        self.gui.facet_codes = ["color"]
        self.gui._rebuild_chips()
        self.gui._update_clear_btn()
        self.settle()
        self.assertEqual(str(self.gui.clear_btn.cget("state")), "normal")
        self.gui.clear_filters()
        self.settle()
        self.assertEqual(self.gui.selected, {"catalog": []})
        self.assertEqual(self.gui.filter_options, {})
        self.assertEqual([str(w.cget("text"))
                          for w in self.gui.chips.winfo_children()],
                         ["Kategóriák"])

    def test_the_default_order_is_not_part_of_the_url(self):
        self.gui.order_var.set("Relevancia")
        self.assertNotIn("order", self.gui._collect_params())
        self.gui.order_var.set("Ár csökkenő")
        self.assertEqual(self.gui._collect_params()["order"],
                         "price_high_to_low")

    def test_the_collected_parameters_reach_the_url(self):
        self.gui.search_text_var.set("kabát")
        self.gui.price_from_var.set("1000")
        self.gui.price_to_var.set("9000")
        self.gui.order_var.set("Legújabb")
        self.gui.discount_var.set(True)
        self.gui.give_away_var.set(True)
        self.gui.selected = {"catalog": ["1605"], "color": ["7", "8"]}
        url = monitor.build_search_url(self.gui._collect_params())
        self.assertIn("search_text=kab%C3%A1t", url)
        self.assertIn("price_from=1000", url)
        self.assertIn("price_to=9000", url)
        self.assertIn("order=newest_first", url)
        self.assertIn("catalog%5B%5D=1605", url)
        self.assertIn("color_ids%5B%5D=7", url)
        self.assertIn("color_ids%5B%5D=8", url)
        self.assertIn("discount_ids%5B%5D=4", url)
        self.assertIn("is_for_give_away=true", url)

    def test_an_unreadable_minimum_price_falls_back_to_zero(self):
        for raw, expected in (("", 0.0), ("abc", 0.0), ("-5", 0.0),
                              ("1 500", 1500.0), ("1,5", 1.5), ("0", 0.0)):
            self.gui.min_price_var.set(raw)
            self.assertEqual(self.gui._read_min_price(), expected, raw)

    def test_the_page_count_is_capped(self):
        for raw, expected in (("", 1), ("0", 1), ("3", 3), ("99", 10)):
            self.gui.pages_var.set(raw)
            self.assertEqual(self.gui._read_pages(), expected, raw)
        self.gui.pages_var.set("x")
        self.assertEqual(self.gui._read_pages(), 1)

    def test_the_chips_start_with_the_category_only(self):
        self.gui._rebuild_chips()
        self.settle()
        texts = [str(w.cget("text")) for w in self.gui.chips.winfo_children()]
        self.assertEqual(texts, ["Kategóriák"])

    def test_a_selected_option_shows_on_the_chip(self):
        self.gui.category_tree = [{"id": 1605, "title": "Kabátok",
                                   "catalogs": []}]
        self.gui._catalog_lookup = None
        self.gui.filter_options = {"color": [(7, "Piros"), (8, "Kék")]}
        self.gui.facet_titles = {"color": "Szín"}
        self.gui.facet_codes = ["color"]
        self.gui.selected = {"catalog": ["1605"], "color": ["7"]}
        self.gui._rebuild_chips()
        self.gui._update_filter_button("catalog")
        self.gui._update_filter_button("color")
        self.settle()
        labels = {str(w.cget("text")) for w in self.gui.chips.winfo_children()}
        self.assertIn("Kabátok", labels)
        self.assertIn("Piros", labels)

    def test_an_unnamed_selection_shows_a_count_not_an_id(self):
        # a kategóriafa még nem töltődött, a nevét nem tudjuk — de a
        # nyers azonosító ("1605") semmit nem mond a felhasználónak
        self.gui.selected = {"catalog": ["1605"], "color": ["7", "8"]}
        self.gui.filter_options = {"color": []}
        self.gui.facet_codes = []
        self.gui._rebuild_chips()
        self.gui._update_filter_button("catalog")
        self.settle()
        self.assertEqual(str(self.gui._filter_buttons["catalog"].cget("text")),
                         "Kategóriák (1)")

    def test_many_selected_options_are_shortened(self):
        self.gui.filter_options = {"color": [(1, "Egy"), (2, "Kettő"),
                                             (3, "Három")]}
        self.gui.facet_titles = {"color": "Szín"}
        self.gui.facet_codes = ["color"]
        self.gui.selected = {"catalog": [], "color": ["1", "2", "3"]}
        self.gui._rebuild_chips()
        self.gui._update_filter_button("color")
        self.settle()
        labels = [str(w.cget("text")) for w in self.gui.chips.winfo_children()]
        self.assertIn("Egy + Kettő … +1", labels)

    def test_the_status_facet_has_options_without_a_download(self):
        self.gui.facet_codes = ["status"]
        self.gui.facet_titles = {"status": "Állapot"}
        self.gui._rebuild_chips()
        self.gui.selected["status"] = ["3"]
        self.gui._update_filter_button("status")
        self.settle()
        labels = [str(w.cget("text")) for w in self.gui.chips.winfo_children()]
        self.assertIn("Jó", labels)

    def test_a_facet_without_options_gets_no_chip(self):
        # a `brand` kivétel: annak van Vinted-keresője
        self.gui.facet_codes = ["brand", "color", "material"]
        self.gui.filter_options = {"color": [(7, "Piros")]}
        self.gui.facet_titles = {"color": "Szín"}
        self.gui._rebuild_chips()
        texts = [str(w.cget("text")) for w in self.gui.chips.winfo_children()]
        self.assertEqual(texts, ["Kategóriák", "brand", "Szín"])

    def test_the_category_popup_asks_for_a_tree_first(self):
        with mock.patch.object(monitor_gui, "messagebox") as box, \
                mock.patch.object(monitor_gui, "CategoryTreePopup") as popup:
            self.gui._open_filter_popup("catalog")
        box.showinfo.assert_called_once()
        self.assertFalse(popup.called)

    def test_a_chosen_category_is_applied(self):
        self.gui.category_tree = [{"id": 1, "title": "Női",
                                   "catalogs": [{"id": 10, "title": "Ruha",
                                                 "url": "/catalog/1605"}]}]
        self.gui.selected["catalog"] = ["1"]
        popup = mock.MagicMock(result=["10"], catalog_url="/catalog/1605")
        with mock.patch.object(monitor_gui, "CategoryTreePopup",
                               return_value=popup), \
                mock.patch.object(tk.Misc, "wait_window"), \
                mock.patch.object(self.gui, "_on_catalog_selected") as apply:
            self.gui._open_filter_popup("catalog")
        self.assertEqual(self.gui.selected["catalog"], ["10"])
        apply.assert_called_once_with("/catalog/1605")

    def test_a_cancelled_category_popup_changes_nothing(self):
        self.gui.category_tree = [{"id": 1, "title": "Női", "url": "/c/1"}]
        popup = mock.MagicMock(result=None, catalog_url=None)
        with mock.patch.object(monitor_gui, "CategoryTreePopup",
                               return_value=popup), \
                mock.patch.object(tk.Misc, "wait_window"), \
                mock.patch.object(self.gui, "_on_catalog_selected") as apply:
            self.gui._open_filter_popup("catalog")
        self.assertFalse(apply.called)

    def test_a_facet_popup_applies_the_result(self):
        self.gui.filter_options = {"color": [(7, "Piros"), (8, "Kék")]}
        self.gui.facet_titles = {"color": "Szín"}
        self.gui.facet_codes = ["color"]
        self.gui._rebuild_chips()
        popup = mock.MagicMock(result=["8"], found_pairs=[])
        with mock.patch.object(monitor_gui, "MultiSelectPopup",
                               return_value=popup) as ctor, \
                mock.patch.object(tk.Misc, "wait_window"):
            self.gui._open_filter_popup("color")
        self.assertEqual(ctor.call_args.args[3], [])       # korábbi kiválasztás
        # csak a `brand` szűrő tud Vintedről keresni
        self.assertIsNone(ctor.call_args.kwargs["on_search"])
        self.assertEqual(self.gui.selected["color"], ["8"])
        self.assertEqual(str(self.gui._filter_buttons["color"].cget("text")),
                         "Kék")

    def test_the_brand_filter_searches_vinted(self):
        self.gui.facet_codes = ["brand"]
        self.gui._rebuild_chips()
        popup = mock.MagicMock(result=[], found_pairs=[])
        with mock.patch.object(monitor_gui, "MultiSelectPopup",
                               return_value=popup) as ctor, \
                mock.patch.object(tk.Misc, "wait_window"):
            self.gui._open_filter_popup("brand")
        self.assertIsNotNone(ctor.call_args.kwargs["on_search"])

    def test_searched_options_survive_the_next_open(self):
        self.gui.filter_options = {"color": [(7, "Piros")]}
        self.gui.facet_titles = {"color": "Szín"}
        popup = mock.MagicMock(result=["7", "9"],
                               found_pairs=[(9, "Lila"), (7, "Piros")])
        with mock.patch.object(monitor_gui, "MultiSelectPopup",
                               return_value=popup), \
                mock.patch.object(tk.Misc, "wait_window"):
            self.gui._open_filter_popup("color")
        # a már ismert opció nem duplikálódik, az újat eltároljuk
        self.assertEqual(self.gui.filter_options["color"],
                         [(7, "Piros"), (9, "Lila")])

    def test_a_facet_without_options_says_so(self):
        self.gui.filter_options = {}
        with mock.patch.object(monitor_gui, "messagebox") as box, \
                mock.patch.object(monitor_gui, "MultiSelectPopup") as popup:
            self.gui._open_filter_popup("color")
        box.showinfo.assert_called_once()
        self.assertFalse(popup.called)

    def test_a_cancelled_facet_popup_changes_nothing(self):
        self.gui.filter_options = {"color": [(7, "Piros")]}
        self.gui.facet_titles = {"color": "Szín"}
        popup = mock.MagicMock(result=None, found_pairs=[])
        with mock.patch.object(monitor_gui, "MultiSelectPopup",
                               return_value=popup), \
                mock.patch.object(tk.Misc, "wait_window"):
            self.gui._open_filter_popup("color")
        self.assertNotIn("color", self.gui.selected)

    def test_a_chip_opens_its_popup(self):
        self.gui.category_tree = [{"id": 1, "title": "Női", "url": "/c/1"}]
        self.gui._rebuild_chips()
        self.settle()
        with mock.patch.object(monitor_gui, "CategoryTreePopup") as popup, \
                mock.patch.object(tk.Misc, "wait_window"):
            popup.return_value.result = None
            popup.return_value.catalog_url = None
            self.gui._filter_buttons["catalog"].invoke()
        self.assertTrue(popup.called)

    def test_the_watch_objects_are_built_from_the_list(self):
        self.add_watches(2)
        self.gui.watches[0].update(min_price=1500.0, pages=3, discord=False,
                                   desktop=True)
        objects = self.gui._build_watch_objects()
        self.assertEqual([w.name for w in objects],
                         ["Figyelés 1", "Figyelés 2"])
        self.assertEqual(objects[0].min_price, 1500.0)
        self.assertEqual(objects[0].pages, 3)
        self.assertFalse(objects[0].discord)
        self.assertTrue(objects[0].desktop)

    def test_a_watch_without_a_url_is_rebuilt_from_the_parameters(self):
        self.add_watches(1)
        self.gui.watches[0].update(url="", params={"search_text": "kabát"})
        objects = self.gui._build_watch_objects()
        self.assertEqual(len(objects), 1)
        self.assertIn("search_text=kab%C3%A1t", objects[0].url)

    def test_a_watch_without_url_and_parameters_is_skipped(self):
        self.add_watches(1)
        self.gui.watches[0].update(url="", params={})
        self.assertEqual(self.gui._build_watch_objects(), [])

    def test_the_form_summary_lists_the_filters(self):
        self.gui.search_text_var.set("kabát")
        self.gui.price_from_var.set("1000")
        self.gui.selected = {"catalog": ["1605"]}
        summary = self.gui._form_summary_text()
        self.assertIn("kabát", summary)
        self.assertIn("min. 1 000 Ft", summary)
        self.assertIn("1 oldal", summary)

    def test_the_form_summary_leaves_out_a_zero_minimum(self):
        self.gui.search_text_var.set("kabát")
        self.gui.min_price_var.set("0")
        self.assertNotIn("min.", self.gui._form_summary_text())


# ─── A beállítások ────────────────────────────────────────────────────────

class SettingsTests(GuiTestCase):

    def open_settings(self):
        self.gui._open_settings()
        self.settle()
        return self.gui.settings_win

    def test_the_window_opens_once(self):
        first = self.open_settings()
        second = self.open_settings()
        self.assertIs(first, second)
        self.assertTrue(second.winfo_exists())

    def test_the_keep_history_choice_is_saved(self):
        win = self.open_settings()
        self.gui.keep_history_var.set(True)
        win._close()
        self.settle()
        self.assertTrue(monitor.load_bool(monitor_gui.KEEP_HISTORY_KEY))
        # egy új indítás a mentett értékkel indul
        root = tk.Tk()
        self.addCleanup(root.destroy)
        gui = VintedMonitorGUI(root)
        self.addCleanup(gui._cancel_jobs)
        self.assertTrue(gui.keep_history_var.get())
        self.gui.keep_history_var.set(False)

    def test_the_desktop_choice_is_saved(self):
        win = self.open_settings()
        self.gui.desktop_var.set(True)
        win._close()
        self.settle()
        self.assertTrue(monitor.load_bool(monitor_gui.DESKTOP_KEY))

    def test_the_intervals_are_saved(self):
        win = self.open_settings()
        self.gui.interval_var.set(12)
        self.gui.max_alerts_var.set(20)
        win._close()
        self.settle()
        self.assertEqual(monitor.load_setting("interval_min"), "12")
        self.assertEqual(monitor.load_setting("max_alerts"), "20")

    def test_a_broken_interval_does_not_break_the_save(self):
        win = self.open_settings()
        self.gui.interval_var.set("x")
        self.gui.keep_history_var.set(True)
        win._close()
        self.settle()
        self.assertTrue(monitor.load_bool(monitor_gui.KEEP_HISTORY_KEY))

    def test_the_webhook_is_saved(self):
        win = self.open_settings()
        self.gui.webhook_var.set("https://discord.com/api/webhooks/1/abc")
        win._close()
        self.settle()
        self.assertEqual(monitor.load_setting(monitor_gui.DISCORD_KEY),
                         "https://discord.com/api/webhooks/1/abc")

    def test_a_cleared_webhook_is_removed(self):
        win = self.open_settings()
        self.gui.webhook_var.set("https://discord.com/api/webhooks/1/abc")
        win._close()
        self.settle()
        win = self.open_settings()
        self.gui.webhook_var.set("")
        win._close()
        self.settle()
        self.assertEqual(monitor.load_setting(monitor_gui.DISCORD_KEY), "")

    def test_the_webhook_test_runs_on_its_own_thread(self):
        self.open_settings()
        self.gui.webhook_var.set("https://discord.com/api/webhooks/1/abc")
        with mock.patch.object(monitor_gui, "test_webhook",
                               return_value=(True, "Rendben")) as test:
            self.gui.settings_win._test()
            self.assertTrue(self.wait_for(
                lambda: not self.gui._filters_result_q.empty()))
        self.gui._poll_filters_result()
        self.settle()
        self.assertTrue(test.called)
        self.assertEqual(test.call_args.args[0],
                         "https://discord.com/api/webhooks/1/abc")
        self.assertEqual(
            str(self.gui.settings_win.test_status.cget("text")), "Rendben")

    def test_a_failed_webhook_test_shows_the_reason(self):
        self.open_settings()
        with mock.patch.object(monitor_gui, "test_webhook",
                               return_value=(False, "Elutasítva")) as test:
            self.gui.settings_win._test()
            self.assertTrue(self.wait_for(
                lambda: not self.gui._filters_result_q.empty()))
        self.gui._poll_filters_result()
        self.settle()
        self.assertTrue(test.called)
        self.assertIn("Elutasítva",
                      str(self.gui.settings_win.test_status.cget("text")))

    def test_a_raising_webhook_test_does_not_leave_the_button_busy(self):
        self.open_settings()

        def boom(url):
            raise RuntimeError("nincs net")

        with mock.patch.object(monitor_gui, "test_webhook", boom):
            self.gui.settings_win._test()
            self.assertTrue(self.wait_for(
                lambda: not self.gui._filters_result_q.empty()))
        self.gui._poll_filters_result()
        self.settle()
        self.assertIn("nincs net",
                      str(self.gui.settings_win.test_status.cget("text")))

    def test_a_webhook_result_outlives_the_window(self):
        self.open_settings().destroy()
        self.gui._filters_result_q.put(("webhook", (False, "Elutasítva")))
        self.gui._poll_filters_result()
        self.gui._poll_log_queue()
        self.settle()
        self.assertIn("Elutasítva", self.log_text())

    def test_the_theme_radio_button_applies_live(self):
        self.open_settings()
        self.gui.theme_var.set("modern")
        self.gui.settings_win._apply_theme()
        self.settle()
        self.assertEqual(monitor_gui.CARD, THEMES["modern"]["CARD"])
        self.assertEqual(monitor.load_setting(VintedMonitorGUI.THEME_KEY),
                         "modern")
        self.gui.apply_theme("klasszikus")

    def test_the_history_policy_deletes_the_database(self):
        with mock.patch.object(monitor_gui, "load_bool",
                               return_value=False), \
                mock.patch.object(monitor_gui, "delete_db_file",
                                  return_value=True) as drop:
            self.gui._apply_history_policy()
        self.assertTrue(drop.called)

    def test_the_history_policy_can_keep_the_database(self):
        with mock.patch.object(monitor_gui, "load_bool", return_value=True), \
                mock.patch.object(monitor_gui, "delete_db_file") as drop:
            self.gui._apply_history_policy()
        self.assertFalse(drop.called)

    def test_resetting_the_data_clears_the_table(self):
        panel = self.gui.results
        panel.add(result(), "Figyelés 1")
        with mock.patch.object(monitor_gui, "messagebox") as box, \
                mock.patch.object(monitor_gui, "delete_db_file",
                                  return_value=True) as drop:
            box.askyesno.return_value = True
            self.gui.reset_data()
        self.assertTrue(drop.called)
        self.assertEqual(panel.rows, {})
        self.assertEqual(str(self.gui.result_count.cget("text")), "0 sor")

    def test_a_declined_reset_keeps_the_data(self):
        with mock.patch.object(monitor_gui, "messagebox") as box, \
                mock.patch.object(monitor_gui, "delete_db_file") as drop:
            box.askyesno.return_value = False
            self.gui.reset_data()
        self.assertFalse(drop.called)

    def test_resetting_is_blocked_while_the_monitor_runs(self):
        with mock.patch.object(monitor_gui, "messagebox") as box, \
                mock.patch.object(monitor_gui, "delete_db_file") as drop:
            self.gui.worker_thread = mock.MagicMock()
            self.gui.worker_thread.is_alive.return_value = True
            self.gui.reset_data()
            self.gui.worker_thread = None
        box.showwarning.assert_called_once()
        self.assertFalse(drop.called)


# ─── A téma ───────────────────────────────────────────────────────────────

class ThemeTests(GuiTestCase):

    def test_both_themes_apply(self):
        for name in THEMES:
            self.gui.apply_theme(name)
            self.settle()
            self.assertEqual(self.gui.theme_var.get(), name)
            self.assertEqual(monitor_gui.CARD, THEMES[name]["CARD"])
            self.assertEqual(str(self.root.cget("bg")), THEMES[name]["BG"])
        self.gui.apply_theme("klasszikus")

    def test_an_unknown_name_falls_back_to_the_classic_theme(self):
        self.gui.apply_theme("nincs ilyen")
        self.settle()
        self.assertEqual(self.gui.theme_var.get(), "klasszikus")

    def test_the_scroll_frames_are_recoloured(self):
        self.gui.apply_theme("modern")
        self.settle()
        for frame in (self.form_scroll(), self.gui._watch_scroll):
            self.assertEqual(frame.cget("bg"), THEMES["modern"]["CARD"])
            self.assertEqual(frame.canvas.cget("bg"), THEMES["modern"]["CARD"])
            self.assertEqual(frame.inner.cget("bg"), THEMES["modern"]["CARD"])
        self.gui.apply_theme("klasszikus")

    def test_the_watch_list_still_scrolls_after_a_theme_change(self):
        self.add_watches(40)
        self.gui.apply_theme("modern")
        self.settle()
        sf = self.gui._watch_scroll
        self.assertTrue(sf.vsb.winfo_manager())
        sf.canvas.yview_moveto(1.0)
        self.settle()
        self.assertAlmostEqual(sf.canvas.yview()[1], 1.0, places=3)
        self.gui.apply_theme("klasszikus")

    def test_the_sidebar_keeps_its_own_colour(self):
        self.gui.apply_theme("modern")
        self.settle()
        allowed = {THEMES["modern"]["SIDEBAR"], THEMES["modern"]["SIDEBAR_HOVER"],
                   THEMES["modern"]["SIDEBAR_ACTIVE"]}
        for item in self.gui._nav_items.values():
            self.assertIn(str(item.cget("bg")), allowed)
        self.assertEqual(str(self.gui.settings_link.cget("bg")),
                         THEMES["modern"]["SIDEBAR"])
        self.gui.apply_theme("klasszikus")

    def test_the_log_and_the_table_follow_the_theme(self):
        self.gui.apply_theme("modern")
        self.settle()
        self.assertEqual(self.gui.log_text.cget("bg"), THEMES["modern"]["CARD"])
        self.assertEqual(self.gui.results.wrap.cget("bg"),
                         THEMES["modern"]["GRID_BG"])
        self.gui.apply_theme("klasszikus")

    def test_the_navigation_highlight_returns_after_a_change(self):
        self.show("log")
        self.gui.apply_theme("modern")
        self.settle()
        active = [k for k, item in self.gui._nav_items.items()
                  if str(item.cget("bg")) == monitor_gui.SIDEBAR_ACTIVE]
        self.assertEqual(active, ["log"])
        self.gui.apply_theme("klasszikus")


# ─── A találatok ──────────────────────────────────────────────────────────

class ResultsTests(GuiTestCase):

    def test_a_new_item_lands_in_the_table(self):
        self.show("results")
        self.gui.results.add(result(), "Figyelés 1", when="12:00:00")
        self.settle()
        self.assertEqual(len(self.gui.results.tree.get_children()), 1)
        self.assertEqual(self.gui.results.rows["1"], "r0")
        values = self.gui.results.tree.item("r0")["values"]
        self.assertEqual(values[0], "12:00:00")
        self.assertEqual(values[1], "2500 HUF")
        self.assertEqual(values[4], "Kabát")
        self.assertEqual(values[5], "Figyelés 1")

    def test_a_new_item_is_highlighted(self):
        self.gui.results.add(result(), is_new=True)
        self.assertIn("new", self.gui.results.tree.item("r0")["tags"])
        panel = self.gui.results
        panel.add(result(id="2"), is_new=False)
        self.assertNotIn("new", panel.tree.item("r1")["tags"])

    def test_the_same_item_is_not_added_twice(self):
        for _ in range(3):
            self.gui.results.add(result())
        self.assertEqual(len(self.gui.results.tree.get_children()), 1)

    def test_the_row_counter_follows_the_queue(self):
        self.gui._result_q.put(("item", result(), "Figyelés 1", True))
        self.gui._poll_results()
        self.settle()
        self.assertEqual(str(self.gui.result_count.cget("text")), "1 sor")
        self.assertIn("new", self.gui.results.tree.item("r0")["tags"])

    def test_the_oldest_row_falls_out_at_the_limit(self):
        panel = self.gui.results
        for i in range(panel.LIMIT + 5):
            panel.add(result(id=str(i), title=f"t{i}"))
        self.settle()
        self.assertEqual(len(panel.tree.get_children()), panel.LIMIT)
        self.assertIn("5", panel.rows)
        self.assertNotIn("0", panel.rows)

    def test_missing_values_become_a_dash(self):
        self.gui.results.add(result(price=None, size=None, status=None))
        values = self.gui.results.tree.item("r0")["values"]
        self.assertEqual(tuple(values[1:4]), ("—", "—", "—"))

    def test_clearing_empties_the_table(self):
        self.gui.results.add(result())
        self.gui._clear_results()
        self.settle()
        self.assertEqual(self.gui.results.tree.get_children(), ())
        self.assertEqual(self.gui.results.rows, {})
        self.assertEqual(str(self.gui.badge.cget("text")), "0 új találat")
        self.assertEqual(str(self.gui.result_count.cget("text")), "0 sor")

    def test_stored_rows_can_be_reloaded(self):
        self.gui.results.load_rows([
            {"id": "1", "title": "Kabát", "price": "2500 HUF", "url": "u",
             "size": "M", "status": "Jó", "watch": "Figyelés 1",
             "first_seen": "2026-01-01 10:00:00"},
            {"id": "2", "title": "Nadrág", "price": "1500 HUF", "url": "u2",
             "size": "L", "status": "Kiváló", "watch": "Figyelés 1",
             "first_seen": "2026-01-02 11:00:00"},
        ])
        self.settle()
        self.assertEqual(len(self.gui.results.tree.get_children()), 2)
        # a betöltött sor nem új, ezért nincs zöld kiemelés
        self.assertNotIn("new", self.gui.results.tree.item("r0")["tags"])
        values = self.gui.results.tree.item("r0")["values"]
        self.assertEqual(values[0], "10:00:00")
        self.assertEqual(values[5], "Figyelés 1")

    def test_a_double_click_opens_the_item(self):
        opened = []
        panel = self.gui.results
        panel.on_open = opened.append
        panel.add(result())
        self.settle()
        panel.tree.selection_set("r0")
        panel._on_double()
        self.assertEqual(opened, ["https://www.vinted.hu/items/1"])

    def test_a_double_click_without_a_selection_does_nothing(self):
        opened = []
        self.gui.results.on_open = opened.append
        self.gui.results.add(result())
        self.gui.results._on_double()
        self.assertEqual(opened, [])

    def test_a_row_without_a_url_does_not_open(self):
        opened = []
        panel = self.gui.results
        panel.on_open = opened.append
        panel.add(result(url=None))
        panel.tree.selection_set("r0")
        panel._on_double()
        self.assertEqual(opened, [])

    def test_a_desktop_notification_reaches_the_table(self):
        self.show("results")
        self.gui._desktop_notify(result(), monitor.Watch(name="W", url="u"))
        self.gui._poll_results()
        self.settle()
        self.assertEqual(len(self.gui.results.tree.get_children()), 1)

    def test_reloading_reads_the_database_on_a_thread(self):
        rows = [{"id": "1", "title": "Kabát", "price": "2500 HUF", "url": "u",
                 "size": "M", "status": "Jó", "watch": "Figyelés 1",
                 "first_seen": "2026-01-01 10:00:00"}]
        with mock.patch.object(monitor_gui, "init_db") as init, \
                mock.patch.object(monitor_gui, "count_listings",
                                  return_value=len(rows)), \
                mock.patch.object(monitor_gui, "fetch_recent",
                                  return_value=rows):
            self.gui.reload_results()
            self.assertTrue(self.wait_for(
                lambda: not self.gui._result_q.empty()))
        self.gui._poll_results()
        self.settle()
        self.assertTrue(init.called)
        self.assertEqual(len(self.gui.results.tree.get_children()), 1)
        self.assertIn("adatbázisban 1",
                      str(self.gui.result_count.cget("text")))

    def test_a_broken_database_read_is_logged(self):
        self.show("results")
        with mock.patch.object(monitor_gui, "init_db",
                               side_effect=RuntimeError("meghúzódott")):
            self.gui.reload_results()
            self.assertTrue(self.wait_for(
                lambda: not self.gui._result_q.empty()))
        self.gui._poll_results()
        self.gui._poll_log_queue()
        self.settle()
        self.assertIn("meghúzódott", self.log_text())
        self.assertEqual(len(self.gui.results.tree.get_children()), 0)


# ─── A napló ──────────────────────────────────────────────────────────────

class LogTests(GuiTestCase):

    def test_a_line_reaches_the_widget_through_the_queue(self):
        self.gui._log("[ok] kesz")
        self.gui._poll_log_queue()
        self.settle()
        self.assertIn("kesz", self.log_text())

    def test_the_lines_are_classified(self):
        cases = [("[err] hiba", "err"), ("[warn] figyelem", "warn"),
                 ("[ok] kesz", "ok"), ("[dim] halvany", "dim"),
                 ("[i] közepes", "info"), ("kapcsolódás sikertelen", "err"),
                 ("3 figyelés indul most", "hl"), ("okos étlap", "info")]
        for message, tag in cases:
            self.assertEqual(self.gui._classify(message), tag, message)

    def test_the_widget_stays_bounded(self):
        for i in range(3200):
            self.gui._append_log(f" sor {i}")
        self.settle()
        lines = int(self.gui.log_text.index("end-1c").split(".")[0])
        self.assertLessEqual(lines, 3000)
        self.assertIn("sor 3199", self.log_text())

    def test_clearing_empties_the_widget(self):
        self.gui._append_log("sor")
        self.gui._clear_log()
        self.settle()
        self.assertNotIn("sor", self.log_text())

    def test_the_export_writes_the_log_to_a_file(self):
        self.gui._append_log("exportálva")
        here = os.path.join(self._tmp.name, "monitor_gui.py")
        with mock.patch.object(monitor_gui, "__file__", here):
            self.gui._export_log()
        target = os.path.join(self._tmp.name, "monitor_log.txt")
        self.assertTrue(os.path.isfile(target))
        with open(target, encoding="utf-8") as f:
            self.assertIn("exportálva", f.read())

    def test_a_failed_export_is_logged_not_raised(self):
        self.gui._append_log("x")
        here = os.path.join(self._tmp.name, "nincs", "ilyen", "monitor_gui.py")
        with mock.patch.object(monitor_gui, "__file__", here):
            self.gui._export_log()
        self.gui._poll_log_queue()
        self.settle()
        self.assertIn("sikertelen", self.log_text())


# ─── A felugró ablakok ───────────────────────────────────────────────────

class MultiSelectPopupTests(GuiTestCase):

    OPTIONS = [(i, f"Opció {i}") for i in range(40)]

    def open_popup(self, options=None, **kw):
        popup = monitor_gui.MultiSelectPopup(
            self.root, "Szűrő", self.OPTIONS if options is None else options,
            **kw)
        self.addCleanup(self.destroy, popup)
        popup.update_idletasks()
        popup.update()
        return popup

    def destroy(self, widget):
        try:
            widget.destroy()
        except tk.TclError:
            pass

    def test_the_options_are_listed_with_their_state(self):
        popup = self.open_popup(selected=[1, 2])
        self.assertEqual(len(popup.widgets), len(self.OPTIONS))
        self.assertTrue(popup.vars["1"].get())
        self.assertFalse(popup.vars["3"].get())

    def test_the_list_scrolls_when_it_is_long(self):
        self.assertTrue(self.open_popup().box.vsb.winfo_manager())

    def test_a_short_list_needs_no_scrollbar(self):
        popup = self.open_popup(self.OPTIONS[:3])
        self.assertFalse(popup.box.vsb.winfo_manager())

    def test_typing_filters_the_list(self):
        popup = self.open_popup()
        popup.search_var.set("Opció 1")
        popup.update_idletasks()
        popup.update()
        # 1, 10–19
        self.assertEqual(len(popup.widgets), 11)

    def test_a_filter_without_a_hit_says_so(self):
        popup = self.open_popup()
        popup.search_var.set("nincs ilyen")
        popup.update_idletasks()
        popup.update()
        self.assertEqual(popup.widgets, [])
        self.assertIn("Nincs találat.",
                      " ".join(self.labels(popup.inner, ttk.Label)))

    def test_confirming_returns_the_checked_options(self):
        popup = self.open_popup(selected=[1])
        popup.vars["2"].set(True)
        popup._ok()
        self.assertEqual(sorted(popup.result), [1, 2])

    def test_confirming_nothing_returns_an_empty_list(self):
        popup = self.open_popup()
        popup._ok()
        self.assertEqual(popup.result, [])

    def test_cancelling_returns_nothing(self):
        popup = self.open_popup(selected=[1])
        popup.destroy()
        self.assertIsNone(popup.result)

    def test_a_search_result_is_added_and_kept_visible(self):
        popup = self.open_popup()
        popup.search_var.set("Opció 1")
        popup._search_done([(900, "Frissített")])
        popup.update_idletasks()
        popup.update()
        self.assertIn((900, "Frissített"), popup.options)
        self.assertFalse(popup.vars["900"].get())
        self.assertEqual(popup.search_var.get(), "")
        self.assertEqual(len(popup.widgets), len(popup.options))
        self.assertIn("1 találat hozzáadva", popup.status_var.get())

    def test_an_already_present_option_is_not_duplicated(self):
        popup = self.open_popup()
        popup._search_done([(1, "Opció 1")])
        popup.update_idletasks()
        popup.update()
        self.assertEqual([o for o in popup.options if o[0] == 1],
                         [(1, "Opció 1")])
        self.assertEqual(len(popup.options), len(self.OPTIONS))

    def test_an_empty_search_result_is_reported(self):
        popup = self.open_popup()
        popup._search_done([])
        self.assertIn("Nincs találat Vintedtől", popup.status_var.get())

    def test_a_failed_search_is_reported_and_re_enables_the_button(self):
        popup = self.open_popup(options=self.OPTIONS[:3],
                                on_search=lambda kw: [])
        popup.search_var.set("Piros")
        popup._search()
        self.assertTrue(self.wait_for(lambda: not popup._result_q.empty()))
        popup._poll_result()
        popup.update_idletasks()
        self.assertEqual(str(popup.search_btn.cget("state")), "normal")
        self.assertIn("Nincs találat Vintedtől", popup.status_var.get())

    def test_a_raising_search_is_reported(self):
        def boom(kw):
            raise RuntimeError("nincs net")

        popup = self.open_popup(options=self.OPTIONS[:3], on_search=boom)
        popup.search_var.set("Piros")
        popup._search()
        self.assertTrue(self.wait_for(lambda: not popup._result_q.empty()))
        popup._poll_result()
        popup.update_idletasks()
        self.assertIn("nincs net", popup.status_var.get())
        self.assertEqual(str(popup.search_btn.cget("state")), "normal")

    def test_an_empty_keyword_does_not_search(self):
        calls = []
        popup = self.open_popup(options=self.OPTIONS[:3],
                                on_search=lambda kw: calls.append(kw) or [])
        popup._search()
        self.settle(10)
        self.assertEqual(calls, [])
        self.assertTrue(popup._result_q.empty())

    def test_only_the_brand_popup_offers_a_search(self):
        # a `MultiSelectPopup` a `on_search` nélkül nem mutat keresőt,
        # ezért nem mindegyik szűrőnél adunk egy használhatatlan mezőt
        with mock.patch.object(monitor_gui, "MultiSelectPopup") as popup, \
                mock.patch.object(tk.Misc, "wait_window"):
            popup.return_value.result = ["8"]
            popup.return_value.found_pairs = []
            self.gui.filter_options = {"color": [(8, "Kék")]}
            self.gui.facet_titles = {"color": "Szín"}
            self.gui._open_filter_popup("color")
            self.assertIsNone(popup.call_args.kwargs["on_search"])

    def test_the_popup_follows_the_theme(self):
        popup = self.open_popup(self.OPTIONS[:3])
        self.gui.apply_theme("modern")
        popup.update_idletasks()
        self.assertEqual(popup.inner.cget("bg"), THEMES["modern"]["CARD"])
        self.assertEqual(popup.box.inner.cget("bg"), THEMES["modern"]["CARD"])
        self.gui.apply_theme("klasszikus")


class CategoryPopupTests(GuiTestCase):

    TREE = [{"id": 1, "title": "Női", "catalogs": [
                {"id": 10, "title": "Ruha", "catalogs": [
                    {"id": 100, "title": "Kabát", "url": "/catalog/1605"}]},
                {"id": 11, "title": "Cipők", "url": "/catalog/1606"}]},
            {"id": 2, "title": "Férfi", "url": "/catalog/42"}]

    def open_popup(self, **kw):
        popup = monitor_gui.CategoryTreePopup(self.root, self.TREE, **kw)
        self.addCleanup(self.destroy, popup)
        popup.update_idletasks()
        popup.update()
        return popup

    def destroy(self, widget):
        try:
            widget.destroy()
        except tk.TclError:
            pass

    def test_the_root_level_is_listed(self):
        popup = self.open_popup()
        self.assertEqual(popup.listbox.size(), 2)
        self.assertEqual(popup.listbox.get(0), "▸  Női")
        self.assertEqual(popup.listbox.get(1), "   Férfi")

    def test_clicking_a_parent_only_navigates(self):
        popup = self.open_popup()
        popup.listbox.selection_set(0)
        popup._on_select(None)
        popup.update_idletasks()
        self.assertEqual(popup.listbox.size(), 2)
        self.assertEqual(popup.listbox.get(0), "▸  Ruha")
        self.assertIsNone(popup.result)

    def test_a_leaf_can_be_confirmed(self):
        popup = self.open_popup()
        popup.listbox.selection_set(0)
        popup._on_select(None)
        popup.listbox.selection_set(1)
        popup._on_select(None)
        popup._ok()
        self.assertEqual(popup.result, ["11"])
        self.assertEqual(popup.catalog_url, "/catalog/1606")

    def test_a_top_level_leaf_is_immediately_chosen(self):
        popup = self.open_popup()
        popup.listbox.selection_set(1)
        popup._on_select(None)
        popup._ok()
        self.assertEqual(popup.catalog_url, "/catalog/42")

    def test_a_double_click_confirms(self):
        popup = self.open_popup()
        popup.listbox.selection_set(1)
        popup._on_select(None)             # az egyszeres kattintás is fut
        popup._on_double(mock.Mock())
        popup.update_idletasks()
        self.assertEqual(popup.catalog_url, "/catalog/42")

    def test_the_breadcrumb_goes_back(self):
        popup = self.open_popup()
        popup.listbox.selection_set(0)
        popup._on_select(None)
        popup._back()
        popup.update_idletasks()
        self.assertEqual(popup.listbox.size(), 2)
        self.assertEqual(str(popup.back_btn.cget("state")), "disabled")

    def test_a_breadcrumb_button_jumps_back(self):
        popup = self.open_popup()
        popup.listbox.selection_set(0)
        popup._on_select(None)
        popup.listbox.selection_set(0)
        popup._on_select(None)
        popup.update_idletasks()
        self.assertEqual(popup.listbox.size(), 1)
        popup._jump_to(0)
        popup.update_idletasks()
        self.assertEqual(popup.listbox.size(), 2)

    def test_the_selection_can_be_cleared(self):
        popup = self.open_popup()
        popup.listbox.selection_set(1)
        popup._on_select(None)
        popup._clear()
        self.assertIsNone(popup.candidate)
        self.assertIn("nincs kiválasztva", str(popup.sel_label.cget("text")))

    def test_a_cleared_selection_confirms_nothing(self):
        popup = self.open_popup()
        popup.listbox.selection_set(1)
        popup._on_select(None)
        popup._clear()
        popup._ok()
        self.assertEqual(popup.result, [])
        self.assertIsNone(popup.catalog_url)

    def test_a_saved_selection_is_restored(self):
        popup = self.open_popup(selected=["11"])
        self.assertEqual(popup.candidate, {"id": "11", "title": "Cipők"})
        self.assertEqual(popup.path[0]["id"], 1)
        self.assertEqual(popup.listbox.size(), 2)

    def test_an_unknown_saved_selection_is_ignored(self):
        popup = self.open_popup(selected=["999"])
        self.assertIsNone(popup.candidate)

    def test_an_empty_tree_does_not_crash(self):
        popup = monitor_gui.CategoryTreePopup(self.root, [])
        self.addCleanup(self.destroy, popup)
        popup.update_idletasks()
        self.assertEqual(popup.listbox.size(), 0)


class LoadingWindowTests(GuiTestCase):

    def test_it_stays_open(self):
        win = monitor_gui.LoadingWindow(self.root, "Kategóriák betöltése")
        self.addCleanup(win.destroy)
        win.update_idletasks()
        self.assertEqual(win.winfo_exists(), 1)

    def test_the_gui_closes_it(self):
        win = monitor_gui.LoadingWindow(self.root)
        self.gui._loading_win = win
        self.gui._close_loading()
        self.assertIsNone(self.gui._loading_win)
        self.gui._close_loading()          # idempotens

    def test_a_missing_window_is_tolerated(self):
        self.gui._loading_win = None
        self.gui._close_loading()


# ─── Az indulás ───────────────────────────────────────────────────────────

class StartupTests(GuiTestCase):

    def run_startup(self):
        """A valódi indulás lefuttatása, majd megvárni, hogy feldolgozza.

        Nem a sorra várunk: azt a GUI saját poll-függvénye 120 ms-onként
        el is szívja. A betöltő ablak bezárása már a feldolgozás végét jelzi.
        """
        with mock.patch.object(VintedMonitorGUI, "_startup_load",
                               _REAL_STARTUP_LOAD):
            self.gui._startup_load()
            self.assertIsNotNone(self.gui._loading_win)
            self.assertTrue(self.wait_for(
                lambda: self.gui._loading_win is None))
        self.settle()

    def test_saved_watches_are_loaded_and_rendered(self):
        monitor.save_setting("watches", json.dumps(
            [watch("Mentett 1"), watch("Mentett 2")], ensure_ascii=False))
        self.run_startup()
        self.assertEqual([w["name"] for w in self.gui.watches],
                         ["Mentett 1", "Mentett 2"])
        self.assertEqual(len(self.rows()), 2)

    def test_a_broken_watch_list_is_ignored(self):
        monitor.save_setting("watches", "{ez nem json")
        self.run_startup()
        self.assertEqual(self.gui.watches, [])

    def test_a_watch_list_that_is_not_a_list_is_ignored(self):
        monitor.save_setting("watches", '{"név": "x"}')
        self.run_startup()
        self.assertEqual(self.gui.watches, [])

    def test_the_form_starts_with_the_toggles_off(self):
        self.gui.discount_var.set(True)
        self.gui.favourite_var.set(True)
        self.run_startup()
        self.assertFalse(self.gui.discount_var.get())
        self.assertFalse(self.gui.favourite_var.get())
        self.assertFalse(self.gui.give_away_var.get())

    def test_the_loading_window_closes_itself(self):
        self.run_startup()
        self.assertIsNone(self.gui._loading_win)

    def test_an_empty_category_tree_is_tolerated(self):
        with mock.patch.object(monitor_gui, "fetch_category_tree",
                               return_value=None):
            self.run_startup()
        self.gui._poll_log_queue()
        self.settle()
        self.assertEqual(self.gui.category_tree, [])
        self.assertIn("Üres kategóriafa", self.log_text())

    def test_a_failing_category_load_is_logged(self):
        with mock.patch.object(monitor_gui, "fetch_category_tree",
                               side_effect=RuntimeError("nincs net")):
            self.run_startup()
        self.assertFalse(self.gui.category_tree)
        self.assertIsNone(self.gui._loading_win)
        self.gui._poll_log_queue()
        self.settle()
        self.assertIn("nincs net", self.log_text())

    def test_a_loaded_tree_shows_the_chip(self):
        tree = [{"id": 1, "title": "Női", "catalogs": [
            {"id": 1605, "title": "Kabátok", "url": "/catalog/1605"}]}]
        with mock.patch.object(monitor_gui, "fetch_category_tree",
                               return_value=tree):
            self.run_startup()
        self.gui.selected["catalog"] = ["1605"]
        self.gui._update_filter_button("catalog")
        self.settle()
        self.assertEqual(str(self.gui._filter_buttons["catalog"].cget("text")),
                         "Kabátok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
