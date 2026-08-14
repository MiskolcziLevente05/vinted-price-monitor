import os
import math
import threading
import queue
import tkinter as tk
from tkinter import ttk, font as tkfont
from tkinter import (
    X, Y, BOTH, LEFT, RIGHT, TOP, BOTTOM, END, N, S, E, W,
    NW, NE, SW, SE, CENTER,
    Canvas, Toplevel, Scrollbar, Text, StringVar, BooleanVar, messagebox,
)

from monitor import (
    main_loop,
    build_search_url,
    fetch_category_tree,
    fetch_category_filters,
    fetch_brands,
    DB_PATH,
    EXPORT_PATH,
    ORDER_OPTIONS,
    STATUS_OPTIONS,
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

LOG_W = 370      # napló panel szélessége
LOG_HINT = 60    # rail szélesség összecsukva

# A chip-sor ajánlott sorrendje — ismeretlen facet code-ok a végére kerülnek
CHIP_ORDER = ["brand", "size", "color", "material", "status", "brand_collection"]

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

        style.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF",
                        bordercolor=ACCENT, focusthickness=0, padding=(22, 11),
                        font=(FONT, 11, "bold"))
        style.map("Accent.TButton",
                  background=[("pressed", ACCENT_DK), ("active", ACCENT_DK)],
                  foreground=[("disabled", "#B7E2E4")])

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


def _desc(name, value):
    return name, value


class SettingsWindow(Toplevel):
    """Külön ablakban nyitható beállítások; a megosztott StringVar/BooleanVar
    élesen szinkronban tartja a főablakkal."""

    def __init__(self, master, gui):
        super().__init__(master)
        self.gui = gui
        self.title("Beállítások")
        self.configure(bg=CARD)
        self.geometry("460x380")
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        header = tk.Frame(self, bg=HEADER_BG)
        header.pack(fill=X)
        tk.Label(header, text="Beállítások", bg=HEADER_BG, fg="#FFFFFF",
                 font=(FONT, 12, "bold"), padx=14, pady=12).pack(anchor=W)

        body = tk.Frame(self, bg=CARD)
        body.pack(fill=BOTH, expand=True, padx=18, pady=16)

        row_t = tk.Frame(body, bg=CARD)
        row_t.pack(fill=X)
        ttk.Checkbutton(row_t, text="Mock mód", variable=self.gui.mock_var,
                        style="Card.TCheckbutton").pack(side=LEFT, padx=(0, 18))
        ttk.Checkbutton(row_t, text="Discord értesítés", variable=self.gui.discord_var,
                        style="Card.TCheckbutton").pack(side=LEFT, padx=(0, 18))
        ttk.Checkbutton(row_t, text="JSON export", variable=self.gui.export_var,
                        style="Card.TCheckbutton").pack(side=LEFT, padx=(0, 18))

        row_u = tk.Frame(body, bg=CARD)
        row_u.pack(fill=X, pady=(16, 0))
        ttk.Label(row_u, text="Manuális URL (fallback):", style="Field.TLabel"
                  ).pack(anchor=W)
        ttk.Entry(row_u, textvariable=self.gui.url_var).pack(fill=X, pady=(4, 0))
        ttk.Label(body, text="ha nincs szűrő kiválasztva, ez az URL kerül használatra; "
                             "ha ez is üres, a .env VINTED_TARGET_URL",
                  style="Muted.TLabel", anchor=W, wraplength=410).pack(
                      fill=X, pady=(6, 0))

        tk.Frame(body, bg=BORDER, height=1).pack(fill=X, pady=(16, 0))

        info = tk.Frame(body, bg=ACCENT_LT, highlightbackground=ACCENT,
                        highlightthickness=1)
        info.pack(fill=X, pady=(16, 0))
        tk.Label(info, text=("A kategória kiválasztásakor a szűrők (facetek) "
                             "automatikusan betöltődnek élő Vinted adatokból."),
                 bg=ACCENT_LT, fg=TEXT, font=(FONT, 9), wraplength=400,
                 padx=12, pady=10, justify="left").pack(fill=X)

        bar = tk.Frame(self, bg=CARD)
        bar.pack(fill=X, padx=18, pady=12)
        ttk.Button(bar, text="Kész", style="Accent.TButton",
                   command=self.destroy).pack(side=RIGHT)


class LoadingWindow(Toplevel):
    """Indításnál megjelenő, forgó spinneres ablak; nem zárható kézzel."""

    _SPIN = ["#09B1BA", "#2FBFC7", "#55CCD3", "#7CD9DE",
             "#A3E6EA", "#C9F2F4", "#E3F8F9", "#E3F8F9"]

    def __init__(self, master, text="Betöltés…"):
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
        ttk.Label(self, text="Élő Vinted adatok lekérése…",
                  style="Muted.TLabel", anchor=CENTER).pack(fill=X, pady=(2, 30))

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
            px = cx + r * math.cos(a)
            py = cy + r * math.sin(a)
            self.canvas.create_oval(px - 4.5, py - 4.5, px + 4.5, py + 4.5,
                                    fill=self._SPIN[i], outline="")

    def _spin(self):
        if not self.winfo_exists():
            return
        self.angle = (self.angle - 24) % 360
        self._draw_spinner()
        self.after(40, self._spin)


class MultiSelectPopup(Toplevel):
    def __init__(self, master, title, options, selected=None):
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
        self.on_search = None
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

    def set_on_search(self, callback):
        self.on_search = callback
        self.search_btn = ttk.Button(self, text="Keresés Vintedtől",
                                     style="Ghost.TButton", command=self._search)
        self.search_btn.pack(fill=X, padx=12, pady=(0, 6))

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
        self.status_var.set("Keresés folyamatban...")
        threading.Thread(target=self._search_worker, args=(kw,), daemon=True).start()

    def _search_worker(self, kw):
        try:
            pairs = self.on_search(kw)
        except Exception:
            pairs = []
        self._result_q.put(pairs)

    def _poll_result(self):
        while not self._result_q.empty():
            pairs = self._result_q.get_nowait()
            self._search_done(pairs)
        if self.winfo_exists():
            self.after(100, self._poll_result)

    def _search_done(self, pairs):
        if self.search_btn:
            self.search_btn.configure(state="normal")
        self.status_var.set(
            f"{len(pairs)} márka hozzáadva" if pairs else "Nincs találat Vintedtől")
        existing = {str(i) for i, _ in self.options}
        for opt_id, label in pairs:
            key = str(opt_id)
            if key not in existing:
                self.options.append((opt_id, label))
                self.vars[key] = BooleanVar(value=False)
                existing.add(key)
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
        self.path = []          # út a gyökértől az aktuális szintig (id + title)
        self.candidate = None   # {"id": ..., "title": ...}
        self.candidate_node = None
        self.catalog_url = None
        self.nodes = []         # az aktuális szint node-jai

        header = tk.Frame(self, bg=HEADER_BG)
        header.pack(fill=X)
        tk.Label(header, text="Kategóriák · egyszerre csak egy választható",
                 bg=HEADER_BG, fg="#FFFFFF", font=(FONT, 12, "bold"),
                 padx=14, pady=12).pack(anchor=W)

        body = tk.Frame(self, bg=CARD)
        body.pack(fill=BOTH, expand=True, padx=12, pady=12)

        # Kiválasztott sor
        sel_row = tk.Frame(body, bg=CARD)
        sel_row.pack(fill=X, pady=(0, 8))
        self.sel_label = tk.Label(sel_row, text="", bg=CARD, fg=TEXT,
                                  font=(FONT, 10, "bold"), anchor=W)
        self.sel_label.pack(side=LEFT, fill=X, expand=True)
        self.ok_btn = ttk.Button(sel_row, text="Kiválasztás",
                                 style="Accent.TButton", command=self._ok)
        self.ok_btn.pack(side=RIGHT)

        # Breadcrumb
        crumb_row = tk.Frame(body, bg=CARD)
        crumb_row.pack(fill=X, pady=(0, 8))
        self.crumb = tk.Frame(crumb_row, bg=CARD)
        self.crumb.pack(side=LEFT)
        self.back_btn = ttk.Button(crumb_row, text="← Vissza",
                                   style="Soft.TButton", command=self._back)
        self.back_btn.pack(side=RIGHT)

        # Lista
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

    # ── Segédfüggvények ─────────────────────────────────────────────────────

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

    # ── Megjelenítés ────────────────────────────────────────────────────────

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
            self.ok_btn.configure(state="normal")
        else:
            self.sel_label.configure(text="Kiválasztott:  — nincs kiválasztva")
            self.ok_btn.configure(state="normal")

    # ── Interakció ──────────────────────────────────────────────────────────

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
        if self.candidate:
            node = self.candidate_node
            self.catalog_url = node.get("url") if node else None
            if not self.catalog_url:
                self.catalog_url = build_search_url(
                    {"catalog": [self.candidate["id"]]})
        self.destroy()


class VintedMonitorGUI:
    def __init__(self, root):
        self.root = root
        UI.init(root)
        self.root.title("Vinted Price Monitor")
        self.root.geometry("1180x900")
        self.root.minsize(980, 720)
        self.root.configure(bg=BG)

        self.worker_thread = None
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self._filters_result_q = queue.Queue()
        self._control_q = queue.Queue()
        self.settings_win = None
        self._log_open = True
        self._loading_win = None
        self._catalog_url = None

        self.selected = {"catalog": []}
        self.filter_options = {}
        self.facet_titles = {}
        self.facet_codes = []
        self.category_tree = None
        self._filter_buttons = {}
        self._order_map = {label: value for value, label in ORDER_OPTIONS}
        self._found = 0

        self._build_ui()
        self._update_clear_btn()
        self._poll_log_queue()
        self._poll_filters_result()
        self._poll_control()
        self.root.after(150, self._startup_load)

    # ── Layout ──────────────────────────────────────────────────────────────

    def _card(self, parent, **kw):
        frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1, **kw)
        return frame

    def _section_title(self, parent, text):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill=X, pady=(0, 10))
        tk.Label(row, text=text.upper(), bg=CARD, fg=ACCENT_DK,
                 font=(FONT, 10, "bold")).pack(side=LEFT)
        tk.Frame(row, bg=BORDER, height=1).pack(fill=X, side=LEFT,
                                                expand=True, padx=10)

    def _build_ui(self):
        # ── Header ──
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

        # ── Content (bal: kereső · jobb: napló, csukható) ──
        page = tk.Frame(self.root, bg=BG, padx=18)
        page.pack(fill=BOTH, expand=True)

        content = tk.Frame(page, bg=BG)
        content.pack(fill=BOTH, expand=True)

        # jobb oldali napló panel — elrejtve a rail váltó gomb
        self.log_col = tk.Frame(content, bg=BG, width=LOG_W)
        self.log_col.pack_propagate(False)
        self.log_col.pack(side=RIGHT, fill=BOTH, expand=False, padx=(16, 0))

        self.log_rail = tk.Frame(content, bg=BG, width=LOG_HINT)
        self.log_rail.pack_propagate(False)

        # bal oszlop
        self.left_col = tk.Frame(content, bg=BG)
        self.left_col.pack(side=LEFT, fill=BOTH, expand=True)

        # Kereső kártya
        search_card = self._card(self.left_col)
        search_card.pack(fill=X, pady=(16, 0))
        sc = tk.Frame(search_card, bg=CARD, padx=18, pady=16)
        sc.pack(fill=X)
        self._section_title(sc, "Kereső szűrők")

        # Sor 1: kulcsszó + rendezés
        r1 = tk.Frame(sc, bg=CARD)
        r1.pack(fill=X, pady=(0, 12))
        ttk.Label(r1, text="Kulcsszó", style="Field.TLabel").pack(anchor=W)
        self.search_text_var = StringVar()
        ttk.Entry(r1, textvariable=self.search_text_var).pack(fill=X, pady=(4, 0))

        r1b = tk.Frame(sc, bg=CARD)
        r1b.pack(fill=X, pady=(0, 12))
        ttk.Label(r1b, text="Rendezés", style="Field.TLabel").pack(anchor=W)
        self.order_var = StringVar(value=ORDER_OPTIONS[0][1])
        ttk.Combobox(r1b, textvariable=self.order_var, state="readonly",
                     values=[label for _, label in ORDER_OPTIONS]).pack(
                         fill=X, pady=(4, 0))

        # Ár sor
        r2 = tk.Frame(sc, bg=CARD)
        r2.pack(fill=X, pady=(0, 12))
        ttk.Label(r2, text="Ár (Ft)", style="Field.TLabel").pack(anchor=W)
        price_row = tk.Frame(r2, bg=CARD)
        price_row.pack(fill=X, pady=(4, 0))
        self.price_from_var = StringVar()
        self.price_to_var = StringVar()
        ttk.Entry(price_row, textvariable=self.price_from_var, width=12).pack(
            side=LEFT)
        tk.Label(price_row, text="—", bg=CARD, fg=MUTED).pack(side=LEFT,
                                                              padx=8)
        ttk.Entry(price_row, textvariable=self.price_to_var, width=12).pack(
            side=LEFT)
        ttk.Label(price_row, text="from · to (opcionális)", style="Muted.TLabel"
                  ).pack(side=LEFT, padx=10)

        tk.Frame(price_row, bg=BORDER, width=1).pack(side=LEFT, fill=Y, padx=10)

        self.clear_btn = ttk.Button(price_row, text="Szűrők törlése",
                                    style="Ghost.TButton",
                                    command=self.clear_filters)
        self.clear_btn.pack(side=LEFT, padx=(4, 0))

        # Filter chip sor — a "Kategóriák" chip mindig jelen van,
        # a többi chip a kategória facetjei alapján épül fel
        r3 = tk.Frame(sc, bg=CARD)
        r3.pack(fill=X)
        self.chips = tk.Frame(r3, bg=CARD)
        self.chips.pack(fill=X)
        self._rebuild_chips()

        # Togglék
        r4 = tk.Frame(sc, bg=CARD)
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

        # a "Szűrők törlése" gomb állapota kövesse a beállított szűrőket
        for var in (self.search_text_var, self.price_from_var, self.price_to_var,
                    self.order_var, self.discount_var, self.favourite_var,
                    self.handicraft_var, self.give_away_var):
            var.trace_add("write", lambda *a: self._update_clear_btn())

        # Beállítási változók (a Beállítások ablak megosztott hivatkozással él)
        self.mock_var = BooleanVar(value=False)
        self.discord_var = BooleanVar(value=True)
        self.export_var = BooleanVar(value=True)
        self.min_price_var = StringVar(value="0")
        self.url_var = StringVar()

        # Akciósor (bal oszlop alja)
        actions = tk.Frame(self.left_col, bg=BG)
        actions.pack(fill=X, pady=(16, 16))
        self.start_btn = ttk.Button(actions, text="▶  Indítás", style="Accent.TButton",
                                    command=self.toggle_monitoring)
        self.start_btn.pack(side=LEFT)
        self.reset_btn = ttk.Button(actions, text="Adatbázis törlése",
                                    style="Soft.TButton", command=self.reset_data)
        self.reset_btn.pack(side=LEFT, padx=(10, 0))
        ttk.Button(actions, text="⚙  Beállítások", style="Ghost.TButton",
                   command=self._open_settings).pack(side=RIGHT)

        # ── Napló panel (jobb oldal) ──
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

        clear_row = tk.Frame(lg, bg=CARD)
        clear_row.pack(fill=X, pady=(10, 0))
        ttk.Button(clear_row, text="Napló ürítése", style="Soft.TButton",
                   command=self._clear_log).pack(side=LEFT)

        # ── Rail (összecsukott napló váltó) ──
        rail_card = self._card(self.log_rail)
        rail_card.pack(fill=BOTH, expand=True, pady=(16, 16))
        ttk.Button(rail_card, text="Napló\n▸", style="Ghost.TButton",
                   command=self._toggle_log).pack(fill=X, padx=6, pady=6)

        # Státusz sáv
        self._build_statusbar()

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
        if any(k in low for k in ("error", "fatal", "failed", "exception")):
            return "err"
        if any(k in low for k in ("new item", "[file]", "betöltve", "hozzáadva",
                                  "alert sent")):
            return "ok"
        if any(k in low for k in ("warn", "skip", "nem adott", "fallback",
                                  "nincs találat")):
            return "warn"
        if any(k in low for k in ("szűrő url", "szűrők betöltése",
                                  "target url", "kategóriafa")):
            return "hl"
        if any(k in low for k in ("sleeping", "fetching", "parsed", "monitoring")):
            return "dim"
        return "info"

    def _log(self, message):
        self.log_queue.put(message)

    def _poll_log_queue(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get_nowait()
            self._append_log(msg)
        self.root.after(120, self._poll_log_queue)

    def _append_log(self, msg):
        tag = self._classify(msg)
        self.log_text.configure(state="normal")
        self.log_text.insert(END, f"{msg}\n", (tag,))
        self.log_text.see(END)
        self.log_text.configure(state="disabled")
        if "NEW Item" in msg:
            self._found += 1
            self.badge.configure(text=f"{self._found} új találat")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", END)
        self.log_text.configure(state="disabled")

    # ── Filter helpers ─────────────────────────────────────────────────────

    def _flat_catalog(self):
        lookup = {}

        def walk(nodes):
            for n in nodes:
                lookup[str(n.get("id"))] = n.get("title") or str(n.get("id"))
                walk(n.get("catalogs") or [])

        if self.category_tree:
            walk(self.category_tree)
        return lookup

    def _open_filter_popup(self, key):
        if key == "catalog":
            if not self.category_tree:
                messagebox.showinfo(
                    "Info", "Még nincs kategóriafa. Indítsd újra az alkalmazást a betöltéséhez.")
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
            messagebox.showinfo(
                "Info", "Ehhez a szűrőhöz nincs betöltött opció.")
            return
        popup = MultiSelectPopup(self.root, self.facet_titles.get(key, key), opts,
                                 self.selected.get(key) or [])
        if key == "brand":
            popup.set_on_search(fetch_brands)
        self.root.wait_window(popup)
        if popup.result is not None:
            self.selected[key] = [str(x) for x in popup.result]
            self._update_filter_button(key)
            self._update_clear_btn()

    # ── Dinamikus filter chip-ek ────────────────────────────────────────────

    def _add_chip(self, code, label):
        btn = ttk.Button(self.chips, text=label, style="Filter.TButton",
                         command=lambda k=code: self._open_filter_popup(k))
        btn.pack(side=LEFT, fill=X, expand=True, padx=(0, 6))
        self._filter_buttons[code] = btn

    def _rebuild_chips(self):
        for w in self.chips.winfo_children():
            w.destroy()
        self._filter_buttons = {}
        self._add_chip("catalog", "Kategóriák")
        for code in self.facet_codes:
            if code == "brand" or self.filter_options.get(code) or code == "status":
                self._add_chip(code, self.facet_titles.get(code, code))

    def _update_filter_button(self, key):
        if key == "catalog":
            lookup = self._flat_catalog()
        else:
            lookup = {str(k): v for k, v in (self.filter_options.get(key) or [])}
            if key == "status":
                lookup.update({str(k): v for k, v in STATUS_OPTIONS})
        titles = [lookup.get(str(i), str(i)) for i in (self.selected.get(key) or [])]
        label = self._filter_buttons[key].cget("text").split(" (")[0]
        if titles:
            brief = " + ".join(titles[:2])
            if len(titles) > 2:
                brief += f" … +{len(titles) - 2}"
            self._filter_buttons[key].configure(text=f"{brief}")
            self._filter_buttons[key].configure(style="Filter.TButton")
        else:
            self._filter_buttons[key].configure(text=label)

    def clear_filters(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning(
                "Warning", "A monitor fut — előbb állítsd le, hogy módosíthass szűrőket.")
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
        self.order_var.set(ORDER_OPTIONS[0][1])
        for var in (self.discount_var, self.favourite_var,
                    self.handicraft_var, self.give_away_var):
            var.set(False)
        self._log("[ok] Szűrők törölve — a kereső űrlap kiürítve.")
        self._set_status(GREEN, "Készenlét", "szűrők törölve")
        self._update_clear_btn()

    # ── Szűrő-státusz / gomb-állapot ─────────────────────────────────────────

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

    def _drop_stored_data(self):
        """Indításkor törli a korábban mentett adatbázist és JSON exportot."""
        deleted = []
        for path, name in [(DB_PATH, "vinted_monitor.db"),
                           (EXPORT_PATH, "found_items.json")]:
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    deleted.append(name)
                except OSError:
                    pass
        if deleted:
            self._log(f"[ok] Indításkor törölve: {', '.join(deleted)}")

    def _startup_load(self):
        self._drop_stored_data()
        # a kedvezményes / kézműves / ingyen elvihető / csak kedvencek togglék
        # induláskor alaphelyzetbe állnak
        for var in (self.discount_var, self.favourite_var,
                    self.handicraft_var, self.give_away_var):
            var.set(False)
        self._update_clear_btn()
        self._loading_win = LoadingWindow(self.root, text="Kategóriák betöltése")
        threading.Thread(target=self._fetch_categories_worker,
                         daemon=True).start()

    def _fetch_categories_worker(self):
        try:
            tree = fetch_category_tree()
            self._filters_result_q.put(("tree", tree))
        except Exception as e:
            self._filters_result_q.put(("err", e))

    def _close_loading(self):
        if self._loading_win and self._loading_win.winfo_exists():
            self._loading_win.destroy()
        self._loading_win = None

    def _on_catalog_selected(self, cat_url):
        # a kategóriához tartozó szűrők más kategóriában nem érvényesek
        keep = self.selected.get("catalog", [])
        self.selected = {"catalog": keep}
        self.filter_options = {}
        self.facet_titles = {}
        self.facet_codes = []
        self._rebuild_chips()
        if not self._filter_buttons:
            return
        self._update_filter_button("catalog")
        cid = (keep or [None])[0]
        self._catalog_url = cat_url if cid else None
        if not cid:
            return
        self._log(f"[i] Kategória szűrőinek betöltése (kategória #{cid})…")
        self._update_clear_btn()
        self._loading_win = LoadingWindow(self.root, text="Kategória szűrők betöltése")
        self._set_status(AMBER, "Folyamatban", "kategória szűrőinek betöltése")
        threading.Thread(target=self._fetch_category_filters_worker,
                         args=(cat_url, cid), daemon=True).start()

    def _fetch_category_filters_worker(self, cat_url, cat_id):
        try:
            facets = fetch_category_filters(cat_url, cat_id)
            self._filters_result_q.put(("catfilters", facets))
        except Exception as e:
            self._filters_result_q.put(("err", e))

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
        self._log(f"[ok] Kategória szűrők betöltve: {total} opció, {len(codes)} szűrő")
        for code in codes:
            if opts[code]:
                self._log(f"[ok]  • {titles[code]}: {len(opts[code])} opció")
        self._set_status(GREEN, "Készenlét", "kategória szűrői betöltve")

    def _poll_filters_result(self):
        while not self._filters_result_q.empty():
            kind, *payload = self._filters_result_q.get_nowait()
            if kind == "tree":
                self._apply_category_tree(payload[0])
            elif kind == "catfilters":
                self._apply_category_filters(payload[0])
            else:
                self._filter_load_error(payload[0])
        self.root.after(120, self._poll_filters_result)

    def _apply_category_tree(self, tree):
        self._close_loading()
        self.category_tree = tree
        self._update_filter_button("catalog")
        self._log(f"[ok] Kategóriafa betöltve: {len(tree)} gyökércsoport")
        self._log("[dim] A kategória szűrői a kategória kiválasztása után "
                  "jelennek meg a chip-sorban.")
        self._set_status(GREEN, "Készenlét", "kategóriák betöltve")

    def _filter_load_error(self, e):
        self._close_loading()
        self._update_clear_btn()
        self._log(f"[err] Szűrő betöltési hiba: {e}")
        self._set_status(RED, "Hiba", "próbáld újra, vagy URL fallback")

    # ── Params / monitoring ────────────────────────────────────────────────

    def _collect_params(self):
        params = {}
        kw = self.search_text_var.get().strip()
        if kw:
            params["search_text"] = kw

        order_label = self.order_var.get()
        order_value = self._order_map.get(order_label)
        if order_value and order_value != ORDER_OPTIONS[0][0]:
            params["order"] = order_value

        for key, var in (("price_from", self.price_from_var),
                         ("price_to", self.price_to_var)):
            v = var.get().strip()
            if v:
                params[key] = v

        for key in self.selected:
            vals = [str(x) for x in (self.selected.get(key) or []) if str(x)]
            if vals:
                params[key] = vals

        if self.discount_var.get():
            params["discount"] = True
        if self.favourite_var.get():
            params["favourite"] = True
        if self.handicraft_var.get():
            params["handicraft"] = True
        if self.give_away_var.get():
            params["give_away"] = True
        return params

    def toggle_monitoring(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.stop_event.set()
            self.start_btn.configure(text="Leállítás…", state="disabled")
            self._set_status(AMBER, "Leállítás", "folyamatban")
            return

        self.stop_event.clear()
        self._clear_log()

        try:
            min_price = float(self.min_price_var.get())
        except ValueError:
            messagebox.showerror("Error", "A helyi minimum árnak számnak kell lennie")
            return

        params = self._collect_params()
        url_field = self.url_var.get().strip() or None
        if params:
            target_url = build_search_url(params)
            self._log(f"[hl] Szűrő URL: {target_url}")
        else:
            target_url = url_field

        if not target_url:
            self._log("[warn] Nincs szűrő és kézzel megadott URL sem — "
                      "a .env VINTED_TARGET_URL lesz használva.")

        mock = self.mock_var.get()
        discord = self.discord_var.get()
        export = self.export_var.get()
        self._found = 0
        self.badge.configure(text="0 új találat")

        self.start_btn.configure(text="■  Leállítás", style="Stop.TButton")
        self.reset_btn.configure(state="disabled")
        self._set_status(GREEN, "Futás", f"mock={mock} · discord={discord}")

        self.worker_thread = threading.Thread(
            target=self._run_monitor,
            args=(min_price, self.stop_event, target_url, mock, discord, export),
            daemon=True,
        )
        self.worker_thread.start()

    def _run_monitor(self, min_price, stop_event, target_url, mock, discord, export):
        def log_func(msg):
            if stop_event.is_set():
                return
            self._log(msg)

        try:
            main_loop(
                mock_mode=mock,
                discord_enabled=discord,
                export_json=export,
                min_price=min_price,
                log_func=log_func,
                stop_event=stop_event,
                target_url=target_url,
            )
        except Exception as e:
            self._log(f"[err] FATAL: {e}")
        finally:
            self._control_q.put(("stop",))

    def _poll_control(self):
        while not self._control_q.empty():
            kind, *_ = self._control_q.get_nowait()
            if kind == "stop":
                self._on_stop()
        self.root.after(120, self._poll_control)

    def reset_data(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("Warning", "Előbb állítsd le a monitort.")
            return
        if not messagebox.askyesno("Megerősítés", "Töröljem az adatbázist és a JSON exportot?"):
            return
        deleted = []
        for path, name in [(DB_PATH, "vinted_monitor.db"),
                           (EXPORT_PATH, "found_items.json")]:
            if os.path.isfile(path):
                os.remove(path)
                deleted.append(name)
        if deleted:
            self._log(f"[ok] Törölve: {', '.join(deleted)}")
        else:
            self._log("[dim] Nem volt mit törölni.")

    def _on_stop(self):
        self.start_btn.configure(text="▶  Indítás", style="Accent.TButton")
        self.reset_btn.configure(state="normal")
        self.stop_event.clear()
        self.worker_thread = None
        self._log("[dim] Leállítva.")
        self._set_status(MUTED, "Készenlét", None)


if __name__ == "__main__":
    root = tk.Tk()
    root.call("tk", "scaling", 1.1)
    VintedMonitorGUI(root)
    root.mainloop()