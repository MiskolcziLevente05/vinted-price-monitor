"""Vinted Price Monitor — desktop GUI (Tkinter).

Layout: search filters on the left, watched searches and the result table
below them, a collapsible event log on the right. All network work happens on
worker threads and reports back through `self.log_queue`, so the Tk main loop
is never blocked.
"""

import atexit
import json
import math
import os
import queue
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import ttk
from tkinter import (
    X, Y, BOTH, LEFT, RIGHT, TOP, BOTTOM, END, W, E, S, NW, CENTER,
    Canvas, Toplevel, Scrollbar, Text, StringVar, BooleanVar, IntVar,
    messagebox,
)

import monitor
from monitor import (
    main_loop,
    build_search_url,
    fetch_category_tree,
    fetch_category_filters,
    fetch_brands,
    test_webhook,
    init_db,
    fetch_recent,
    count_listings,
    delete_db_file,
    load_setting,
    save_setting,
    load_bool,
    save_bool,
    DB_PATH,
    ORDER_OPTIONS,
    STATUS_OPTIONS,
    Watch,
)

# ─── Palette ─────────────────────────────────────────────────────────────────
BG          = "#EAF0F5"
CARD        = "#FFFFFF"
BORDER      = "#DFE6EE"
TEXT        = "#172033"
MUTED       = "#6B778C"
ACCENT      = "#09B1BA"
ACCENT_DK   = "#07868E"
ACCENT_LT   = "#E3F8F9"
HEADER_BG   = "#0C1A2A"
HEADER_SUB  = "#9FB2C5"
GREEN       = "#12A150"
GREEN_LT    = "#E4F7ED"
AMBER       = "#C87A08"
AMBER_LT    = "#FDF1DC"
RED         = "#D13537"
RED_LT      = "#FCEBEB"
GRID_BG     = "#F2F5F8"

FONT  = "Segoe UI"
F_MAIN = FONT

LOG_W = 380      # napló panel szélessége (oldalt dokkolva)
LOG_H = 250      # napló panel magassága (alul dokkolva)
LOG_HINT = 60    # rail szélesség összecsukva

LOG_DOCKS = ("right", "left", "bottom", "hidden")

RESULTS_MIN = 110    # a találati táblázat legkisebb magassága (kis ablaknál)
RESULTS_MAX = 640    # és legnagyobb magassága
WHEEL_STEP = 3       # egy görgő-kattintás hány "egységet" ugrik

# Ezek a widgetek maguk görgetnek a mouse wheelre, ezért felettük a lapot
# nem szabad görgetni — különben kettős görgetés lenne.
_WHEEL_OWNERS = {"Text", "Treeview", "Listbox", "TListbox", "TSpinbox"}

# A chip-sor ajánlott sorrendje — ismeretlen facet code-ok a végére kerülnek
CHIP_ORDER = ["brand", "size", "color", "material", "status", "patterns",
              "brand_collection"]

DISCORD_KEY = "discord_webhook"
KEEP_HISTORY_KEY = "keep_history"
DESKTOP_KEY = "desktop_notify"
HISTORY_NOTIFY_KEY = "desktop_on_new"
LAYOUT_KEY = "layout"


class UI:
    """Shared ttk styles applied once per app."""
    _done = False

    @classmethod
    def init(cls, root):
        if cls._done:
            return
        cls._done = True
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        base_font = (FONT, 10)
        style.configure(".", font=base_font, background=BG, foreground=TEXT)

        style.configure("App.TFrame", background=CARD)
        style.configure("Page.TFrame", background=BG)

        style.configure("App.TLabel", background=CARD, foreground=TEXT)
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED)

        style.configure("Title.TLabel", background=HEADER_BG, foreground="#FFFFFF",
                        font=(FONT, 16, "bold"))
        style.configure("HeaderSub.TLabel", background=HEADER_BG, foreground=HEADER_SUB,
                        font=(FONT, 9))

        style.configure("SectionTitle.TLabel", background=CARD, foreground=TEXT,
                        font=(FONT, 11, "bold"))
        style.configure("Field.TLabel", background=CARD, foreground=MUTED,
                        font=(FONT, 9))

        style.configure("TEntry", fieldbackground=CARD, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, insertcolor=TEXT,
                        padding=(8, 6))
        style.map("TEntry", bordercolor=[("focus", ACCENT)])

        style.configure("TCombobox", fieldbackground=CARD, background=CARD,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        arrowcolor=MUTED, padding=(8, 5))
        style.map("TCombobox",
                  bordercolor=[("focus", ACCENT)],
                  fieldbackground=[("readonly", CARD)])

        style.configure("TButton", background=CARD, foreground=TEXT,
                        bordercolor=BORDER, focusthickness=0, padding=(10, 6))
        style.map("TButton", background=[("active", GRID_BG)])

        style.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF",
                        bordercolor=ACCENT, focusthickness=0, padding=(22, 11),
                        font=(FONT, 11, "bold"))
        style.map("Accent.TButton",
                  background=[("pressed", ACCENT_DK), ("active", ACCENT_DK),
                              ("disabled", "#B7E2E4")])

        style.configure("Stop.TButton", background=RED, foreground="#FFFFFF",
                        bordercolor=RED, focusthickness=0, padding=(22, 11),
                        font=(FONT, 11, "bold"))
        style.map("Stop.TButton",
                  background=[("pressed", "#A8292B"), ("active", "#A8292B")])

        style.configure("Ghost.TButton", background=CARD, foreground=ACCENT_DK,
                        bordercolor=ACCENT, focusthickness=0, padding=(12, 7),
                        font=(FONT, 10))
        style.map("Ghost.TButton",
                  background=[("pressed", ACCENT_LT), ("active", ACCENT_LT)],
                  foreground=[("disabled", "#AFC6C8")])

        style.configure("Soft.TButton", background=BG, foreground=TEXT,
                        bordercolor=BORDER, focusthickness=0, padding=(12, 7),
                        font=(FONT, 10))
        style.map("Soft.TButton",
                  background=[("pressed", "#DCE4EC"), ("active", "#E3EAF1")])

        style.configure("Link.TButton", background=CARD, foreground=ACCENT_DK,
                        bordercolor=BORDER, focusthickness=0, padding=(8, 4),
                        font=(FONT, 9))
        style.map("Link.TButton", background=[("active", ACCENT_LT)])

        style.configure("Filter.TButton", background=CARD, foreground=TEXT,
                        bordercolor=BORDER, focusthickness=0, padding=(6, 8),
                        font=(FONT, 9))
        style.map("Filter.TButton",
                  background=[("active", ACCENT_LT)],
                  bordercolor=[("active", ACCENT)])

        style.configure("Crumb.TButton", background=CARD, foreground=ACCENT_DK,
                        bordercolor=BORDER, focusthickness=0, padding=(4, 2),
                        font=(FONT, 9, "bold"))
        style.map("Crumb.TButton",
                  background=[("active", ACCENT_LT)])

        style.configure("TCheckbutton", background=CARD, foreground=TEXT,
                        font=(FONT, 10))
        style.map("TCheckbutton", background=[("active", CARD)])

        style.configure("Card.TCheckbutton", background=CARD, foreground=TEXT,
                        font=(FONT, 10))
        style.map("Card.TCheckbutton", background=[("active", CARD)])

        style.configure("Hint.TCheckbutton", background=CARD, foreground=MUTED,
                        font=(FONT, 9))
        style.map("Hint.TCheckbutton", background=[("active", CARD)])

        style.configure("Treeview", background=CARD, fieldbackground=CARD,
                        foreground=TEXT, rowheight=26, borderwidth=0,
                        font=(FONT, 10))
        style.map("Treeview", background=[("selected", ACCENT_LT)],
                  foreground=[("selected", TEXT)])
        style.configure("Treeview.Heading", background=BG, foreground=MUTED,
                        font=(FONT, 9, "bold"), relief="flat", padding=(6, 6))

        style.configure("Card.TSeparator", background=BORDER)
        style.configure("TProgressbar", background=ACCENT, troughcolor=BG,
                        bordercolor=BG)


class SettingsWindow(Toplevel):
    """Külön ablakban nyitható beállítások; a megosztott változók élesen
    szinkronban tartják a főablakkal."""

    def __init__(self, master, gui):
        super().__init__(master)
        self.gui = gui
        self.title("Beállítások")
        self.configure(bg=CARD)
        self.geometry("520x600")
        self.resizable(False, False)
        self.transient(master)
        # X-s bezárás is mentsen, nem csak a 'Kész' gomb
        self.protocol("WM_DELETE_WINDOW", self._close)

        header = tk.Frame(self, bg=HEADER_BG)
        header.pack(fill=X)
        tk.Label(header, text="Beállítások", bg=HEADER_BG, fg="#FFFFFF",
                 font=(FONT, 12, "bold"), padx=14, pady=12).pack(anchor=W)

        body = tk.Frame(self, bg=CARD)
        body.pack(fill=BOTH, expand=True, padx=18, pady=16)

        row_t = tk.Frame(body, bg=CARD)
        row_t.pack(fill=X)
        ttk.Checkbutton(row_t, text="Discord értesítés", variable=self.gui.discord_var,
                        style="Card.TCheckbutton").pack(side=LEFT, padx=(0, 18))
        ttk.Checkbutton(row_t, text="Asztali értesítés", variable=self.gui.desktop_var,
                        style="Card.TCheckbutton").pack(side=LEFT, padx=(0, 18))
        ttk.Checkbutton(row_t, text="Előzmények megőrzése",
                        variable=self.gui.keep_history_var,
                        style="Card.TCheckbutton").pack(side=LEFT)

        tk.Frame(body, bg=BORDER, height=1).pack(fill=X, pady=(16, 0))

        row_w = tk.Frame(body, bg=CARD)
        row_w.pack(fill=X, pady=(12, 0))
        tk.Label(row_w, text="Discord webhook", bg=CARD, fg=TEXT,
                 font=(FONT, 9)).pack(side=LEFT)
        ttk.Entry(row_w, textvariable=self.gui.webhook_var).pack(
            side=LEFT, fill=X, expand=True, padx=(12, 8))
        ttk.Button(row_w, text="Teszt", style="Link.TButton",
                   command=self._test).pack(side=RIGHT)

        self.test_status = tk.Label(body, text="", bg=CARD, fg=MUTED,
                                    font=(FONT, 9), anchor=W)
        self.test_status.pack(fill=X, pady=(6, 0))

        tk.Frame(body, bg=BORDER, height=1).pack(fill=X, pady=(16, 0))

        row_i = tk.Frame(body, bg=CARD)
        row_i.pack(fill=X, pady=(12, 0))
        tk.Label(row_i, text="Ártesztelés", bg=CARD, fg=TEXT,
                 font=(FONT, 9)).pack(side=LEFT)
        ttk.Spinbox(row_i, from_=1, to=120, width=6, increment=5,
                    textvariable=self.gui.interval_var).pack(
                        side=LEFT, padx=(12, 8))
        tk.Label(row_i, text="perc (jitter ±)", bg=CARD, fg=MUTED,
                 font=(FONT, 9)).pack(side=LEFT)
        row_m = tk.Frame(body, bg=CARD)
        row_m.pack(fill=X, pady=(8, 0))
        tk.Label(row_m, text="Discord üzenet / ciklus", bg=CARD, fg=TEXT,
                 font=(FONT, 9)).pack(side=LEFT)
        ttk.Spinbox(row_m, from_=1, to=50, width=6,
                    textvariable=self.gui.max_alerts_var).pack(
                        side=LEFT, padx=(12, 8))
        tk.Label(row_m, text="db (a többi összefoglalóként megy)", bg=CARD,
                 fg=MUTED, font=(FONT, 9)).pack(side=LEFT)

        info = tk.Frame(body, bg=ACCENT_LT, highlightbackground=ACCENT,
                        highlightthickness=1)
        info.pack(fill=X, pady=(16, 0))
        tk.Label(info, text=(
            "A kategória kiválasztásakor a szűrők (facetek) automatikusan\n"
            "betöltődnek élő Vinted adatokból — böngésző nélkül, az API-ról.\n\n"
            "A webhook és a beállítások a settings.db-be mentődnek.\n"
            "Üres webhook esetén a Discord értesítés kimarad."),
            bg=ACCENT_LT, fg=TEXT, font=(FONT, 9), wraplength=460,
            padx=12, pady=10, justify="left").pack(fill=X)

        bar = tk.Frame(self, bg=CARD)
        bar.pack(fill=X, padx=18, pady=12)
        ttk.Button(bar, text="Kész", style="Accent.TButton",
                   command=self._close).pack(side=RIGHT)

    def _test(self):
        """Élő webhook-ellenőrzés külön szálon, hogy a GUI ne fagyjon."""
        self.test_status.configure(text="Tesztelés…", fg=MUTED)
        self.gui._save_webhook()

        def worker():
            ok, msg = test_webhook(self.gui.webhook_var.get().strip())
            self.gui.after(0, lambda: self.test_status.configure(
                text=msg, fg=GREEN if ok else RED))

        threading.Thread(target=worker, daemon=True).start()

    def _close(self):
        """A 'Kész' gomb és az X is azonnal kiírja a webhookot."""
        self.gui._save_settings()
        self.destroy()


class LoadingWindow(Toplevel):
    """Indításnál megjelenő, forgó spinneres ablak; nem zárható kézzel."""

    _SPIN = ["#09B1BA", "#2FBFC7", "#55CCD3", "#7CD9DE",
             "#A3E6EA", "#C9F2F4", "#E3F8F9", "#E3F8F9"]

    def __init__(self, master, text="Betöltés…", sub="Élő Vinted adatok lekérése…"):
        super().__init__(master)
        self.title("Betöltés")
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.grab_set()

        self.angle = 0
        self.canvas = Canvas(self, width=72, height=72, bg=CARD,
                             highlightthickness=0)
        self.canvas.pack(padx=48, pady=(30, 8))
        self._draw_spinner()
        self.after(40, self._spin)

        ttk.Label(self, text=text, style="SectionTitle.TLabel",
                  anchor=CENTER).pack(fill=X, padx=30)
        ttk.Label(self, text=sub, style="Muted.TLabel",
                  anchor=CENTER).pack(fill=X, pady=(2, 30))

        self.update_idletasks()
        self._center(master)

    def _center(self, master):
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _draw_spinner(self):
        self.canvas.delete("all")
        cx = cy = 36
        r = 26
        n = 8
        for i in range(n):
            a = math.radians(self.angle + i * (360.0 / n))
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            self.canvas.create_oval(x - 4.5, y - 4.5, x + 4.5, y + 4.5,
                                    fill=self._SPIN[i], outline="")

    def _spin(self):
        if not self.winfo_exists():
            return
        self.angle = (self.angle - 24) % 360
        self._draw_spinner()
        self.after(40, self._spin)


class MultiSelectPopup(Toplevel):
    """Többes választó popup; opcionális Vinted-kereséssel."""

    def __init__(self, master, title, options, selected=None, on_search=None):
        super().__init__(master)
        self.title(title)
        self.configure(bg=CARD)
        self.geometry("400x460")
        self.minsize(320, 300)
        self.transient(master)
        self.grab_set()
        self.result = None
        self.options = list(options)
        self.vars = {}
        self.widgets = []
        self.on_search = on_search
        self.found_pairs = []          # megőrzendő keresési találatok
        self._result_q = queue.Queue()
        selected = set(str(x) for x in (selected or ()))

        for opt_id, label in self.options:
            var = BooleanVar(value=str(opt_id) in selected)
            self.vars[str(opt_id)] = var

        header = tk.Frame(self, bg=HEADER_BG)
        header.pack(fill=X)
        tk.Label(header, text=title, bg=HEADER_BG, fg="#FFFFFF",
                 font=(FONT, 12, "bold"), padx=14, pady=12).pack(anchor=W)

        body = tk.Frame(self, bg=CARD)
        body.pack(fill=BOTH, expand=True, padx=12, pady=12)

        self.search_var = StringVar()
        self.search_entry = ttk.Entry(body, textvariable=self.search_var)
        self.search_entry.pack(fill=X)
        self.search_var.trace_add("write", lambda *a: self._refresh())

        self.search_btn = None
        if on_search is not None:
            self.search_btn = ttk.Button(body, text="Keresés Vintedtől",
                                         style="Ghost.TButton",
                                         command=self._search)
            self.search_btn.pack(fill=X, pady=(8, 0))

        self.status_var = StringVar()
        ttk.Label(body, textvariable=self.status_var, style="Muted.TLabel",
                  anchor=W).pack(fill=X, pady=(6, 0))
        self._build_options_box(body)
        self._refresh()

        bar = tk.Frame(self, bg=CARD)
        bar.pack(fill=X, padx=12, pady=12)
        ttk.Button(bar, text="Kiválasztás", style="Accent.TButton",
                   command=self._ok).pack(side=LEFT)
        ttk.Button(bar, text="Mégse", style="Soft.TButton",
                   command=self.destroy).pack(side=RIGHT)
        self.after(100, self._poll_result)

    def _build_options_box(self, parent):
        self.box_frame = tk.Frame(parent, bg=CARD)
        self.box_frame.pack(fill=BOTH, expand=True, pady=(6, 0))
        canvas = Canvas(self.box_frame, bg=CARD, highlightthickness=0)
        scroll = Scrollbar(self.box_frame, orient="vertical", command=canvas.yview)
        self.inner = tk.Frame(canvas, bg=CARD)
        self.inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=RIGHT, fill=Y)
        canvas.pack(side=LEFT, fill=BOTH, expand=True)
        canvas.bind("<Enter>", lambda e: self._bind_mousewheel(canvas))
        canvas.bind("<Leave>", lambda e: self._unbind_mousewheel(canvas))
        self.canvas = canvas

    def _bind_mousewheel(self, canvas):
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

    def _unbind_mousewheel(self, canvas):
        canvas.unbind_all("<MouseWheel>")

    def _refresh(self):
        kw = self.search_var.get().strip().lower()
        for w in self.widgets:
            w.pack_forget()
        self.widgets = []
        seen = 0
        for opt_id, label in self.options:
            if kw and kw not in label.lower():
                continue
            cb = ttk.Checkbutton(self.inner, text=label,
                                 variable=self.vars[str(opt_id)])
            cb.pack(fill=X, padx=2, pady=1)
            self.widgets.append(cb)
            seen += 1
        if seen == 0:
            ttk.Label(self.inner, text="Nincs találat.",
                      style="Muted.TLabel").pack(anchor=W, padx=2, pady=4)

    def _search(self):
        kw = self.search_var.get().strip()
        if not kw or not self.on_search:
            return
        if self.search_btn:
            self.search_btn.configure(state="disabled")
        self.status_var.set("Keresés folyamatban…")
        threading.Thread(target=self._search_worker, args=(kw,), daemon=True).start()

    def _search_worker(self, kw):
        try:
            pairs = self.on_search(kw)
        except Exception as e:
            self._result_q.put(("err", str(e)))
            return
        self._result_q.put(("ok", pairs))

    def _poll_result(self):
        while not self._result_q.empty():
            kind, payload = self._result_q.get_nowait()
            if kind == "ok":
                self._search_done(payload)
            else:
                if self.search_btn:
                    self.search_btn.configure(state="normal")
                self.status_var.set(f"Keresési hiba: {payload[:80]}")
        if self.winfo_exists():
            self.after(100, self._poll_result)

    def _search_done(self, pairs):
        if self.search_btn:
            self.search_btn.configure(state="normal")
        self.status_var.set(
            f"{len(pairs)} találat hozzáadva" if pairs else "Nincs találat Vintedtől")
        existing = {str(i) for i, _ in self.options}
        for opt_id, label in pairs:
            key = str(opt_id)
            if key not in existing:
                self.options.append((opt_id, label))
                self.vars[key] = BooleanVar(value=False)
                existing.add(key)
            self.found_pairs.append((opt_id, label))
        # a keresési találatok a szűrési mező alá kerülnek, ne tűnjenek el
        if self.search_var.get().strip():
            self.search_var.set("")
        self._refresh()

    def _ok(self):
        self.result = [opt_id for opt_id, _ in self.options
                       if self.vars[str(opt_id)].get()]
        self.destroy()


class CategoryTreePopup(Toplevel):
    """Drill-down kategóriaböngésző: kattintás csak navigál, kijelölés a gombbal."""

    def __init__(self, master, tree, selected=None):
        super().__init__(master)
        self.title("Kategóriák")
        self.configure(bg=CARD)
        self.geometry("560x600")
        self.minsize(360, 380)
        self.transient(master)
        self.grab_set()
        self.result = None

        self.root_nodes = tree or []
        self._lookup = {}
        self._build_lookup(self.root_nodes)
        self.path = []          # út a gyökértől az aktuális szintig
        self.candidate = None
        self.candidate_node = None
        self.catalog_url = None
        self.nodes = []

        header = tk.Frame(self, bg=HEADER_BG)
        header.pack(fill=X)
        tk.Label(header, text="Kategóriák · egyszerre csak egy választható",
                 bg=HEADER_BG, fg="#FFFFFF", font=(FONT, 12, "bold"),
                 padx=14, pady=12).pack(anchor=W)

        body = tk.Frame(self, bg=CARD)
        body.pack(fill=BOTH, expand=True, padx=12, pady=12)

        sel_row = tk.Frame(body, bg=CARD)
        sel_row.pack(fill=X, pady=(0, 8))
        self.sel_label = tk.Label(sel_row, text="", bg=CARD, fg=TEXT,
                                  font=(FONT, 10, "bold"), anchor=W)
        self.sel_label.pack(side=LEFT, fill=X, expand=True)
        self.ok_btn = ttk.Button(sel_row, text="Kiválasztás",
                                 style="Accent.TButton", command=self._ok)
        self.ok_btn.pack(side=RIGHT)

        crumb_row = tk.Frame(body, bg=CARD)
        crumb_row.pack(fill=X, pady=(0, 8))
        self.crumb = tk.Frame(crumb_row, bg=CARD)
        self.crumb.pack(side=LEFT)
        self.back_btn = ttk.Button(crumb_row, text="← Vissza",
                                   style="Soft.TButton", command=self._back)
        self.back_btn.pack(side=RIGHT)

        wrap = tk.Frame(body, bg=GRID_BG, highlightbackground=BORDER,
                        highlightthickness=1)
        wrap.pack(fill=BOTH, expand=True)
        self.listbox = tk.Listbox(
            wrap, bg=CARD, fg=TEXT, relief="flat", font=(FONT, 10),
            selectmode="browse", highlightthickness=0, activestyle="dotbox",
            exportselection=False, selectbackground=ACCENT_LT,
            selectforeground=TEXT, borderwidth=0,
        )
        vsb = Scrollbar(wrap, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=vsb.set)
        self.listbox.pack(side=LEFT, fill=BOTH, expand=True)
        vsb.pack(side=RIGHT, fill=Y)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<Double-Button-1>", self._on_double)

        hint = ttk.Label(body,
                         text="kattintás = belépés / jelölés · dupla kattintás = kiválasztás",
                         style="Muted.TLabel", anchor=W)
        hint.pack(fill=X, pady=(8, 0))

        bar = tk.Frame(self, bg=CARD)
        bar.pack(fill=X, padx=12, pady=12)
        ttk.Button(bar, text="Törlés", style="Soft.TButton",
                   command=self._clear).pack(side=LEFT)
        ttk.Button(bar, text="Mégse", style="Soft.TButton",
                   command=self.destroy).pack(side=RIGHT)

        self._init_from_selected(selected)
        self._show_level()

    def _build_lookup(self, nodes):
        for n in nodes:
            cid = str(n.get("id"))
            self._lookup[cid] = n.get("title") or cid
            self._build_lookup(n.get("catalogs") or [])

    def _find_parent_path(self, nodes, cid, trail):
        for n in nodes:
            nid = str(n.get("id"))
            if nid == cid:
                return trail
            kids = n.get("catalogs") or []
            if kids:
                res = self._find_parent_path(kids, cid, trail + [n])
                if res is not None:
                    return res
        return None

    def _find_node(self, nodes, cid):
        for n in nodes:
            if str(n.get("id")) == cid:
                return n
            res = self._find_node(n.get("catalogs") or [], cid)
            if res is not None:
                return res
        return None

    def _init_from_selected(self, selected):
        for cid in selected or ():
            cid = str(cid)
            if cid not in self._lookup:
                continue
            self.candidate = {"id": cid, "title": self._lookup[cid]}
            self.candidate_node = self._find_node(self.root_nodes, cid)
            parent = self._find_parent_path(self.root_nodes, cid, [])
            if parent:
                self.path = parent
            break

    def _show_level(self):
        self.listbox.delete(0, END)
        parent = self.path[-1] if self.path else None
        self.nodes = (parent.get("catalogs") or []) if parent else self.root_nodes
        for n in self.nodes:
            title = n.get("title") or str(n.get("id"))
            arrow = "▸  " if n.get("catalogs") else "   "
            self.listbox.insert(END, f"{arrow}{title}")
        self._render_breadcrumb()
        self._render_selection()

    def _render_breadcrumb(self):
        for w in self.crumb.winfo_children():
            w.destroy()
        if not self.path:
            tk.Label(self.crumb, text="Főoldal", bg=CARD, fg=MUTED,
                     font=(FONT, 9)).pack(side=LEFT)
        for i, node in enumerate(self.path):
            if i:
                tk.Label(self.crumb, text="›", bg=CARD, fg=MUTED,
                         font=(FONT, 9)).pack(side=LEFT)
            ttk.Button(self.crumb, text=node["title"], style="Crumb.TButton",
                       command=lambda idx=i: self._jump_to(idx)).pack(side=LEFT)
        self.back_btn.configure(state="normal" if self.path else "disabled")

    def _render_selection(self):
        if self.candidate:
            self.sel_label.configure(
                text=f"Kiválasztott:  {self.candidate['title']}")
        else:
            self.sel_label.configure(text="Kiválasztott:  — nincs kiválasztva")
        self.ok_btn.configure(state="normal")

    def _on_select(self, event):
        sel = self.listbox.curselection()
        if not sel:
            return
        n = self.nodes[sel[0]]
        self.candidate = {"id": str(n.get("id")),
                          "title": n.get("title") or str(n.get("id"))}
        self.candidate_node = n
        if n.get("catalogs"):
            self.path.append(n)
            self._show_level()
        else:
            self._render_selection()

    def _on_double(self, event):
        if self.listbox.curselection():
            self._ok()

    def _back(self):
        if not self.path:
            return
        self.path.pop()
        if self.path:
            self.candidate = {"id": str(self.path[-1]["id"]),
                              "title": self.path[-1].get("title")}
            self.candidate_node = self.path[-1]
        else:
            self.candidate = None
            self.candidate_node = None
        self._show_level()

    def _jump_to(self, idx):
        if idx < 0 or idx >= len(self.path):
            return
        self.path = self.path[:idx + 1]
        self.candidate = {"id": str(self.path[-1]["id"]),
                          "title": self.path[-1].get("title")}
        self.candidate_node = self.path[-1]
        self._show_level()

    def _clear(self):
        self.candidate = None
        self.candidate_node = None
        self._render_selection()

    def _ok(self):
        self.result = [self.candidate["id"]] if self.candidate else []
        self.catalog_url = None
        if self.candidate and self.candidate_node:
            self.catalog_url = self.candidate_node.get("url")
        self.destroy()


class ResultsPanel:
    """A talált tételek táblázata; dupla kattintásra megnyílik a böngészőben."""

    COLUMNS = ("when", "price", "size", "status", "title", "watch")
    LIMIT = 400

    def __init__(self, parent, on_open=None):
        self.on_open = on_open
        self.rows = {}                      # item id -> iid
        self.urls = {}                      # iid -> url
        self._next = 0

        self.wrap = tk.Frame(parent, bg=GRID_BG, highlightbackground=BORDER,
                             highlightthickness=1)
        self.wrap.pack(fill=BOTH, expand=True)

        self.tree = ttk.Treeview(self.wrap, columns=self.COLUMNS,
                                 show="headings", selectmode="browse")
        heads = {
            "when": ("Idő", 62, "center", False),
            "price": ("Ár", 78, "e", False),
            "size": ("Méret", 82, "center", False),
            "status": ("Állapot", 82, "center", False),
            "title": ("Termék", 300, "w", True),
            "watch": ("Figyelés", 96, "w", False),
        }
        for col, (label, width, anchor, stretch) in heads.items():
            self.tree.heading(col, text=label, anchor=anchor)
            self.tree.column(col, width=width, anchor=anchor,
                             stretch=stretch, minwidth=50)
        vsb = Scrollbar(self.wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=RIGHT, fill=Y)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        self.tree.tag_configure("odd", background="#FAFCFD")
        self.tree.tag_configure("new", foreground=GREEN)
        self.tree.bind("<Double-Button-1>", self._on_double)
        self.tree.bind("<Return>", self._on_double)

    def _on_double(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        url = self.urls.get(sel[0])
        if url:
            if self.on_open:
                self.on_open(url)
            else:
                webbrowser.open(url)

    def add(self, item, watch_name="", when="", is_new=True):
        item_id = str(item.get("id"))
        if item_id in self.rows:
            return
        if len(self.rows) >= self.LIMIT:
            # a beszúrási sorrend megmarad, így a legrégebbi esik ki
            oldest_key = next(iter(self.rows))
            oldest_iid = self.rows.pop(oldest_key)
            self.urls.pop(oldest_iid, None)
            self.tree.delete(oldest_iid)

        iid = f"r{self._next}"
        self._next += 1
        self.rows[item_id] = iid
        self.urls[iid] = item.get("url")
        tags = ("new",) if is_new else ()
        if len(self.rows) % 2 == 0:
            tags += ("odd",)
        self.tree.insert("", END, iid=iid, tags=tags, values=(
            when or time.strftime("%H:%M:%S"),
            item.get("price") or "—",
            item.get("size") or "—",
            item.get("status") or "—",
            (item.get("title") or "")[:120],
            watch_name or "",
        ))

    def clear(self):
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.urls.clear()

    def load_rows(self, rows):
        self.clear()
        for r in rows:
            item = {"id": r["id"], "title": r["title"], "price": r["price"],
                    "url": r["url"], "size": r["size"], "status": r["status"]}
            self.add(item, r.get("watch") or "",
                     when=(r.get("first_seen") or "")[-8:], is_new=False)


class WatchDialog(Toplevel):
    """A figyelés nevének és rövid leírásának beállítása.

    A szűrők már a fő űrlapból jönnek; itt csak az azonosítás és a
    leírás kérdezhető meg. Mentéskor a callback megkapja a nevet és a
    leírást, a lemondás a `on_cancel`-t hívja (ha meg van adva).
    """

    def __init__(self, master, title, name, desc, info, on_save,
                 on_cancel=None):
        super().__init__(master)
        self._on_save = on_save
        self._on_cancel = on_cancel
        self.title(title)
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()

        ctx = tk.Frame(self, bg=CARD, padx=24, pady=18)
        ctx.pack(fill=BOTH, expand=True)

        ttk.Label(ctx, text=title, style="SectionTitle.TLabel",
                  anchor=W).pack(fill=X)
        if info:
            ttk.Label(ctx, text=info, style="Muted.TLabel", anchor=W,
                      wraplength=400, justify=LEFT).pack(fill=X, pady=(4, 12))

        ttk.Label(ctx, text="Név", style="Field.TLabel").pack(anchor=W)
        self.name_var = StringVar(value=name or "")
        self.name_entry = ttk.Entry(ctx, textvariable=self.name_var)
        self.name_entry.pack(fill=X, pady=(2, 12))

        ttk.Label(ctx, text="Leírás (mit figyel)", style="Field.TLabel").pack(anchor=W)
        self.desc_var = StringVar(value=desc or "")
        ttk.Entry(ctx, textvariable=self.desc_var).pack(fill=X, pady=(2, 4))
        ttk.Label(ctx, text="Ez jelenik meg a figyelés-lista második sorában.",
                  style="Muted.TLabel", anchor=W).pack(fill=X)
        ttk.Label(ctx, text="Ha üresen hagyod, a szűrőkből generálódik.",
                  style="Muted.TLabel", anchor=W).pack(fill=X, pady=(0, 4))

        btns = tk.Frame(ctx, bg=CARD)
        btns.pack(fill=X, pady=(14, 0))
        self.save_btn = ttk.Button(btns, text="Mentés", style="Accent.TButton",
                                   command=self._save)
        self.save_btn.pack(side=RIGHT)
        ttk.Button(btns, text="Mégse", style="Ghost.TButton",
                   command=self._cancel).pack(side=RIGHT, padx=(0, 8))

        self.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self._cancel())

        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.name_entry.focus_set()
        self.name_entry.select_range(0, "end")

    def _save(self):
        cb, self._on_save = self._on_save, None
        if cb:
            cb(self.name_var.get().strip(), self.desc_var.get().strip())
        self._done()

    def _cancel(self):
        cb, self._on_cancel = self._on_cancel, None
        if cb:
            cb()
        self._done()

    def _done(self):
        try:
            if self.winfo_exists():
                self.destroy()
        except tk.TclError:
            pass


class Panel(tk.Frame):
    """Újrarendezhető és összecsukható kártya.

    A fejléc fogantyúját (`⠿`) vagy a címét meghúzva a panel máshová
    hurcolható a többi panel között; a `▾` gomb összecsukja. A sorrend,
    az összecsukott állapot és a napló dokkolása a `settings.db`-be kerül,
    így a következő indításkor ugyanígy jön vissza.
    """

    def __init__(self, master, gui, key, title):
        super().__init__(master, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1)
        self.gui = gui
        self.key = key
        self._collapsed = False
        self._fixed = False
        self._height = 0

        self.header = tk.Frame(self, bg=CARD)
        self.header.pack(fill=X, padx=6, pady=(9, 0))

        self.grip = tk.Label(self.header, text="⠿", bg=CARD, fg="#AFBCCB",
                             font=(FONT, 13), cursor="fleur", padx=3)
        self.grip.pack(side=LEFT)

        self.title_label = tk.Label(self.header, text=title.upper(), bg=CARD,
                                    fg=ACCENT_DK, font=(FONT, 10, "bold"))
        self.title_label.pack(side=LEFT, padx=(6, 10))

        # A chevron csak összecsuk, nem indít húzást — így a kattintás és a
        # fogantyú nem versenyez egymással.
        self.collapse_btn = tk.Label(self.header, text="▾", bg=CARD, fg=MUTED,
                                     font=(FONT, 12), cursor="hand2", padx=6)
        self.collapse_btn.pack(side=RIGHT)
        self.collapse_btn.bind("<Button-1>", lambda _e: self.toggle())

        self.body = tk.Frame(self, bg=CARD, padx=18, pady=14)
        self.body.pack(fill=BOTH, expand=True)

        for w in (self.grip, self.title_label):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<Escape>", self._drag_cancel)
            w.configure(cursor="fleur")

    # ── drag & drop ───────────────────────────────────────────────────────

    def _drag_start(self, event):
        self.gui._panel_drag_start(self, event)

    def _drag_move(self, _event):
        self.gui._panel_drag_move()

    def _drag_end(self, _event):
        self.gui._panel_drag_end()

    def _drag_cancel(self, _event):
        self.gui._panel_drag_cancel()

    # ── collapse / size ───────────────────────────────────────────────────

    def toggle(self):
        if self._collapsed:
            self.body.pack(fill=BOTH, expand=True)
            self.collapse_btn.configure(text="▾")
            self._collapsed = False
            if self._fixed:
                self.pack_propagate(False)
                self.configure(height=self._height)
            else:
                # tartalom-vezérelt magasság: pl. a figyeléslista nő néhány sorral
                self.pack_propagate(True)
        else:
            self._height = self.winfo_reqheight()
            self.body.pack_forget()
            # A fejléc magasságára zsugorodik; pack_propagate nélkül a
            # Tk nem venné figyelembe a kért magasságot.
            self.pack_propagate(False)
            self.configure(height=self.header.winfo_reqheight() + 11)
            self.collapse_btn.configure(text="▸")
            self._collapsed = True
        self.gui._layout_changed()

    def set_collapsed(self, collapsed):
        if bool(collapsed) != self._collapsed:
            self.toggle()

    def set_body_height(self, height):
        """Rögzíti a panel magasságát (a tartalom nem terjedhet túl rajta)."""
        if self._collapsed or height <= 0:
            return
        self._fixed = True
        self._height = int(height)
        self.pack_propagate(False)
        self.configure(height=self._height)

    def release_height(self):
        self._fixed = False
        self._height = 0
        self.pack_propagate(True)


class VintedMonitorGUI:
    def __init__(self, root):
        self.root = root
        UI.init(root)
        self.root.title("Vinted Price Monitor")
        self.root.geometry("1280x940")
        self.root.minsize(880, 560)
        self.root.configure(bg=BG)

        self.worker_thread = None
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self._filters_result_q = queue.Queue()
        self._control_q = queue.Queue()
        self._result_q = queue.Queue()
        self.settings_win = None
        self._loading_win = None
        self._catalog_url = None
        self._catalog_lookup = None      # cache a kategórianevekre

        # elrendezés (görgethető lap + átrendezhető panelek)
        self._panels = {}
        self._panel_order = []
        self._dock_var = StringVar(value="right")
        self._log_dock = "right"
        self._last_dock = "right"
        self._drag = None
        self._drag_line = None
        self._fitting = False
        self._order, self._collapsed_panels, dock, side = self._load_layout()
        self._log_dock = self._last_dock = dock
        self._dock_var.set(dock)
        self._results_side = side       # a találatok a jobb oldali oszlopban
        self._results_side_var = BooleanVar(value=side)
        self._results_col = None        # a jobb oldali találati oszlop

        self.selected = {"catalog": []}
        self.filter_options = {}
        self.facet_titles = {}
        self.facet_codes = []
        self.category_tree = None
        self._filter_buttons = {}
        self._chip_labels = {}
        self._order_map = {label: value for value, label in ORDER_OPTIONS}
        self._found = 0
        self.watches = []
        self._editing = None
        self._closing = False
        self._after_jobs = set()

        self._build_ui()
        self._update_clear_btn()
        self._poll_log_queue()
        self._poll_filters_result()
        self._poll_control()
        self._poll_results()
        self._bind_shortcuts()
        self._schedule(150, self._startup_load)

        # A monitor összes belső üzenete ide kerüljön, nem a konzolra
        monitor.set_log_sink(self._log)

    # ── Layout ──────────────────────────────────────────────────────────────

    def _bind_shortcuts(self):
        self.root.bind("<Control-Return>", lambda e: self.toggle_monitoring())
        self.root.bind("<Control-l>", lambda e: self._toggle_log())
        self.root.bind("<Control-comma>", lambda e: self._open_settings())
        self.root.bind("<Control-f>", lambda e: self._focus_search())
        self.root.bind("<Escape>", lambda e: self._escape())

    def _focus_search(self):
        try:
            self.search_text_var.set("")
            self.search_entry.focus_set()
        except tk.TclError:
            pass

    def _escape(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.destroy()
        elif self.worker_thread and self.worker_thread.is_alive():
            self.toggle_monitoring()

    def _schedule(self, delay_ms, func):
        """`root.after` nyomon követéssel.

        A callback lefutáskor eldobja a saját azonosítóját, különben a
        `_after_jobs` halmaz egy hosszú munkamenet alatt korlátlanul nőne.
        """
        holder = {}

        def run():
            self._after_jobs.discard(holder.get("id"))
            func()

        job = self.root.after(delay_ms, run)
        holder["id"] = job
        self._after_jobs.add(job)
        return job

    def _unschedule(self, job):
        if job is None:
            return
        self._after_jobs.discard(job)
        try:
            self.root.after_cancel(job)
        except Exception:
            pass

    def _cancel_jobs(self):
        for job in list(self._after_jobs):
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._after_jobs.clear()

    def _card(self, parent, **kw):
        return tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                        highlightthickness=1, **kw)

    # ── Elrendezés: panel-sorrend, dokkolás, perzisztencia ──────────────────

    PANEL_KEYS = ("search", "watches", "results")

    def _load_layout(self):
        """Az elrendezés visszaolvasása a settings.db-ből.

        A hiányzó vagy ismeretlen kulcsokat eldobjuk, a panellistát a
        `PANEL_KEYS` egészével egészítjük ki, így egy új verzió hozzáadott
        panele sem töri el a mentett elrendezést.
        """
        data = {}
        raw = load_setting(LAYOUT_KEY, "")
        if raw:
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                data = {}
        if not isinstance(data, dict):
            data = {}

        order = [k for k in data.get("order", []) if k in self.PANEL_KEYS]
        for key in self.PANEL_KEYS:
            if key not in order:
                order.append(key)

        collapsed = {k for k in data.get("collapsed", []) if k in order}
        dock = data.get("log")
        if dock not in LOG_DOCKS:
            dock = "right"
        side = data.get("results") == "side"
        return order, collapsed, dock, side

    def _save_layout(self):
        save_setting(LAYOUT_KEY, json.dumps({
            "order": list(self._panel_order),
            "collapsed": sorted(k for k, p in self._panels.items()
                                if p._collapsed),
            "log": self._log_dock,
            "results": "side" if self._results_side else "stack",
        }, ensure_ascii=False))

    def _reset_layout(self):
        """Visszaállítás az alapértelmezett sorrendre és dokkolásra."""
        self._results_side = False
        self._results_side_var.set(False)
        self._apply_results_mode()
        self._order = list(self.PANEL_KEYS)
        self._panel_order = [k for k in self._order if k in self._panels]
        for panel in self._panels.values():
            panel.set_collapsed(False)
            panel.release_height()
        self._log_dock = self._last_dock = "right"
        self._dock_var.set("right")
        self._apply_panel_order()
        self._apply_log_dock()
        self._save_layout()
        self._log("[dim] Elrendezés visszaállítva az alapértelmezettre.")
        self.reload_results()

    def _apply_panel_order(self):
        panels = [self._panels[k] for k in self._panel_order if k in self._panels]
        for panel in panels:
            panel.pack_forget()
        for panel in panels:
            panel.pack(fill=X, pady=(0, 12))
        self._layout_changed()

    # ── Húzogatás ──────────────────────────────────────────────────────────

    def _panel_drag_start(self, panel, _event):
        if self._closing:
            return
        self._drag = {"key": panel.key, "insert": None}
        self._drag_line = tk.Frame(panel.master, bg=ACCENT, height=3,
                                   highlightthickness=0, borderwidth=0)
        self._panel_drag_move()

    def _panel_drag_move(self, y=None):
        """A behúzott kék vonal jelzi, hová csapódna a panel."""
        if not self._drag or self._drag_line is None or self._closing:
            return
        if y is None:
            y = self.root.winfo_pointery()
        panels = [self._panels[k] for k in self._panel_order]
        index = self._drop_index(y)
        self._drag["insert"] = index
        self._drag_line.pack_forget()
        try:
            if index >= len(panels):
                self._drag_line.pack(after=panels[-1], fill=X, pady=(4, 2))
            else:
                self._drag_line.pack(before=panels[index], fill=X, pady=(4, 2))
        except tk.TclError:
            pass

    def _panel_drag_end(self):
        if not self._drag:
            return
        drag = self._drag
        index = drag.get("insert")
        key = drag["key"]
        self._panel_drag_cancel()
        if index is None:
            return
        order = list(self._panel_order)
        current = order.index(key)
        if current == index or current == index - 1:
            return                      # nincs valódi mozgás
        order.pop(current)
        order.insert(index - 1 if index > current else index, key)
        self._panel_order = order
        # a "results" a side-módban nincs a sorban — a kanonikus elrendezés
        # (self._order) viszont mindig megtartja, hogy visszatéréskor
        # ugyanide kerüljön
        self._order = list(order) + [k for k in self._order
                                     if k not in order]
        self._apply_panel_order()
        self._save_layout()
        labels = {"search": "szűrők", "watches": "figyelések",
                  "results": "találatok"}
        self._log("[dim] Elrendezés: " + " → ".join(
            labels.get(k, k) for k in order))

    def _panel_drag_cancel(self):
        if self._drag_line is not None:
            try:
                self._drag_line.pack_forget()
                self._drag_line.destroy()
            except tk.TclError:
                pass
        self._drag_line = None
        self._drag = None

    def _drop_index(self, y):
        """Hová kerül a panel: hányadik helyre csapódna a `y` kurzorpozícióban."""
        for index, key in enumerate(self._panel_order):
            panel = self._panels.get(key)
            if panel is None:
                continue
            if y < panel.winfo_rooty() + panel.winfo_height() // 2:
                return index
        return len(self._panel_order)

    # ── A napló dokkolása ──────────────────────────────────────────────────

    def _build_dock_menu(self, parent):
        self.dock_btn = ttk.Menubutton(parent, text="⇄", style="Link.TButton",
                                       width=3)
        self.dock_menu = tk.Menu(self.dock_btn, tearoff=0)
        for value, label in (("right", "Napló → jobbra"),
                             ("left", "Napló → balra"),
                             ("bottom", "Napló → alulra"),
                             ("hidden", "Napló elrejtve")):
            self.dock_menu.add_radiobutton(
                label=label, variable=self._dock_var, value=value,
                command=self._set_log_dock)
        self.dock_menu.add_separator()
        self.dock_menu.add_checkbutton(
            label="Találatok ⇄ napló (jobbra)",
            variable=self._results_side_var, command=self._results_side_from_var)
        self.dock_menu.add_command(
            label="Elrendezés visszaállítása", command=self._reset_layout)
        self.dock_btn.configure(menu=self.dock_menu)
        self.dock_btn.pack(side=RIGHT, padx=(0, 6))

    def _set_log_dock(self, dock=None):
        dock = dock or self._dock_var.get()
        if dock not in LOG_DOCKS:
            return
        self._log_dock = dock
        self._dock_var.set(dock)
        if dock != "hidden":
            self._last_dock = dock
        self._apply_log_dock()
        self._save_layout()
        names = {"right": "jobbra", "left": "balra", "bottom": "alulra",
                 "hidden": "elrejtve"}
        self._log(f"[dim] A napló most {names.get(dock, dock)} van dokkolva.")

    def _apply_log_dock(self):
        widgets = [self.log_col, self.log_rail, self.canvas_area]
        if self._results_col is not None:
            widgets.append(self._results_col)
        for widget in widgets:
            try:
                widget.pack_forget()
            except tk.TclError:
                pass

        dock = self._log_dock
        if self._results_side:
            # a jobb oszlop a találatoké, a napló nem fér el mellette oldalt
            if dock in ("right", "left"):
                dock = "bottom"
        if dock == "hidden":
            self.log_rail.pack(side=RIGHT, fill=Y)
        elif dock == "bottom":
            self.log_col.configure(width=1, height=LOG_H)
            self.log_col.pack(side=BOTTOM, fill=X, pady=(0, 12))
        elif dock == "left":
            self.log_col.configure(width=LOG_W, height=1)
            self.log_col.pack(side=LEFT, fill=Y, padx=(0, 12))
        else:                                       # right
            self.log_col.configure(width=LOG_W, height=1)
            self.log_col.pack(side=RIGHT, fill=Y, padx=(12, 0))

        if self._results_side and self._results_col is not None:
            # a találati oszlop a jobb szélre, a vászon a maradékba
            self._results_col.pack(side=RIGHT, fill=Y, padx=(12, 12))
        self.canvas_area.pack(side=LEFT, fill=BOTH, expand=True)
        self._layout_changed()

    # ── Találatok ⇄ napló ──────────────────────────────────────────────────

    def _results_side_from_var(self):
        """A dokk-menü checkbutton-jából — a változó már átbillent."""
        if self._closing:
            return
        self._set_results_side(bool(self._results_side_var.get()))

    def toggle_results_side(self):
        """Fejléc-gomb: a találatok átkerülnek a jobb (magas) oszlopba."""
        self._set_results_side(not self._results_side)

    def _set_results_side(self, side):
        if side == self._results_side:
            self._results_side_var.set(side)   # csak szinkron
            return
        self._results_side = side
        self._results_side_var.set(side)
        self._apply_results_mode()
        self._save_layout()
        where = "jobbra helyezve (a napló alulra került)." if side \
            else "visszahelyezve a bal oldali listába."
        self._log(f"[dim] Találatok {where}")
        self.reload_results()

    def _apply_results_mode(self):
        """A találati kártya újraépítése a jelenlegi mód szerint."""
        # — lebontás —
        panel = self._panels.pop("results", None)
        if panel is not None:
            try:
                panel.destroy()
            except tk.TclError:
                pass
        if self._results_col is not None:
            for child in list(self._results_col.winfo_children()):
                try:
                    child.destroy()
                except tk.TclError:
                    pass
        self.results = None
        self.result_count = None
        self.swap_btn = None
        self._panel_order = [k for k in self._panel_order if k in self._panels]

        # — építés a célhelyre —
        if self._results_side:
            self._build_results_side_card()
        else:
            self._build_results_card()
            self._panel_order = [k for k in self._order if k in self._panels]

        self._apply_panel_order()
        self._apply_log_dock()

    def _build_ui(self):
        header = tk.Frame(self.root, bg=HEADER_BG)
        header.pack(fill=X)
        inner = tk.Frame(header, bg=HEADER_BG, padx=22, pady=14)
        inner.pack(fill=X)

        brand = tk.Frame(inner, bg=HEADER_BG)
        brand.pack(side=LEFT)
        dot = tk.Canvas(brand, width=14, height=14, bg=HEADER_BG,
                        highlightthickness=0)
        dot.create_oval(2, 2, 12, 12, fill=ACCENT, outline="")
        dot.pack(side=LEFT, padx=(0, 10))
        tk.Label(brand, text="VINTED PRICE MONITOR", bg=HEADER_BG, fg="#FFFFFF",
                 font=(FONT, 15, "bold")).pack(side=LEFT)
        tk.Label(inner, text="Valós idejű keresés · szűrők · Discord értesítések",
                 bg=HEADER_BG, fg=HEADER_SUB, font=(FONT, 9)).pack(side=RIGHT,
                                                                    padx=(0, 14))

        page = tk.Frame(self.root, bg=BG, padx=18)
        page.pack(fill=BOTH, expand=True, pady=(0, 12))

        # A napló oszlopát a dokkolás helye határozza meg; a többi felület
        # a jobb/bal szélen van, fölötte a görgethető lap.
        self.log_col = tk.Frame(page, bg=BG, width=LOG_W)
        self.log_col.pack_propagate(False)
        self.log_rail = tk.Frame(page, bg=BG, width=LOG_HINT)
        self.log_rail.pack_propagate(False)
        self._results_col = tk.Frame(page, bg=BG, width=LOG_W)
        self._results_col.pack_propagate(False)

        self.canvas_area = tk.Frame(page, bg=BG)
        self._build_scroll_area()
        self._build_action_bar()

        # a görgethető lap: minden bal oldali kártya ebbe kerül
        self.left_col = self.content

        self._build_search_card()
        self._build_watch_card()
        self._build_log_panel()
        self._build_rail()

        # a találatok a mentett elrendezés szerint: a bal oldali sorba
        # (Panel), vagy a jobb, teljes magasságú oszlopba
        if self._results_side:
            self._build_results_side_card()
        else:
            self._build_results_card()

        self._panel_order = [k for k in self._order if k in self._panels]
        self._apply_panel_order()
        for key in self._collapsed_panels:
            panel = self._panels.get(key)
            if panel:
                panel.set_collapsed(True)
        self._apply_log_dock()
        self._build_statusbar()

    # ── Görgethető lap ─────────────────────────────────────────────────────

    def _build_scroll_area(self):
        self.vscroll = Scrollbar(
            self.canvas_area, orient="vertical", command=self.canvas_yview,
            bg=BG, troughcolor=BG, activebackground=ACCENT, borderwidth=0,
            relief="flat", width=12)
        self.canvas = Canvas(self.canvas_area, bg=BG, highlightthickness=0,
                             borderwidth=0, takefocus=0)
        self.canvas.configure(yscrollcommand=self._on_scroll_region)
        self.content = tk.Frame(self.canvas, bg=BG)
        self._canvas_win = self.canvas.create_window(0, 0, window=self.content,
                                                     anchor=NW)

        self.canvas.pack(side=TOP, fill=BOTH, expand=True)
        self.content.bind("<Configure>", self._on_content_resize)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        # A root toplevel-címkéjére kötjük, nem `bind_all`-ra: így a
        # beállítások és a felugró ablakok fölött nem görgetne a lap.
        self.root.bind("<MouseWheel>", self._on_wheel)

    def canvas_yview(self, *args):
        if not self._closing:
            try:
                self.canvas.yview(*args)
            except tk.TclError:
                pass

    def _on_scroll_region(self, first, last):
        """A görgetősáv csak akkor látszik, ha tényleg van mit görgetni."""
        if self._closing:
            return
        try:
            first, last = float(first), float(last)
        except (TypeError, ValueError):
            return
        fits = first <= 0.0 and last >= 1.0
        try:
            shown = bool(self.vscroll.winfo_manager())
        except tk.TclError:
            return
        if fits and shown:
            self.vscroll.pack_forget()
        elif not fits and not shown:
            self.vscroll.pack(side=RIGHT, fill=Y, padx=(8, 0), before=self.canvas)
        if not fits:
            self.vscroll.set(first, last)

    def _on_content_resize(self, _event=None):
        if self._closing:
            return
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except tk.TclError:
            pass

    def _on_canvas_resize(self, event):
        if self._closing:
            return
        try:
            self.canvas.itemconfigure(self._canvas_win, width=event.width)
        except tk.TclError:
            return
        self._fit_results(event.height)

    def _on_wheel(self, event):
        """A lap görgetése a mouse wheelrel.

        A `Text` (napló) és a `Treeview` (találatok) maga görget, fölöttük
        ezért nem nyúlunk hozzá — különben egy kattintás kétszer menne.
        """
        if self._closing:
            return
        try:
            if event.widget.winfo_class() in _WHEEL_OWNERS:
                return
            steps = event.delta / 120.0 * WHEEL_STEP
            if 0 < abs(steps) < WHEEL_STEP:
                steps = WHEEL_STEP if steps > 0 else -WHEEL_STEP
            if steps:
                self.canvas.yview_scroll(-int(steps), "units")
        except (AttributeError, tk.TclError):
            pass

    def _fit_results(self, available):
        """A maradék helyet a találati táblázat kapja meg."""
        if self._fitting or self._closing:
            return
        panel = self._panels.get("results")
        if panel is None or available <= 1:
            return
        self._fitting = True
        try:
            used = sum(p.winfo_reqheight() for k, p in self._panels.items()
                       if k != "results" and not p._collapsed)
            budget = available - used - 16
            # A táblázat pontosan a maradék helyet kapja (min. RESULTS_MIN),
            # így alapablaknál nincs sikamlós 20-30 px-es görgetés; ha az
            # ablak kicsi, akkor viszont a lap görgethető marad.
            height = min(max(budget, RESULTS_MIN), RESULTS_MAX)
            if abs(height - panel.winfo_height()) >= 4:
                panel.set_body_height(height)
        finally:
            self._fitting = False

    def _layout_changed(self):
        """Panel mozgatása/összecsukása után újra kell számolni a méreteket."""
        self._on_content_resize()
        try:
            self._fit_results(self.canvas.winfo_height())
        except tk.TclError:
            pass

    # ── Search card ─────────────────────────────────────────────────────────

    def _add_panel(self, key, title):
        """Létrehoz egy újrarendezhető kártyát a bal oldali panelek közé."""
        panel = Panel(self.left_col, self, key, title)
        self._panels[key] = panel
        return panel

    # ── Search card ─────────────────────────────────────────────────────────

    def _build_search_card(self):
        card = self._add_panel("search", "Kereső szűrők")
        body = card.body

        r1 = tk.Frame(body, bg=CARD)
        r1.pack(fill=X, pady=(0, 12))
        ttk.Label(r1, text="Kulcsszó", style="Field.TLabel").pack(anchor=W)
        self.search_text_var = StringVar()
        self.search_entry = ttk.Entry(r1, textvariable=self.search_text_var)
        self.search_entry.pack(fill=X, pady=(4, 0))

        r1b = tk.Frame(body, bg=CARD)
        r1b.pack(fill=X, pady=(0, 12))
        ttk.Label(r1b, text="Rendezés", style="Field.TLabel").pack(anchor=W)
        self.order_var = StringVar(value=ORDER_OPTIONS[0][1])
        self.order_combo = ttk.Combobox(
            r1b, textvariable=self.order_var, state="readonly",
            values=[label for _, label in ORDER_OPTIONS])
        self.order_combo.pack(side=LEFT, fill=X, expand=True, pady=(4, 0))
        self.order_var.trace_add("write", lambda *a: self._check_order_hint())

        r2 = tk.Frame(body, bg=CARD)
        r2.pack(fill=X, pady=(0, 12))
        ttk.Label(r2, text="Ár (Ft)", style="Field.TLabel").pack(anchor=W)
        price_row = tk.Frame(r2, bg=CARD)
        price_row.pack(fill=X, pady=(4, 0))
        self.price_from_var = StringVar()
        self.price_to_var = StringVar()
        self.min_price_var = StringVar(value="0")
        ttk.Entry(price_row, textvariable=self.price_from_var, width=11).pack(side=LEFT)
        tk.Label(price_row, text="—", bg=CARD, fg=MUTED).pack(side=LEFT, padx=8)
        ttk.Entry(price_row, textvariable=self.price_to_var, width=11).pack(side=LEFT)
        ttk.Label(price_row, text="from · to", style="Muted.TLabel").pack(
            side=LEFT, padx=(10, 0))

        tk.Frame(price_row, bg=BORDER, width=1).pack(side=LEFT, fill=Y, padx=10)
        ttk.Label(price_row, text="Helyi min.", style="Field.TLabel").pack(side=LEFT)
        ttk.Entry(price_row, textvariable=self.min_price_var, width=8).pack(
            side=LEFT, padx=(8, 0))
        ttk.Label(price_row, text="Ft felett küldj", style="Muted.TLabel").pack(
            side=LEFT, padx=(8, 0))
        tk.Frame(price_row, bg=BORDER, width=1).pack(side=LEFT, fill=Y, padx=10)

        self.clear_btn = ttk.Button(price_row, text="Szűrők törlése",
                                    style="Ghost.TButton",
                                    command=self.clear_filters)
        self.clear_btn.pack(side=LEFT, padx=(4, 0))

        r2b = tk.Frame(body, bg=CARD)
        r2b.pack(fill=X, pady=(10, 0))
        ttk.Label(r2b, text="Oldalak", style="Field.TLabel").pack(side=LEFT)
        self.pages_var = IntVar(value=1)
        ttk.Spinbox(r2b, from_=1, to=10, width=4, textvariable=self.pages_var).pack(
            side=LEFT, padx=(8, 4))
        ttk.Label(r2b, text="× 96 tétel / ciklus", style="Muted.TLabel").pack(side=LEFT)
        tk.Label(r2b, text="·", bg=CARD, fg=MUTED).pack(side=LEFT, padx=10)
        self.desktop_var = BooleanVar(value=load_bool(DESKTOP_KEY, True))
        ttk.Checkbutton(r2b, text="Asztali értesítés", variable=self.desktop_var,
                        style="Card.TCheckbutton").pack(side=LEFT)

        self.order_hint = tk.Label(
            body,
            text=("ℹ A „Relevancia” rendezésnél az első oldal nem mozdul, "
                  "ezért új terméket nem fogsz látni. Válts „Legújabb”-ra!"),
            bg=AMBER_LT, fg=AMBER, font=(FONT, 9), anchor=W,
            padx=10, pady=6, wraplength=640, justify="left")
        self.order_hint.pack_forget()

        r3 = tk.Frame(body, bg=CARD)
        r3.pack(fill=X, pady=(12, 0))
        self.chips = tk.Frame(r3, bg=CARD)
        self.chips.pack(fill=X)
        self._rebuild_chips()

        r4 = tk.Frame(body, bg=CARD)
        r4.pack(fill=X, pady=(14, 0))
        self.discount_var = BooleanVar(value=False)
        self.favourite_var = BooleanVar(value=False)
        self.handicraft_var = BooleanVar(value=False)
        self.give_away_var = BooleanVar(value=False)
        for var, text in (
            (self.discount_var, "Kedvezményes"),
            (self.favourite_var, "Csak kedvencek"),
            (self.handicraft_var, "Kézműves"),
            (self.give_away_var, "Ingyen elvihető"),
        ):
            ttk.Checkbutton(r4, text=text, variable=var,
                            style="Card.TCheckbutton").pack(side=LEFT, padx=(0, 18))

        for var in (self.search_text_var, self.price_from_var,
                    self.price_to_var, self.min_price_var, self.order_var,
                    self.pages_var, self.discount_var, self.favourite_var,
                    self.handicraft_var, self.give_away_var):
            var.trace_add("write", lambda *a: self._update_clear_btn())

        # beállítási változók (a Beállítások ablak ezeket használja)
        self.discord_var = BooleanVar(value=True)
        self.keep_history_var = BooleanVar(value=load_bool(KEEP_HISTORY_KEY, False))
        self.interval_var = IntVar(value=int(load_setting("interval_min", "7") or 7))
        self.max_alerts_var = IntVar(value=int(load_setting("max_alerts", "15") or 15))
        self.webhook_var = StringVar(value=load_setting(DISCORD_KEY, ""))
        self._webhook_saved = self.webhook_var.get()
        self._webhook_save_job = None
        self.webhook_var.trace_add("write", self._on_webhook_changed)

    def _check_order_hint(self):
        """Figyelmeztet, ha a rendezés miatt nem érkeznek új termékek."""
        if self.order_var.get() == ORDER_OPTIONS[1][1]:
            self.order_hint.pack_forget()
        else:
            self.order_hint.pack(fill=X, pady=(10, 0), before=self.chips.master)

    # ── Watches card ────────────────────────────────────────────────────────

    def _build_watch_card(self):
        card = self._add_panel("watches", "Figyelések")
        body = card.body
        head = card.header
        self.watch_count = tk.Label(head, text="(0)", bg=CARD, fg=MUTED,
                                    font=(FONT, 9))
        self.watch_count.pack(side=RIGHT, padx=(0, 6))
        ttk.Button(head, text="+ Hozzáadás", style="Link.TButton",
                   command=self.add_watch).pack(side=RIGHT)

        self.watch_box = tk.Frame(body, bg=CARD)
        self.watch_box.pack(fill=X)
        self.watch_empty = tk.Label(
            self.watch_box,
            text="Még nincs figyelés. Állítsd be a szűrőket, majd „+ Hozzáadás”.",
            bg=CARD, fg=MUTED, font=(FONT, 9), anchor=W)
        self.watch_empty.pack(fill=X, pady=(2, 0))

    def _render_watches(self):
        for w in self.watch_box.winfo_children():
            w.destroy()
        self.watch_count.configure(text=f"({len(self.watches)})")
        if not self.watches:
            self.watch_empty = tk.Label(
                self.watch_box,
                text="Még nincs figyelés. Állítsd be a szűrőket, majd „+ Hozzáadás”.",
                bg=CARD, fg=MUTED, font=(FONT, 9), anchor=W)
            self.watch_empty.pack(fill=X, pady=(2, 0))
            return

        wrap = max(200, self.watch_box.winfo_width() - 24)
        for idx, w in enumerate(self.watches):
            row = tk.Frame(self.watch_box, bg=CARD)
            row.pack(fill=X, pady=3)
            bg = ACCENT_LT if idx == self._editing else CARD

            top = tk.Frame(row, bg=bg)
            top.pack(fill=X)
            ttk.Button(top, text="✕", style="Link.TButton", width=3,
                       command=lambda i=idx: self.remove_watch(i)).pack(side=RIGHT)
            ttk.Button(top, text="✎", style="Link.TButton", width=3,
                       command=lambda i=idx: self.edit_watch(i)).pack(side=RIGHT)
            tk.Label(top, text=w["name"], bg=bg, fg=TEXT,
                     font=(FONT, 10, "bold"), anchor=W).pack(
                side=LEFT, fill=X, expand=True)
            detail_bits = [f"{w.get('pages') or 1} oldal",
                           f"min. {int(w.get('min_price') or 0)} Ft"]
            if not w.get("discord"):
                detail_bits.append("discord: ki")
            tk.Label(top, text=" · ".join(detail_bits), bg=bg, fg=MUTED,
                     font=(FONT, 9), anchor=E).pack(side=RIGHT, padx=(8, 0))

            desc = (w.get("desc") or "").strip()
            if not desc:
                desc = self._describe_filters(w) or "nem szűrt keresés"
            tk.Label(row, text=desc, bg=bg, fg=MUTED, font=(FONT, 9),
                     anchor=W, justify=LEFT, wraplength=wrap).pack(
                fill=X, pady=(1, 4), padx=2)

    def add_watch(self):
        params = self._collect_params()
        if not params:
            self._log("[warn] Nincs szűrő kiválasztva — adj meg legalább egyet.")
            return
        summary = self._form_summary_text()
        WatchDialog(
            self.root, "Új figyelés",
            self._next_watch_name(), summary,
            info="Elmentésre kerül: " + summary,
            on_save=lambda name, desc: self._finish_add(params, name, desc))

    def _finish_add(self, params, name, desc):
        name = name or self._next_watch_name()
        watch = {
            "name": name,
            "desc": desc,
            "params": params,
            "url": build_search_url(params),
            "min_price": self._read_min_price(),
            "pages": self._read_pages(),
            "discord": self.discord_var.get(),
            "desktop": self.desktop_var.get(),
        }
        self.watches.append(watch)
        self._editing = None
        self._render_watches()
        self._save_watches()
        self._log(f"[ok] Figyelés hozzáadva: „{name}” — {watch['pages']} oldal, "
                  f"min. {int(watch['min_price'])} Ft")
        if watch["desc"]:
            self._log(f"[dim] Leírás: {watch['desc']}")

    def edit_watch(self, idx):
        if not (0 <= idx < len(self.watches)):
            return
        w = self.watches[idx]
        self._load_params_into_form(w.get("params") or {})
        self._editing = idx
        self.min_price_var.set(str(int(w["min_price"])))
        self.pages_var.set(w["pages"])
        self._render_watches()
        self._log(f"[i] „{w['name']}” szerkesztése — "
                  "a szűrők az űrlapba lettek betöltve.")
        summary = self._form_summary_text()
        WatchDialog(
            self.root, "Figyelés szerkesztése",
            w.get("name") or "", w.get("desc") or "",
            info="A mentéskor ez kerül elmentésre: " + summary,
            on_save=lambda name, desc: self._finish_edit(idx, name, desc),
            on_cancel=self._cancel_edit)

    def _cancel_edit(self):
        self._editing = None
        self._render_watches()

    def _finish_edit(self, idx, name, desc):
        if not (0 <= idx < len(self.watches)):
            return
        w = self.watches[idx]
        params = self._collect_params()
        if not params:
            params = w.get("params") or {}
        w.update({
            "name": name or w["name"],
            "desc": desc,
            "params": params,
            "url": build_search_url(params),
            "min_price": self._read_min_price(),
            "pages": self._read_pages(),
            "discord": self.discord_var.get(),
            "desktop": self.desktop_var.get(),
        })
        self._editing = None
        self._render_watches()
        self._save_watches()
        self._log(f"[ok] Figyelés frissítve: „{w['name']}”")
        if w["desc"]:
            self._log(f"[dim] Leírás: {w['desc']}")

    def remove_watch(self, idx):
        if not (0 <= idx < len(self.watches)):
            return
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("Figyelem",
                                   "A monitor fut — először állítsd le.")
            return
        name = self.watches[idx]["name"]
        self.watches.pop(idx)
        if self._editing == idx:
            self._editing = None
        elif self._editing is not None and self._editing > idx:
            self._editing -= 1
        self._render_watches()
        self._save_watches()
        self._log(f"[dim] Figyelés törölve: „{name}”")

    def _next_watch_name(self):
        return f"Figyelés {len(self.watches) + 1}"

    # ── Leírás-generálás ────────────────────────────────────────────────────

    _PARAM_KEYS = ("search_text", "order", "price_from", "price_to",
                   "discount", "favourite", "handicraft", "give_away")

    def _price_range_text(self, params):
        def fmt(value):
            try:
                return f"{int(float(value)):,}".replace(",", " ")
            except (TypeError, ValueError):
                return str(value)
        frm = (params.get("price_from") or "").strip()
        to = (params.get("price_to") or "").strip()
        if frm and to:
            return f"{fmt(frm)}–{fmt(to)} Ft"
        if frm:
            return f"min. {fmt(frm)} Ft"
        if to:
            return f"max. {fmt(to)} Ft"
        return ""

    def _describe_filters(self, watch):
        """A szűrők rövid összefoglalója (a leírás sora, ha nincs saját)."""
        p = watch.get("params") or {}
        parts = []
        kw = (p.get("search_text") or "").strip()
        if kw:
            parts.append(f"„{kw}”")
        price = self._price_range_text(p)
        if price:
            parts.append(price)
        n = sum(len(v) for k, v in p.items()
                if k not in self._PARAM_KEYS
                and isinstance(v, (list, tuple)))
        if n:
            parts.append(f"{n} szűrő")
        extras = []
        for key, label in (("discount", "kedvezmény"), ("favourite", "kedvenc"),
                           ("handicraft", "kézműves"), ("give_away", "ajándék")):
            if p.get(key):
                extras.append(label)
        if extras:
            parts.append(" + ".join(extras))
        return " · ".join(parts)

    def _describe_watch(self, watch):
        """Teljes összefoglaló: szűrők + oldalak + min. ár + értesítések."""
        base = self._describe_filters(watch)
        parts = [base] if base else []
        parts.append(f"{watch.get('pages') or 1} oldal")
        if watch.get("min_price"):
            parts.append(f"min. {int(watch['min_price'])} Ft")
        if watch.get("discord"):
            parts.append("Discord")
        if watch.get("desktop"):
            parts.append("asztali jelzés")
        return " · ".join(parts)

    def _form_summary_text(self):
        """A fő űrlap pillanatnyi tartalomjegyzéke a párbeszédhez."""
        return self._describe_watch({
            "params": self._collect_params() or {},
            "min_price": self._read_min_price(),
            "pages": self._read_pages(),
            "discord": self.discord_var.get(),
            "desktop": self.desktop_var.get(),
        })

    # ── Results card ────────────────────────────────────────────────────────

    def _build_results_card(self):
        card = self._add_panel("results", "Találatok")
        self._build_results_header(card.header, self._swap_btn_label())
        self._build_results_body(card.body)

    def _build_results_side_card(self):
        """A találatok a jobb oldali, teljes magasságú oszlopban."""
        card = self._card(self._results_col)
        card.pack(fill=BOTH, expand=True)
        head = tk.Frame(card, bg=CARD)
        head.pack(fill=X, padx=16, pady=(12, 0))
        tk.Label(head, text="TALÁLATOK", bg=CARD, fg=ACCENT_DK,
                 font=(FONT, 10, "bold")).pack(side=LEFT)
        body = tk.Frame(card, bg=CARD, padx=16, pady=12)
        body.pack(fill=BOTH, expand=True)
        self._build_results_header(head, self._swap_btn_label())
        self._build_results_body(body)

    def _swap_btn_label(self):
        return "⇄ vissza" if self._results_side else "⇄ jobbra"

    def _build_results_header(self, head, swap_label):
        self.result_count = tk.Label(head, text="0 sor", bg=CARD, fg=MUTED,
                                     font=(FONT, 9))
        self.result_count.pack(side=RIGHT, padx=(0, 6))
        ttk.Button(head, text="Frissítés", style="Link.TButton",
                   command=self.reload_results).pack(side=RIGHT)
        ttk.Button(head, text="Ürítés", style="Link.TButton",
                   command=self._clear_results).pack(side=RIGHT, padx=(0, 6))
        self.swap_btn = ttk.Button(head, text=swap_label, style="Link.TButton",
                                   command=self.toggle_results_side)
        self.swap_btn.pack(side=RIGHT, padx=(0, 6))

    def _build_results_body(self, body):
        self.results = ResultsPanel(body, on_open=self._open_item)
        ttk.Label(body, text="dupla kattintás = termék megnyitása böngészőben",
                  style="Muted.TLabel", anchor=W).pack(fill=X, pady=(6, 0))

    def _open_item(self, url):
        self._log(f"[dim] Megnyitom: {url}")
        try:
            webbrowser.open(url)
        except Exception as e:
            self._log(f"[err] Nem sikerült megnyitni: {e}")

    def _clear_results(self):
        self.results.clear()
        self._found = 0
        self.badge.configure(text="0 új találat")
        self.result_count.configure(text="0 sor")
        self._log("[dim] A találati táblázat ürítve (az adatbázis érintetlen).")

    def reload_results(self):
        """Újratölti a táblázatot az adatbázisból."""

        def worker():
            try:
                conn = init_db()
                try:
                    total = count_listings(conn)
                    rows = fetch_recent(conn, 400)
                finally:
                    conn.close()
            except Exception as e:
                self._result_q.put(("err", str(e)))
                return
            self._result_q.put(("rows", rows, total))

        threading.Thread(target=worker, daemon=True).start()

    # ── Action bar & log ────────────────────────────────────────────────────

    def _build_action_bar(self):
        actions = tk.Frame(self.canvas_area, bg=BG)
        self.action_bar = actions
        actions.pack(side=BOTTOM, fill=X, pady=(14, 0))
        self.start_btn = ttk.Button(actions, text="▶  Indítás", style="Accent.TButton",
                                    command=self.toggle_monitoring)
        self.start_btn.pack(side=LEFT)
        self.reset_btn = ttk.Button(actions, text="Adatbázis törlése",
                                    style="Soft.TButton", command=self.reset_data)
        self.reset_btn.pack(side=LEFT, padx=(10, 0))
        ttk.Button(actions, text="⚙  Beállítások", style="Ghost.TButton",
                   command=self._open_settings).pack(side=RIGHT)

    def _build_log_panel(self):
        log_card = self._card(self.log_col)
        log_card.pack(fill=BOTH, expand=True)
        lg = tk.Frame(log_card, bg=CARD, padx=16, pady=16)
        lg.pack(fill=BOTH, expand=True)

        head = tk.Frame(lg, bg=CARD)
        head.pack(fill=X, pady=(0, 10))
        tk.Label(head, text="ESEMÉNYNAPLÓ", bg=CARD, fg=ACCENT_DK,
                 font=(FONT, 10, "bold")).pack(side=LEFT)
        ttk.Button(head, text="−  Elrejt", style="Soft.TButton", width=8,
                   command=self._toggle_log).pack(side=RIGHT)
        self._build_dock_menu(head)

        self.log_text = Text(
            lg, wrap="word", state="disabled",
            bg=CARD, fg=TEXT, insertbackground=TEXT,
            font=(FONT, 9), relief="flat", padx=8, pady=8,
            highlightbackground=BORDER, highlightthickness=1,
        )
        log_scroll = Scrollbar(lg, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side=RIGHT, fill=Y)
        self.log_text.pack(fill=BOTH, expand=True)
        self._setup_log_tags()
        self.log_text.tag_configure("hint", foreground=ACCENT_DK)

        clear_row = tk.Frame(lg, bg=CARD)
        clear_row.pack(fill=X, pady=(10, 0))
        ttk.Button(clear_row, text="Napló ürítése", style="Soft.TButton",
                   command=self._clear_log).pack(side=LEFT)
        ttk.Button(clear_row, text="Mentés", style="Soft.TButton",
                   command=self._export_log).pack(side=RIGHT)

    def _build_rail(self):
        rail_card = self._card(self.log_rail)
        rail_card.pack(fill=BOTH, expand=True)
        ttk.Button(rail_card, text="Napló\n▸", style="Ghost.TButton",
                   command=self._toggle_log).pack(fill=X, padx=6, pady=6)

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=HEADER_BG)
        bar.pack(fill=X, side=tk.BOTTOM)
        inner = tk.Frame(bar, bg=HEADER_BG, padx=18, pady=9)
        inner.pack(fill=X)

        self.status_dot = tk.Canvas(inner, width=12, height=12, bg=HEADER_BG,
                                    highlightthickness=0)
        self._status_ball = self.status_dot.create_oval(2, 2, 10, 10,
                                                        fill="#8A98AC",
                                                        outline="")
        self.status_dot.pack(side=LEFT, padx=(0, 8))
        self.status_label = tk.Label(inner, text="Készenlét", bg=HEADER_BG,
                                     fg="#FFFFFF", font=(FONT, 9, "bold"))
        self.status_label.pack(side=LEFT)
        tk.Label(inner, text="·", bg=HEADER_BG, fg=HEADER_SUB).pack(side=LEFT,
                                                                    padx=8)
        self.status_sub = tk.Label(inner, text="válassz szűrőket, majd indítsd",
                                   bg=HEADER_BG, fg=HEADER_SUB, font=(FONT, 9))
        self.status_sub.pack(side=LEFT)

        self.badge = tk.Label(inner, text="0 új találat", bg=HEADER_BG,
                              fg=HEADER_SUB, font=(FONT, 9))
        self.badge.pack(side=RIGHT)

    def _export_log(self):
        """A napló mentése szövegfájlba."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "monitor_log.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log_text.get("1.0", END))
        except OSError as e:
            self._log(f"[err] Napló mentése sikertelen: {e}")
            return
        self._log(f"[ok] Napló mentve: {path}")

    def _open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.lift()
            self.settings_win.focus_force()
            return
        self.settings_win = SettingsWindow(self.root, self)
        self.settings_win.focus_force()

    def _toggle_log(self):
        """Ctrl+L: a napló elrejtése, illetve az előző dokkolás visszaállítása."""
        if self._log_dock == "hidden":
            self._set_log_dock(self._last_dock or "right")
        else:
            self._set_log_dock("hidden")

    def _set_status(self, color, text, sub=None):
        self.status_dot.itemconfig(self._status_ball, fill=color)
        self.status_label.configure(text=text)
        if sub is not None:
            self.status_sub.configure(text=sub)

    def _setup_log_tags(self):
        self.log_text.tag_configure("info", foreground=TEXT)
        self.log_text.tag_configure("dim", foreground=MUTED)
        self.log_text.tag_configure("ok", foreground=GREEN)
        self.log_text.tag_configure("warn", foreground=AMBER)
        self.log_text.tag_configure("err", foreground=RED)
        self.log_text.tag_configure("hl", foreground=ACCENT_DK,
                                    font=(FONT, 9, "bold"))

    def _classify(self, msg):
        low = msg.lower()
        if any(k in low for k in ("[err]", "error", "fatal", "failed",
                                  "exception", "sikertelen")):
            return "err"
        if any(k in low for k in ("[ok]", "new ", "betöltve", "hozzáadva")):
            return "ok"
        if any(k in low for k in ("[warn]", "skip", "fallback", "nincs találat",
                                  "árcsökkenés")):
            return "warn"
        if any(k in low for k in ("szűrő url", "szűrők betöltése", "target url",
                                  "kategóriafa", "figyelés", " indul ")):
            return "hl"
        if any(k in low for k in ("[dim]", "sleeping", "fetching", "monitoring",
                                  "ellenőrizve")):
            return "dim"
        return "info"

    def _log(self, message):
        self.log_queue.put(str(message))

    def _poll_log_queue(self):
        if self._closing:
            return
        while not self.log_queue.empty():
            self._append_log(self.log_queue.get_nowait())
        self._schedule(120, self._poll_log_queue)

    def _append_log(self, msg):
        tag = self._classify(msg)
        self.log_text.configure(state="normal")
        self.log_text.insert(END, f"{msg}\n", (tag,))
        # keep the widget from growing unbounded over a long session
        if int(self.log_text.index("end-1c").split(".")[0]) > 3000:
            self.log_text.delete("1.0", "300.0")
        self.log_text.see(END)
        self.log_text.configure(state="disabled")
        # a main_loop időbélyeggel prefixeli a sorokat, ezért tartalmazásra
        # kell keresni, nem `startswith`-ra
        if " NEW [" in msg:
            self._found += 1
            self.badge.configure(text=f"{self._found} új találat")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", END)
        self.log_text.configure(state="disabled")

    # ── Filters ─────────────────────────────────────────────────────────────

    def _flat_catalog(self, rebuild=False):
        """Kategórianevek keresőtáblája (egyszer épül fel, aztán cache)."""
        if self._catalog_lookup is None or rebuild:
            lookup = {}

            def walk(nodes):
                for n in nodes:
                    lookup[str(n.get("id"))] = n.get("title") or str(n.get("id"))
                    walk(n.get("catalogs") or [])

            walk(self.category_tree or [])
            self._catalog_lookup = lookup
        return self._catalog_lookup

    def _open_filter_popup(self, key):
        if key == "catalog":
            if not self.category_tree:
                messagebox.showinfo("Info", "Még nincs kategóriafa.")
                return
            popup = CategoryTreePopup(self.root, self.category_tree,
                                      self.selected.get("catalog") or [])
            self.root.wait_window(popup)
            if popup.result is not None:
                self.selected["catalog"] = [str(x) for x in popup.result]
                self._on_catalog_selected(getattr(popup, "catalog_url", None))
            return

        opts = self.filter_options.get(key) or []
        if not opts and key == "status":
            opts = STATUS_OPTIONS
        if not opts and key != "brand":
            messagebox.showinfo("Info", "Ehhez a szűrőhöz nincs betöltött opció.")
            return
        on_search = (lambda kw: fetch_brands(kw, self.selected.get("catalog") or [])
                     if key == "brand" else None)
        popup = MultiSelectPopup(self.root, self.facet_titles.get(key, key), opts,
                                 self.selected.get(key) or [],
                                 on_search=on_search)
        self.root.wait_window(popup)
        if popup.result is not None:
            self.selected[key] = [str(x) for x in popup.result]
            # a Vintedről megkeresett opciók megmaradnak a következő megnyitásnál
            if popup.found_pairs:
                known = {str(i) for i, _ in (self.filter_options.get(key) or [])}
                merged = list(self.filter_options.get(key) or [])
                for pair in popup.found_pairs:
                    if str(pair[0]) not in known:
                        merged.append(pair)
                        known.add(str(pair[0]))
                self.filter_options[key] = merged
            self._update_filter_button(key)
            self._update_clear_btn()

    def _add_chip(self, code, label):
        btn = ttk.Button(self.chips, text=label, style="Filter.TButton",
                         command=lambda k=code: self._open_filter_popup(k))
        btn.pack(side=LEFT, fill=X, expand=True, padx=(0, 6))
        self._filter_buttons[code] = btn
        self._chip_labels[code] = label

    def _rebuild_chips(self):
        for w in self.chips.winfo_children():
            w.destroy()
        self._filter_buttons = {}
        self._chip_labels = {}
        self._add_chip("catalog", "Kategóriák")
        for code in self.facet_codes:
            if code == "brand" or self.filter_options.get(code) or code == "status":
                self._add_chip(code, self.facet_titles.get(code, code))

    def _update_filter_button(self, key):
        """A chip feliratát a kiválasztott opciók rövidített névre hozza."""
        btn = self._filter_buttons.get(key)
        if btn is None:
            return
        base = self._chip_labels.get(key, key)
        if key == "catalog":
            lookup = self._flat_catalog()
        else:
            lookup = {str(k): v for k, v in (self.filter_options.get(key) or [])}
            if key == "status":
                lookup.update({str(k): v for k, v in STATUS_OPTIONS})

        titles = [lookup.get(str(i), str(i))
                  for i in (self.selected.get(key) or [])]
        if not titles:
            btn.configure(text=base)
            return
        brief = " + ".join(titles[:2])
        if len(titles) > 2:
            brief += f" … +{len(titles) - 2}"
        btn.configure(text=brief)

    def clear_filters(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning(
                "Figyelem", "A monitor fut — előbb állítsd le, "
                            "hogy módosíthass szűrőket.")
            return
        self.selected = {"catalog": []}
        self.filter_options = {}
        self.facet_titles = {}
        self.facet_codes = []
        self._catalog_url = None
        self._rebuild_chips()
        self.search_text_var.set("")
        self.price_from_var.set("")
        self.price_to_var.set("")
        self.min_price_var.set("0")
        self.pages_var.set(1)
        self.order_var.set(ORDER_OPTIONS[0][1])
        for var in (self.discount_var, self.favourite_var,
                    self.handicraft_var, self.give_away_var):
            var.set(False)
        self._log("[ok] Szűrők törölve — a kereső űrlap kiürítve.")
        self._set_status(GREEN, "Készenlét", "szűrők törölve")
        self._update_clear_btn()

    def _has_filters(self):
        if any(bool(v) for v in self.selected.values()):
            return True
        if self.search_text_var.get().strip():
            return True
        if self.price_from_var.get().strip() or self.price_to_var.get().strip():
            return True
        if self.order_var.get() != ORDER_OPTIONS[0][1]:
            return True
        return any(v.get() for v in (self.discount_var, self.favourite_var,
                                     self.handicraft_var, self.give_away_var))

    def _update_clear_btn(self):
        self.clear_btn.configure(
            state=("normal" if self._has_filters() else "disabled"))

    # ── Settings persistence ────────────────────────────────────────────────

    def _on_webhook_changed(self, *_args):
        """A webhook mező szerkesztése után mentjük, hogy a következő
        indításkor már betöltve legyen."""
        self._unschedule(self._webhook_save_job)
        self._webhook_save_job = self._schedule(600, self._save_webhook)

    def _save_webhook(self):
        self._webhook_save_job = None
        value = self.webhook_var.get().strip()
        if value == self._webhook_saved:
            return
        if save_setting(DISCORD_KEY, value):
            self._webhook_saved = value
            if value:
                self._log("[ok] Discord webhook elmentve (settings.db).")
            else:
                self._log("[dim] Discord webhook törölve a beállításokból.")

    def _save_watches(self):
        save_setting("watches", json.dumps(self.watches, ensure_ascii=False))

    def _load_watches(self):
        raw = load_setting("watches", "")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return data if isinstance(data, list) else []

    def _save_settings(self):
        """Minden beállítás kiírása — a Beállítások ablak bezárásakor."""
        self._save_webhook()
        self.keep_history_var.set(load_bool(KEEP_HISTORY_KEY,
                                            self.keep_history_var.get()))
        save_bool(KEEP_HISTORY_KEY, self.keep_history_var.get())
        save_bool(DESKTOP_KEY, self.desktop_var.get())
        try:
            save_setting("interval_min", str(int(self.interval_var.get())))
            save_setting("max_alerts", str(int(self.max_alerts_var.get())))
        except (tk.TclError, ValueError):
            pass
        self._save_watches()

    def _apply_history_policy(self):
        """Indításkori adatbázis-eljárás.

        Alapértelmezés szerint minden induláskor újrakezdünk (az indulás
        elején küldjön minden új termékről). Ha a felhasználó bekapcsolta az
        előzmények megőrzését, a `vinted_monitor.db` megmarad, és a GUI
        betölti belőle a korábbi találatokat.
        """
        if load_bool(KEEP_HISTORY_KEY, False):
            if os.path.isfile(DB_PATH):
                self._log("[dim] Előzmények megőrzése be van kapcsolva — "
                          "a vinted_monitor.db megmarad.")
            return
        if delete_db_file():
            self._log("[ok] Indításkor törölve: vinted_monitor.db")

    # ── Startup & workers ───────────────────────────────────────────────────

    def _startup_load(self):
        self._apply_history_policy()
        for var in (self.discount_var, self.favourite_var,
                    self.handicraft_var, self.give_away_var):
            var.set(False)
        self._update_clear_btn()
        self._check_order_hint()

        self.watches = self._load_watches()
        self._render_watches()
        if self.watches:
            self._log(f"[ok] {len(self.watches)} mentett figyelés betöltve.")

        self._loading_win = LoadingWindow(self.root, text="Kategóriák betöltése")
        threading.Thread(target=self._fetch_categories_worker,
                         daemon=True).start()

    def _fetch_categories_worker(self):
        try:
            tree = fetch_category_tree()
            self._filters_result_q.put(("tree", tree))
        except Exception as e:
            self._filters_result_q.put(("err", e))

    def _fetch_category_filters_worker(self, cat_url, cat_id):
        try:
            facets = fetch_category_filters(cat_url, cat_id)
            self._filters_result_q.put(("catfilters", facets))
        except Exception as e:
            self._filters_result_q.put(("err", e))

    def _close_loading(self):
        if self._loading_win and self._loading_win.winfo_exists():
            self._loading_win.destroy()
        self._loading_win = None

    def _on_catalog_selected(self, cat_url):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning(
                "Figyelem", "A monitor fut — először állítsd le, "
                            "hogy módosíthass kategóriát.")
            return
        keep = self.selected.get("catalog", [])
        self.selected = {"catalog": keep}
        self.filter_options = {}
        self.facet_titles = {}
        self.facet_codes = []
        self._rebuild_chips()
        self._update_filter_button("catalog")
        cid = (keep or [None])[0]
        self._catalog_url = cat_url if cid else None
        self._update_clear_btn()
        if not cid:
            return
        self._log(f"[i] Kategória szűrőinek betöltése (kategória #{cid})…")
        self._loading_win = LoadingWindow(self.root,
                                          text="Kategória szűrők betöltése",
                                          sub="Vinted szűrő-API lekérése…")
        self._set_status(AMBER, "Folyamatban", "kategória szűrőinek betöltése")
        threading.Thread(target=self._fetch_category_filters_worker,
                         args=(cat_url, cid), daemon=True).start()

    def _apply_category_filters(self, facets):
        facets = facets or {}
        self._close_loading()
        self._update_clear_btn()
        cat = self.selected.get("catalog", [])
        opts, titles, codes = {}, {}, []
        for code, meta in facets.items():
            if not isinstance(meta, dict):
                meta = {"title": code, "options": meta}
            pairs = meta.get("options") or []
            if not pairs and code not in ("brand", "status"):
                continue
            opts[code] = pairs
            titles[code] = meta.get("title") or code
            codes.append(code)
        self.filter_options = opts
        self.facet_titles = titles
        self.facet_codes = sorted(
            codes, key=lambda c: CHIP_ORDER.index(c) if c in CHIP_ORDER else 100)
        kept = {"catalog": cat}
        for code in codes:
            if code in self.selected:
                kept[code] = self.selected[code]
        self.selected = kept
        self._rebuild_chips()
        for code in self.selected:
            self._update_filter_button(code)
        total = sum(len(v) for v in opts.values())
        self._log(f"[ok] Kategória szűrők betöltve: {total} opció, "
                  f"{len(codes)} szűrő")
        for code in codes:
            if opts[code]:
                self._log(f"[ok]  • {titles[code]}: {len(opts[code])} opció")
        self._set_status(GREEN, "Készenlét", "kategória szűrői betöltve")

    def _poll_filters_result(self):
        if self._closing:
            return
        while not self._filters_result_q.empty():
            kind, *payload = self._filters_result_q.get_nowait()
            if kind == "tree":
                self._apply_category_tree(payload[0])
            elif kind == "catfilters":
                self._apply_category_filters(payload[0])
            else:
                self._filter_load_error(payload[0])
        self._schedule(120, self._poll_filters_result)

    def _apply_category_tree(self, tree):
        self._close_loading()
        self.category_tree = tree
        self._catalog_lookup = None
        self._update_filter_button("catalog")
        self._log(f"[ok] Kategóriafa betöltve: {len(tree)} gyökércsoport")
        self._log("[dim] A kategória szűrői a kategória kiválasztása után "
                  "jelennek meg a chip-sorban.")
        self._set_status(GREEN, "Készenlét", "kategóriák betöltve")

    def _filter_load_error(self, e):
        self._close_loading()
        self._update_clear_btn()
        self._log(f"[err] Szűrő betöltési hiba: {e}")
        self._set_status(RED, "Hiba", "próbáld újra a kategóriakiválasztást")

    # ── Params ──────────────────────────────────────────────────────────────

    def _read_min_price(self):
        raw = self.min_price_var.get().strip().replace(" ", "")
        try:
            return max(0.0, float(raw.replace(",", ".")))
        except ValueError:
            return 0.0

    def _read_pages(self):
        try:
            return max(1, min(10, int(self.pages_var.get())))
        except (tk.TclError, ValueError):
            return 1

    def _collect_params(self):
        params = {}
        kw = self.search_text_var.get().strip()
        if kw:
            params["search_text"] = kw

        order_value = self._order_map.get(self.order_var.get())
        if order_value and order_value != ORDER_OPTIONS[0][0]:
            params["order"] = order_value

        for key, var in (("price_from", self.price_from_var),
                         ("price_to", self.price_to_var)):
            value = var.get().strip()
            if value:
                params[key] = value

        for key in self.selected:
            vals = [str(x) for x in (self.selected.get(key) or []) if str(x)]
            if vals:
                params[key] = vals

        for key, var in (("discount", self.discount_var),
                         ("favourite", self.favourite_var),
                         ("handicraft", self.handicraft_var),
                         ("give_away", self.give_away_var)):
            if var.get():
                params[key] = True
        return params

    def _load_params_into_form(self, params):
        self.search_text_var.set(params.get("search_text") or "")
        order_value = params.get("order")
        if order_value:
            for value, label in ORDER_OPTIONS:
                if value == order_value:
                    self.order_var.set(label)
                    break
        self.price_from_var.set(str(params.get("price_from") or ""))
        self.price_to_var.set(str(params.get("price_to") or ""))
        for code in list(self.selected.keys()):
            if code != "catalog":
                self.selected.pop(code, None)
        for code, vals in params.items():
            if code in ("search_text", "order", "price_from", "price_to",
                        "discount", "favourite", "handicraft", "give_away"):
                continue
            if isinstance(vals, (list, tuple)) and vals:
                self.selected[code] = [str(v) for v in vals]
        self.discount_var.set(bool(params.get("discount")))
        self.favourite_var.set(bool(params.get("favourite")))
        self.handicraft_var.set(bool(params.get("handicraft")))
        self.give_away_var.set(bool(params.get("give_away")))
        self._rebuild_chips()
        for code in self.selected:
            self._update_filter_button(code)
        self._update_clear_btn()

    # ── Monitoring ──────────────────────────────────────────────────────────

    def _build_watch_objects(self):
        """A figyelési listából `Watch` objektumokat épít."""
        objects = []
        for w in self.watches:
            url = w.get("url")
            if not url and w.get("params"):
                url = build_search_url(w["params"])
            if not url:
                continue
            objects.append(Watch(
                name=w.get("name") or "Monitor",
                url=url,
                min_price=float(w.get("min_price") or 0),
                pages=int(w.get("pages") or 1),
                discord=bool(w.get("discord", True)),
                desktop=bool(w.get("desktop", False)),
            ))
        return objects

    def toggle_monitoring(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.stop_event.set()
            self.start_btn.configure(text="Leállítás…", state="disabled")
            self._set_status(AMBER, "Leállítás", "folyamatban")
            self._log("[dim] Leállítás kérése…")
            return

        watches = self._build_watch_objects()
        if not watches:
            params = self._collect_params()
            if not params:
                self._log("[warn] Nincs szűrő kiválasztva — adj meg legalább "
                          "egy szűrőt vagy adj hozzá figyelést.")
                return
            self.add_watch()
            watches = self._build_watch_objects()
            if not watches:
                return

        webhook = self.webhook_var.get().strip()
        if self.discord_var.get():
            if not webhook:
                self._log("[warn] A Discord be van kapcsolva, de nincs webhook URL "
                          "— értesítések kimaradnak. (Beállítások → webhook)")
            else:
                ok, msg = monitor.check_webhook_format(webhook)
                if not ok:
                    self._log(f"[err] Discord: {msg} Az értesítések nem fognak "
                              f"kiszaladni. (Beállítások → Teszt gomb)")

        try:
            interval = max(1, int(self.interval_var.get()))
        except (tk.TclError, ValueError):
            interval = 7
        try:
            max_alerts = max(1, int(self.max_alerts_var.get()))
        except (tk.TclError, ValueError):
            max_alerts = 15

        self._save_settings()
        self.results.clear()
        self._found = 0
        self.badge.configure(text="0 új találat")
        self._clear_log()

        self.start_btn.configure(text="■  Leállítás", style="Stop.TButton")
        self.reset_btn.configure(state="disabled")
        self._set_status(GREEN, "Futás",
                         f"{len(watches)} figyelés · "
                         f"{interval} perc · discord={self.discord_var.get()}")

        self.worker_thread = threading.Thread(
            target=self._run_monitor,
            args=(watches, self.stop_event, webhook, interval, max_alerts,
                  self.desktop_var.get()),
            daemon=True,
        )
        self.worker_thread.start()

    def _desktop_notify(self, item, watch):
        """Called from the worker thread.

        Only the queue is touched here — Tkinter is not thread safe, so the
        table insert and the beep both happen in `_poll_results` on the main
        thread.
        """
        self._result_q.put(("item", item, watch.name, True))

    def _run_monitor(self, watches, stop_event, webhook, interval, max_alerts,
                     desktop):
        def log_func(msg):
            self._log(msg)

        def notify(item, watch):
            if desktop:
                self._desktop_notify(item, watch)

        try:
            main_loop(
                watches,
                log_func=log_func,
                stop_event=stop_event,
                interval=(interval * 60 * 0.8, interval * 60 * 1.2),
                webhook_url=webhook,
                desktop_notify=notify,
                max_alerts=max_alerts,
            )
        except Exception as e:
            self._log(f"[err] FATAL: {e}")
        finally:
            self._control_q.put(("stop",))

    def _poll_control(self):
        if self._closing:
            return
        while not self._control_q.empty():
            kind, *_ = self._control_q.get_nowait()
            if kind == "stop":
                self._on_stop()
        self._schedule(120, self._poll_control)

    def _poll_results(self):
        if self._closing:
            return
        while not self._result_q.empty():
            kind, *payload = self._result_q.get_nowait()
            if kind == "item":
                item, watch_name, is_new = payload
                self.results.add(item, watch_name, is_new=is_new)
                self.result_count.configure(
                    text=f"{len(self.results.rows)} sor")
                try:
                    self.root.bell()
                except tk.TclError:
                    pass
            elif kind == "rows":
                rows, total = payload
                self.results.load_rows(rows)
                self.result_count.configure(
                    text=f"{len(self.results.rows)} sor "
                         f"(adatbázisban {total})")
            elif kind == "err":
                self._log(f"[err] Találatok betöltése sikertelen: {payload[0]}")
        self._schedule(200, self._poll_results)

    def reset_data(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("Figyelem", "Előbb állítsd le a monitort.")
            return
        if not messagebox.askyesno(
                "Megerősítés",
                "Töröljem az adatbázist? A beállítások (settings.db) megmaradnak."):
            return
        if delete_db_file():
            self.results.clear()
            self.result_count.configure(text="0 sor")
            self._log("[ok] Törölve: vinted_monitor.db")
        else:
            self._log("[dim] Nem volt mit törölni.")

    def _on_stop(self):
        self.start_btn.configure(text="▶  Indítás", style="Accent.TButton")
        self.reset_btn.configure(state="normal")
        self.stop_event.clear()
        self.worker_thread = None
        self._log("[dim] Leállva.")
        self._set_status(MUTED, "Készenlét", None)
        self.reload_results()

    def _on_close(self):
        """Tiszta kilépés: figyelmeztetés, beállítások mentése, böngészők lezárása."""
        self._closing = True
        if self.worker_thread and self.worker_thread.is_alive():
            if not messagebox.askyesno(
                    "Kilépés",
                    "A monitor fut. Leálljak és kilépsek?"):
                self._closing = False
                return
            self.stop_event.set()
            # adjuk a szálnak idejét bezárni a böngészőt, ne maradjon
            # kísért chromedriver folyamat a háttérben
            self.worker_thread.join(timeout=15)
        self._save_settings()
        self._cancel_jobs()
        try:
            self.root.destroy()
        except Exception:
            pass
        # az ablak már bezárult, így ez nem fagyasztja a felületet
        monitor.shutdown_browsers()


if __name__ == "__main__":
    root = tk.Tk()
    root.call("tk", "scaling", 1.1)
    gui = VintedMonitorGUI(root)
    root.protocol("WM_DELETE_WINDOW", gui._on_close)
    atexit.register(monitor.shutdown_browsers)
    root.mainloop()
