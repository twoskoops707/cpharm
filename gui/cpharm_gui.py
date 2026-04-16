"""
CPharm GUI — Windows desktop app for managing the LDPlayer phone farm.
Requires: pip install customtkinter
Run:      python gui/cpharm_gui.py
"""

import customtkinter as ctk
import subprocess, threading, time, socket, os, webbrowser
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
LDPLAYER   = r"C:\LDPlayer\LDPlayer9\ldconsole.exe"
APK_DIR    = Path(__file__).parent.parent / "apks"
DASH_PY    = Path(__file__).parent.parent / "automation" / "dashboard.py"
REFRESH_S  = 5          # seconds between auto-refresh
RAM_PER_PH = 1.5        # GB per running phone

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

# ── LDPlayer helpers ────────────────────────────────────────────────────────────
def ld(*args) -> str:
    if not os.path.exists(LDPLAYER):
        return "ERROR: LDPlayer not found"
    try:
        r = subprocess.run([LDPLAYER, *args], capture_output=True, text=True, timeout=15)
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"

def list_phones():
    raw = ld("list2")
    phones = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        idx, name = int(parts[0]), parts[1]
        running_raw = ld("isrunning", "--index", str(idx))
        phones.append({
            "index": idx,
            "name":  name,
            "running": "running" in running_raw.lower(),
            "is_cpharm": name.lower().startswith("cpharm"),
        })
    return [p for p in phones if p["is_cpharm"]]

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "?.?.?.?"

# ── Dashboard server ─────────────────────────────────────────────────────────────
_dash_proc = None

def start_dashboard():
    global _dash_proc
    if _dash_proc and _dash_proc.poll() is None:
        return "already running"
    _dash_proc = subprocess.Popen(
        ["python", str(DASH_PY)],
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    return "started"

def stop_dashboard():
    global _dash_proc
    if _dash_proc and _dash_proc.poll() is None:
        _dash_proc.terminate()
        _dash_proc = None
        return "stopped"
    return "not running"

def dashboard_running():
    return _dash_proc is not None and _dash_proc.poll() is None

# ── Main App ──────────────────────────────────────────────────────────────────────
class CPharmApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("CPharm")
        self.geometry("780x620")
        self.minsize(680, 500)
        self.configure(fg_color="#0d1117")
        self._phones = []
        self._cards  = {}
        self._busy   = False
        self._build_ui()
        self._schedule_refresh()

    # ── UI construction ────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Header ──
        hdr = ctk.CTkFrame(self, height=56, fg_color="#080b0f", corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        logo = ctk.CTkLabel(hdr, text="[ C·PHARM ]",
                            font=ctk.CTkFont("Courier New", 18, "bold"),
                            text_color="#00e676")
        logo.pack(side="left", padx=20)

        self._ip_label = ctk.CTkLabel(hdr, text="ip: —",
                                      font=ctk.CTkFont("Courier New", 11),
                                      text_color="#5a6270")
        self._ip_label.pack(side="left", padx=8)

        self._refresh_btn = ctk.CTkButton(hdr, text="⟳ Refresh", width=90, height=32,
                                          fg_color="#1f2733", hover_color="#2a3140",
                                          text_color="#9198a1",
                                          command=lambda: self._run_bg(self._do_refresh))
        self._refresh_btn.pack(side="right", padx=16)

        self._dash_btn = ctk.CTkButton(hdr, text="◉ Dashboard", width=110, height=32,
                                       fg_color="#1f2733", hover_color="#2a3140",
                                       text_color="#9198a1",
                                       command=self._toggle_dashboard)
        self._dash_btn.pack(side="right", padx=4)

        # ── Stats bar ──
        stats = ctk.CTkFrame(self, height=62, fg_color="#0d1117", corner_radius=0)
        stats.pack(fill="x")
        stats.pack_propagate(False)

        sep = ctk.CTkFrame(stats, height=1, fg_color="#2a3140", corner_radius=0)
        sep.pack(fill="x")

        sbar = ctk.CTkFrame(stats, fg_color="transparent")
        sbar.pack(fill="both", expand=True, padx=16, pady=4)

        self._stat_total   = self._stat_widget(sbar, "PHONES",  "—")
        self._stat_running = self._stat_widget(sbar, "RUNNING", "—", "#00e676")
        self._stat_stopped = self._stat_widget(sbar, "STOPPED", "—")
        self._stat_ram     = self._stat_widget(sbar, "RAM EST", "—", "#ffd740")

        # ── Global controls ──
        ctrl = ctk.CTkFrame(self, height=52, fg_color="#0d1117", corner_radius=0)
        ctrl.pack(fill="x")

        sep2 = ctk.CTkFrame(ctrl, height=1, fg_color="#2a3140", corner_radius=0)
        sep2.pack(fill="x")

        cbar = ctk.CTkFrame(ctrl, fg_color="transparent")
        cbar.pack(fill="both", expand=True, padx=14, pady=6)

        ctk.CTkButton(cbar, text="▶  Start All", width=120, height=34,
                      fg_color="#00e676", hover_color="#00c853", text_color="#000",
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=lambda: self._run_bg(self._start_all)).pack(side="left", padx=(0, 8))

        ctk.CTkButton(cbar, text="■  Stop All", width=110, height=34,
                      fg_color="#ff5252", hover_color="#d32f2f", text_color="#fff",
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=lambda: self._run_bg(self._stop_all)).pack(side="left", padx=(0, 8))

        ctk.CTkButton(cbar, text="↺  Restart All", width=130, height=34,
                      fg_color="#1f2733", hover_color="#2a3140", text_color="#e6edf3",
                      command=lambda: self._run_bg(self._restart_all)).pack(side="left", padx=(0, 8))

        ctk.CTkButton(cbar, text="＋  Clone Phone", width=140, height=34,
                      fg_color="#1f2733", hover_color="#2a3140", text_color="#00e676",
                      command=lambda: self._run_bg(self._clone_phone)).pack(side="right")

        # ── Scrollable phone grid ──
        self._scroll = ctk.CTkScrollableFrame(self, fg_color="#080b0f", corner_radius=0)
        self._scroll.pack(fill="both", expand=True)
        self._scroll.grid_columnconfigure((0, 1), weight=1)

        # ── Status bar ──
        foot = ctk.CTkFrame(self, height=28, fg_color="#080b0f", corner_radius=0)
        foot.pack(fill="x")
        ctk.CTkFrame(foot, height=1, fg_color="#2a3140", corner_radius=0).pack(fill="x")
        self._status = ctk.CTkLabel(foot, text="Ready.",
                                    font=ctk.CTkFont("Courier New", 10),
                                    text_color="#5a6270")
        self._status.pack(side="left", padx=14)

        self._dot = ctk.CTkLabel(foot, text="●", text_color="#5a6270",
                                 font=ctk.CTkFont(size=10))
        self._dot.pack(side="right", padx=14)

    def _stat_widget(self, parent, label, val, color="#e6edf3"):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(side="left", padx=14, pady=2)
        v = ctk.CTkLabel(frame, text=val,
                         font=ctk.CTkFont("Courier New", 22, "bold"),
                         text_color=color)
        v.pack()
        ctk.CTkLabel(frame, text=label,
                     font=ctk.CTkFont(size=9),
                     text_color="#5a6270").pack()
        return v

    # ── Phone cards ────────────────────────────────────────────────────────────
    def _render_phones(self):
        # Remove stale cards
        existing = set(self._cards.keys())
        current  = {p["index"] for p in self._phones}
        for idx in existing - current:
            self._cards[idx].destroy()
            del self._cards[idx]

        for i, phone in enumerate(self._phones):
            idx = phone["index"]
            if idx in self._cards:
                self._update_card(self._cards[idx], phone)
            else:
                card = self._make_card(phone)
                card.grid(row=i // 2, column=i % 2, padx=8, pady=6, sticky="ew")
                self._cards[idx] = card

        # Update stats
        running = sum(1 for p in self._phones if p["running"])
        stopped = len(self._phones) - running
        ram = running * RAM_PER_PH
        self._stat_total.configure(text=str(len(self._phones)))
        self._stat_running.configure(text=str(running))
        self._stat_stopped.configure(text=str(stopped))
        self._stat_ram.configure(text=f"{ram:.1f}G")

    def _make_card(self, phone):
        running = phone["running"]
        border  = "#00e67630" if running else "#2a3140"
        card = ctk.CTkFrame(self._scroll, fg_color="#161b22",
                            border_color=border, border_width=1,
                            corner_radius=10)

        # Top row: dot + name + status
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 4))

        dot_color = "#00e676" if running else "#5a6270"
        dot = ctk.CTkLabel(top, text="●", text_color=dot_color,
                           font=ctk.CTkFont(size=11))
        dot.pack(side="left")
        card._dot = dot

        name_lbl = ctk.CTkLabel(top, text=phone["name"],
                                font=ctk.CTkFont("Courier New", 13, "bold"),
                                text_color="#e6edf3")
        name_lbl.pack(side="left", padx=8)
        card._name = name_lbl

        status_text = "RUNNING" if running else "stopped"
        status_color = "#00e676" if running else "#5a6270"
        status_lbl = ctk.CTkLabel(top, text=status_text,
                                  font=ctk.CTkFont("Courier New", 10),
                                  text_color=status_color)
        status_lbl.pack(side="left")
        card._status = status_lbl
        card._phone_running = running

        # Pills row
        pills = ctk.CTkFrame(card, fg_color="transparent")
        pills.pack(fill="x", padx=14, pady=(0, 8))

        self._pill(pills, f"idx {phone['index']}")
        self._pill(pills, "ADB", "#00e67614", "#00e676" if running else "#5a6270")
        if running:
            self._pill(pills, "~1.5 GB", "#ffd74014", "#ffd740")

        # Action button
        btn_text  = "Stop"  if running else "Start"
        btn_fg    = "#ff5252" if running else "#00e676"
        btn_hover = "#d32f2f" if running else "#00c853"
        btn_tc    = "#fff" if running else "#000"
        btn = ctk.CTkButton(card, text=btn_text, width=80, height=30,
                            fg_color=btn_fg, hover_color=btn_hover,
                            text_color=btn_tc,
                            font=ctk.CTkFont(size=12, weight="bold"),
                            command=lambda i=phone["index"], r=running:
                                self._run_bg(lambda: self._toggle_phone(i, r)))
        btn.pack(anchor="e", padx=14, pady=(0, 12))
        card._btn = btn

        return card

    def _update_card(self, card, phone):
        running = phone["running"]
        if getattr(card, "_phone_running", None) == running:
            return  # no change
        card._phone_running = running
        color = "#00e676" if running else "#5a6270"
        card._dot.configure(text_color=color)
        card._status.configure(text="RUNNING" if running else "stopped", text_color=color)
        card.configure(border_color="#00e67630" if running else "#2a3140")
        card._btn.configure(
            text="Stop" if running else "Start",
            fg_color="#ff5252" if running else "#00e676",
            hover_color="#d32f2f" if running else "#00c853",
            text_color="#fff" if running else "#000",
            command=lambda i=phone["index"], r=running:
                self._run_bg(lambda: self._toggle_phone(i, r))
        )

    def _pill(self, parent, text, bg="#1f2733", fg="#5a6270"):
        ctk.CTkLabel(parent, text=text, font=ctk.CTkFont("Courier New", 9),
                     fg_color=bg, text_color=fg,
                     corner_radius=4, padx=6, pady=2).pack(side="left", padx=(0, 4))

    # ── Actions ────────────────────────────────────────────────────────────────
    def _do_refresh(self):
        self._set_status("Refreshing…")
        self._phones = list_phones()
        self.after(0, self._render_phones)
        ip = get_local_ip()
        self.after(0, lambda: self._ip_label.configure(text=f"ip: {ip}"))
        self._set_status(f"Refreshed — {len(self._phones)} phones")

    def _start_all(self):
        self._set_status("Starting all phones…")
        for p in self._phones:
            if not p["running"]:
                ld("launch", "--index", str(p["index"]))
        time.sleep(2)
        self._do_refresh()

    def _stop_all(self):
        self._set_status("Stopping all phones…")
        for p in self._phones:
            if p["running"]:
                ld("quit", "--index", str(p["index"]))
        time.sleep(1)
        self._do_refresh()

    def _restart_all(self):
        self._stop_all()
        time.sleep(2)
        self._start_all()

    def _toggle_phone(self, index, currently_running):
        name = f"CPharm-{index}"
        if currently_running:
            self._set_status(f"Stopping {name}…")
            ld("quit", "--index", str(index))
        else:
            self._set_status(f"Starting {name}…")
            ld("launch", "--index", str(index))
        time.sleep(1)
        self._do_refresh()

    def _clone_phone(self):
        n = len(self._phones) + 1
        name = f"CPharm-{n}"
        self._set_status(f"Cloning {name}…")
        ld("copy", "--name", name, "--from", "0")
        time.sleep(3)
        self._do_refresh()

    def _toggle_dashboard(self):
        if dashboard_running():
            stop_dashboard()
            self._dash_btn.configure(text="◉ Dashboard", text_color="#9198a1")
            self._set_status("Dashboard stopped.")
        else:
            start_dashboard()
            time.sleep(1)
            ip = get_local_ip()
            self._dash_btn.configure(text="◉ Dashboard  ON", text_color="#00e676")
            self._set_status(f"Dashboard on http://{ip}:8080")
            webbrowser.open(f"http://localhost:8080")

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _run_bg(self, fn):
        """Run fn in background thread so UI stays responsive."""
        def wrapped():
            self._set_busy(True)
            try:    fn()
            finally: self._set_busy(False)
        threading.Thread(target=wrapped, daemon=True).start()

    def _set_busy(self, on):
        self._busy = on
        color = "#00e676" if on else "#5a6270"
        self.after(0, lambda: self._dot.configure(text_color=color))

    def _set_status(self, msg):
        self.after(0, lambda: self._status.configure(text=msg))

    def _schedule_refresh(self):
        self._run_bg(self._do_refresh)
        self.after(REFRESH_S * 1000, self._schedule_refresh)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = CPharmApp()
    app.mainloop()
