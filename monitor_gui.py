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
    X, Y, BOTH, LEFT, RIGHT, TOP, BOTTOM, END, W, S, NW, CENTER,
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

LOG_W = 380      # napló panel szélessége
LOG_HINT = 60    # rail szélesség összecsukva

# A chip-sor ajánlott sorrendje — ismeretlen facet code-ok a végére kerülnek
CHIP_ORDER = ["brand", "size", "color", "material", "status", "patterns",
              "brand_collection"]

DISCORD_KEY = "discord_webhook"
KEEP_HISTORY_KEY = "keep_history"
DESKTOP_KEY = "desktop_notify"
HISTORY_NOTIFY_KEY = "desktop_on_new"


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


class VintedMonitorGUI:
    def __init__(self, root):
        self.root = root
        UI.init(root)
        self.root.title("Vinted Price Monitor")
        self.root.geometry("1280x940")
        self.root.minsize(1040, 760)
        self.root.configure(bg=BG)

        self.worker_thread = None
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self._filters_result_q = queue.Queue()
        self._control_q = queue.Queue()
        self._result_q = queue.Queue()
        self.settings_win = None
        self._log_open = True
        self._loading_win = None
        self._catalog_url = None
        self._catalog_lookup = None      # cache a kategórianevekre

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

        self._build_ui()
        self._update_clear_btn()
        self._closing = False
        self._after_jobs = set()
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

    def _section_title(self, parent, text):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill=X, pady=(0, 10))
        tk.Label(row, text=text.upper(), bg=CARD, fg=ACCENT_DK,
                 font=(FONT, 10, "bold")).pack(side=LEFT)
        tk.Frame(row, bg=BORDER, height=1).pack(fill=X, side=LEFT,
                                                expand=True, padx=10)

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
        page.pack(fill=BOTH, expand=True)

        content = tk.Frame(page, bg=BG)
        content.pack(fill=BOTH, expand=True)

        self.log_col = tk.Frame(content, bg=BG, width=LOG_W)
        self.log_col.pack_propagate(False)
        self.log_col.pack(side=RIGHT, fill=BOTH, expand=False, padx=(16, 0))

        self.log_rail = tk.Frame(content, bg=BG, width=LOG_HINT)
        self.log_rail.pack_propagate(False)

        self.left_col = tk.Frame(content, bg=BG)
        self.left_col.pack(side=LEFT, fill=BOTH, expand=True)

        self._build_search_card()
        self._build_watch_card()
        self._build_results_card()
        self._build_action_bar()
        self._build_log_panel()
        self._build_rail()
        self._build_statusbar()

    # ── Search card ─────────────────────────────────────────────────────────

    def _build_search_card(self):
        card = self._card(self.left_col)
        card.pack(fill=X, pady=(16, 0))
        body = tk.Frame(card, bg=CARD, padx=18, pady=16)
        body.pack(fill=X)
        self._section_title(body, "Kereső szűrők")

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
        card = self._card(self.left_col)
        card.pack(fill=X, pady=(12, 0))
        body = tk.Frame(card, bg=CARD, padx=18, pady=14)
        body.pack(fill=X)
        head = tk.Frame(body, bg=CARD)
        head.pack(fill=X, pady=(0, 8))
        tk.Label(head, text="FIGYELÉSEK", bg=CARD, fg=ACCENT_DK,
                 font=(FONT, 10, "bold")).pack(side=LEFT)
        self.watch_count = tk.Label(head, text="(0)", bg=CARD, fg=MUTED,
                                    font=(FONT, 9))
        self.watch_count.pack(side=LEFT, padx=(8, 0))
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

        for idx, w in enumerate(self.watches):
            row = tk.Frame(self.watch_box, bg=CARD)
            row.pack(fill=X, pady=2)
            if idx == self._editing:
                row.configure(bg=ACCENT_LT)
            name = tk.Label(row, text=w["name"], bg=row.cget("bg"), fg=TEXT,
                            font=(FONT, 10, "bold"), width=22, anchor=W)
            name.pack(side=LEFT)
            detail = (f"{w['pages']} oldal · min. {int(w['min_price'])} Ft"
                      + ("" if w["discord"] else " · discord: ki"))
            tk.Label(row, text=detail, bg=row.cget("bg"), fg=MUTED,
                     font=(FONT, 9), anchor=W).pack(side=LEFT, fill=X,
                                                    expand=True, padx=(8, 0))
            ttk.Button(row, text="✎", style="Link.TButton", width=3,
                       command=lambda i=idx: self.edit_watch(i)).pack(side=RIGHT)
            ttk.Button(row, text="✕", style="Link.TButton", width=3,
                       command=lambda i=idx: self.remove_watch(i)).pack(side=RIGHT)

    def add_watch(self):
        params = self._collect_params()
        if not params:
            self._log("[warn] Nincs szűrő kiválasztva — adj meg legalább egyet.")
            return
        name = self._next_watch_name()
        watch = {
            "name": name,
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

    def edit_watch(self, idx):
        if not (0 <= idx < len(self.watches)):
            return
        w = self.watches[idx]
        self._load_params_into_form(w.get("params") or {})
        self._editing = idx
        self.min_price_var.set(str(int(w["min_price"])))
        self.pages_var.set(w["pages"])
        self._render_watches()
        self._log(f"[i] „{w['name']}” szerkesztése — a módosítások mentéskor "
                  "érvényesek lesznek.")
        self._log("[dim] Szerkesztés után nyomd meg a „+ Hozzáadás” gombot a "
                  "frissítéshez, vagy töröld a figyelést.")

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

    # ── Results card ────────────────────────────────────────────────────────

    def _build_results_card(self):
        card = self._card(self.left_col)
        card.pack(fill=BOTH, expand=True, pady=(12, 0))
        body = tk.Frame(card, bg=CARD, padx=18, pady=14)
        body.pack(fill=BOTH, expand=True)
        head = tk.Frame(body, bg=CARD)
        head.pack(fill=X, pady=(0, 8))
        tk.Label(head, text="TALÁLATOK", bg=CARD, fg=ACCENT_DK,
                 font=(FONT, 10, "bold")).pack(side=LEFT)
        self.result_count = tk.Label(head, text="0 sor", bg=CARD, fg=MUTED,
                                     font=(FONT, 9))
        self.result_count.pack(side=LEFT, padx=(8, 0))
        ttk.Button(head, text="Frissítés", style="Link.TButton",
                   command=self.reload_results).pack(side=RIGHT)
        ttk.Button(head, text="Táblázat ürítése", style="Link.TButton",
                   command=self._clear_results).pack(side=RIGHT, padx=(0, 6))

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
        actions = tk.Frame(self.left_col, bg=BG)
        actions.pack(fill=X, pady=(12, 16))
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
        log_card.pack(fill=BOTH, expand=True, pady=(16, 16))
        lg = tk.Frame(log_card, bg=CARD, padx=16, pady=16)
        lg.pack(fill=BOTH, expand=True)

        head = tk.Frame(lg, bg=CARD)
        head.pack(fill=X, pady=(0, 10))
        tk.Label(head, text="ESEMÉNYNAPLÓ", bg=CARD, fg=ACCENT_DK,
                 font=(FONT, 10, "bold")).pack(side=LEFT)
        ttk.Button(head, text="−  Elrejt", style="Soft.TButton", width=8,
                   command=self._toggle_log).pack(side=RIGHT)

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
        rail_card.pack(fill=BOTH, expand=True, pady=(16, 16))
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
        self.log_col.pack_forget()
        self.log_rail.pack_forget()
        self.left_col.pack_forget()
        if self._log_open:
            self._log_open = False
            self.log_rail.pack(side=RIGHT, fill=Y, expand=False, padx=(16, 0))
        else:
            self._log_open = True
            self.log_col.pack(side=RIGHT, fill=BOTH, expand=False, padx=(16, 0))
        self.left_col.pack(side=LEFT, fill=BOTH, expand=True)

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
