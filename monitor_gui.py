import os
import threading
import queue
import time
from tkinter import Tk, Frame, Checkbutton, BooleanVar, Label, Entry, Button, Text, Scrollbar, RIGHT, LEFT, BOTH, END, N, S, E, W, EW, TOP, X, Y, IntVar, StringVar, messagebox

from monitor import main_loop, DB_PATH, EXPORT_PATH


class VintedMonitorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Vinted Price Monitor")
        self.root.geometry("700x500")

        self.worker_thread = None
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()

        self._build_ui()
        self._poll_log_queue()

    def _build_ui(self):
        # ── Settings frame ──
        settings = Frame(self.root, padx=10, pady=10)
        settings.pack(fill=X)

        self.mock_var = BooleanVar(value=True)
        self.discord_var = BooleanVar(value=True)
        self.export_var = BooleanVar(value=True)
        self.min_price_var = StringVar(value="0")

        Checkbutton(settings, text="MOCK_MODE", variable=self.mock_var).grid(row=0, column=0, sticky=W)
        Checkbutton(settings, text="DISCORD_ENABLED", variable=self.discord_var).grid(row=0, column=1, sticky=W)
        Checkbutton(settings, text="EXPORT_TO_JSON", variable=self.export_var).grid(row=0, column=2, sticky=W)

        Label(settings, text="Min price (Ft):").grid(row=1, column=0, sticky=W, pady=5)
        Entry(settings, textvariable=self.min_price_var, width=12).grid(row=1, column=0, sticky=W, padx=(90, 0))

        Label(settings, text="Vinted URL:").grid(row=2, column=0, sticky=W, pady=5)
        self.url_var = StringVar()
        Entry(settings, textvariable=self.url_var, width=60).grid(row=2, column=1, columnspan=3, sticky=EW, padx=(5, 0))

        self.start_btn = Button(settings, text="Start", command=self.toggle_monitoring, width=12)
        self.start_btn.grid(row=1, column=2, sticky=E, padx=(0, 5))

        self.reset_btn = Button(settings, text="Reset DB & JSON", command=self.reset_data, width=14)
        self.reset_btn.grid(row=1, column=3, sticky=E)

        settings.columnconfigure(1, weight=1)

        # ── Log frame ──
        log_frame = Frame(self.root, padx=10, pady=10)
        log_frame.pack(fill=BOTH, expand=True)

        self.log_text = Text(log_frame, wrap="word", state="disabled", font=("Consolas", 9))
        scroll = Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=RIGHT, fill=Y)
        self.log_text.pack(fill=BOTH, expand=True)

    def _log(self, message):
        self.log_queue.put(message)

    def _poll_log_queue(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get_nowait()
            self.log_text.configure(state="normal")
            self.log_text.insert(END, msg + "\n")
            self.log_text.see(END)
            self.log_text.configure(state="disabled")
        self.root.after(100, self._poll_log_queue)

    def toggle_monitoring(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.stop_event.set()
            self.start_btn.configure(text="Stopping...", state="disabled")
        else:
            self.stop_event.clear()
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", END)
            self.log_text.configure(state="disabled")

            try:
                min_price = float(self.min_price_var.get())
            except ValueError:
                messagebox.showerror("Error", "Min price must be a number")
                return

            self.start_btn.configure(text="Stop", state="normal")

            self.worker_thread = threading.Thread(
                target=self._run_monitor,
                args=(min_price, self.stop_event),
                daemon=True,
            )
            self.worker_thread.start()

    def _run_monitor(self, min_price, stop_event):
        def log_func(msg):
            if stop_event.is_set():
                return
            self._log(msg)

        try:
            url = self.url_var.get().strip() or None
            main_loop(
                mock_mode=self.mock_var.get(),
                discord_enabled=self.discord_var.get(),
                export_json=self.export_var.get(),
                min_price=min_price,
                log_func=log_func,
                stop_event=stop_event,
                target_url=url,
            )
        except Exception as e:
            self._log(f"FATAL: {e}")
        finally:
            self.root.after(0, self._on_stop)

    def reset_data(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("Warning", "Stop monitoring first before resetting.")
            return
        if not messagebox.askyesno("Confirm", "Delete database and JSON export?"):
            return
        deleted = []
        for path, name in [(DB_PATH, "vinted_monitor.db"), (EXPORT_PATH, "found_items.json")]:
            if os.path.isfile(path):
                os.remove(path)
                deleted.append(name)
        if deleted:
            self._log(f"Deleted: {', '.join(deleted)}")
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", END)
            self.log_text.configure(state="disabled")
        else:
            self._log("Nothing to delete — files not found.")

    def _on_stop(self):
        self.start_btn.configure(text="Start", state="normal")
        self.stop_event.clear()
        self._log("Stopped.")


if __name__ == "__main__":
    root = Tk()
    VintedMonitorGUI(root)
    root.mainloop()
