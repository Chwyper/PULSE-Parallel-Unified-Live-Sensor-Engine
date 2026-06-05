# =============================================================================
#  dashboard.py — PULSE: Parallel Unified Live Sensor Engine
#  SCADA-style Tkinter GUI, dark theme, real-time multi-sensor visualization.
# =============================================================================

import multiprocessing
import os
import threading
import time
from collections import deque

# pyrefly: ignore [missing-import]
import matplotlib
matplotlib.use("TkAgg")
# pyrefly: ignore [missing-import]
import matplotlib.gridspec as gridspec
# pyrefly: ignore [missing-import]
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
# pyrefly: ignore [missing-import]
from matplotlib.figure import Figure

import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

import config
from config import (
    SessionPhase, PHASE_DURATION_S, QUEUE_MAX_SIZE, QUEUE_NAMES,
    NODE_CONFIG, NUM_WORKER_PROCESSES, UPDATE_INTERVAL_MS, CHART_WINDOW,
    COLOR_BG, COLOR_PANEL, COLOR_TEXT, COLOR_ACCENT,
    COLOR_NORMAL, COLOR_WARNING, COLOR_CRITICAL,
    COLOR_ECG, COLOR_ACCEL, COLOR_SPO2, COLOR_ENV,
    ANOMALY_THRESHOLDS, PROJECT_NAME, PROJECT_FULL, VERSION, INSTITUTION,
)

# =============================================================================
#  Constants
# =============================================================================

_FONT_TITLE  = ("Segoe UI", 13, "bold")
_FONT_HEADER = ("Segoe UI", 9,  "bold")
_FONT_BODY   = ("Segoe UI", 9)
_FONT_MONO   = ("Consolas", 8)
_FONT_VALUE  = ("Segoe UI", 11, "bold")
_FONT_SMALL  = ("Segoe UI", 8)

_PHASE_COLORS = {
    "RESTING":  COLOR_ACCENT,    # biru
    "WARMUP":   COLOR_NORMAL,    # hijau
    "SPRINT":   COLOR_CRITICAL,  # merah
    "COOLDOWN": COLOR_WARNING,   # amber
    "RECOVERY": "#bc8cff",       # ungu
}

_EXERTION_COLORS = {
    "LOW":      COLOR_NORMAL,
    "MODERATE": COLOR_ACCENT,
    "HIGH":     COLOR_WARNING,
    "EXTREME":  COLOR_CRITICAL,
}

_ECG_BUF_SIZE   = 500   # ~1 s di 500 Hz
_METRIC_BUF     = 100   # data points untuk HR/SpO2/Accel/Resp


# =============================================================================
#  CLASS: PulseDashboard
# =============================================================================

class PulseDashboard:

    def __init__(
        self, root,
        queue_manager, logger,
        sensor_nodes_dict,
        session_controller,
        fusion_engine,
        benchmarker,
        shared_state,
    ):
        self.root      = root
        self._qm       = queue_manager
        self._logger   = logger
        self._nodes    = sensor_nodes_dict
        self._ctrl     = session_controller
        self._engine   = fusion_engine
        self._bench    = benchmarker
        self._shared   = shared_state

        # --- Buffers untuk chart ---
        self._ecg_buf   = deque([0.0] * _ECG_BUF_SIZE, maxlen=_ECG_BUF_SIZE)
        self._hr_buf    = deque([70.0]  * _METRIC_BUF, maxlen=_METRIC_BUF)
        self._spo2_buf  = deque([98.0]  * _METRIC_BUF, maxlen=_METRIC_BUF)
        self._accel_buf = deque([0.1]   * _METRIC_BUF, maxlen=_METRIC_BUF)
        self._resp_buf  = deque([15.0]  * _METRIC_BUF, maxlen=_METRIC_BUF)

        # Flash state untuk CRITICAL alerts (toggle 500ms)
        self._flash_on      = True
        self._flash_tick    = 0

        # Threading
        self._session_thread = None
        self._bench_thread   = None
        self._bench_results  = None

        # Node last-seen tracker (untuk status dots)
        self._node_last_seen: dict[str, float] = {n: 0.0 for n in NODE_CONFIG}

        # Recent anomalies (3 terbaru)
        self._recent_anomalies: list[str] = []

        # Session running flag
        self._running = False
        self._paused  = False

        # Event log buffer
        self._event_buf: deque[str] = deque(maxlen=200)

        self._build_ui()
        self._start_update_loop()

    # =========================================================================
    #  UI Builder
    # =========================================================================

    def _build_ui(self):
        self.root.title(f"PULSE v{VERSION} — Athlete Monitor")
        self.root.geometry(config.WINDOW_SIZE)
        self.root.configure(bg=COLOR_BG)
        
        # Maximize window di Windows
        try:
            self.root.state('zoomed')
        except Exception:
            pass
            
        # Toggle Fullscreen dengan F11, keluar dengan Escape
        self.root.bind("<F11>", lambda event: self.root.attributes("-fullscreen", not self.root.attributes("-fullscreen")))
        self.root.bind("<Escape>", lambda event: self.root.attributes("-fullscreen", False))

        self._build_header()

        # Body (3 columns)
        body = tk.Frame(self.root, bg=COLOR_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 2))

        self._build_left_panel(body)
        self._build_center_panel(body)
        self._build_right_panel(body)

        self._build_phase_bar()
        self._build_buttons()

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self):
        hdr = tk.Frame(self.root, bg="#0a0e14", height=52)
        hdr.pack(fill=tk.X, side=tk.TOP)
        hdr.pack_propagate(False)

        tk.Label(
            hdr, text=f"  \U0001f493  {PROJECT_NAME} — {PROJECT_FULL}",
            bg="#0a0e14", fg=COLOR_TEXT, font=_FONT_TITLE,
        ).pack(side=tk.LEFT, padx=10, pady=10)

        # Timer
        self._lbl_timer = tk.Label(
            hdr, text="00:00", bg="#0a0e14", fg=COLOR_ACCENT,
            font=("Consolas", 13, "bold"),
        )
        self._lbl_timer.pack(side=tk.RIGHT, padx=12, pady=10)

        # Phase badge (canvas pill)
        self._badge_canvas = tk.Canvas(
            hdr, width=110, height=28, bg="#0a0e14", highlightthickness=0,
        )
        self._badge_canvas.pack(side=tk.RIGHT, padx=6, pady=12)
        self._badge_rect = self._badge_canvas.create_rectangle(
            2, 2, 108, 26, fill=COLOR_ACCENT, outline="", tags="badge",
        )
        self._badge_text = self._badge_canvas.create_text(
            55, 14, text="RESTING", fill="#000000",
            font=("Segoe UI", 9, "bold"), tags="badge",
        )

        tk.Label(
            hdr, text="ITENAS IFB-206", bg="#0a0e14",
            fg="#484f58", font=_FONT_SMALL,
        ).pack(side=tk.RIGHT, padx=4, pady=10)

    # ── Left Panel — Athlete Status ───────────────────────────────────────────

    def _build_left_panel(self, body):
        lf = tk.Frame(body, bg=COLOR_PANEL, width=258)
        lf.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 3), pady=0)
        lf.pack_propagate(False)

        self._mk_section_label(lf, "ATHLETE STATUS")

        # Metric rows
        self._metric_labels: dict[str, tk.Label] = {}
        metrics = [
            ("HR",       "heart_rate_bpm",  "bpm"),
            ("SpO2",     "spo2_pct",        "%"),
            ("Steps",    "steps_total",     ""),
            ("Cadence",  "cadence_spm",     "spm"),
            ("Resp",     "resp_rate_bpm",   "br/m"),
            ("Temp",     "skin_temp_c",     "\u00b0C"),
            ("GSR",      "gsr_kohm",        "k\u03a9"),
            ("Stress",   "stress_index",    ""),
        ]
        for label, key, unit in metrics:
            row = tk.Frame(lf, bg=COLOR_PANEL)
            row.pack(fill=tk.X, padx=10, pady=2)
            tk.Label(row, text=f"{label}:", bg=COLOR_PANEL, fg="#6e7681",
                     font=_FONT_HEADER, width=8, anchor="w").pack(side=tk.LEFT)
            val_lbl = tk.Label(row, text="---", bg=COLOR_PANEL,
                               fg=COLOR_NORMAL, font=_FONT_VALUE, width=7, anchor="e")
            val_lbl.pack(side=tk.LEFT)
            tk.Label(row, text=unit, bg=COLOR_PANEL, fg="#6e7681",
                     font=_FONT_SMALL, width=4).pack(side=tk.LEFT)
            self._metric_labels[key] = val_lbl

        # Divider
        ttk.Separator(lf, orient="horizontal").pack(fill=tk.X, padx=8, pady=6)

        # Exertion badge
        tk.Label(lf, text="EXERTION LEVEL", bg=COLOR_PANEL, fg="#6e7681",
                 font=_FONT_SMALL).pack(anchor="w", padx=10)
        self._exertion_frame = tk.Frame(lf, bg=COLOR_NORMAL, padx=12, pady=4)
        self._exertion_frame.pack(padx=10, pady=4, fill=tk.X)
        self._exertion_lbl = tk.Label(
            self._exertion_frame, text="LOW", bg=COLOR_NORMAL,
            fg="#000000", font=("Segoe UI", 10, "bold"),
        )
        self._exertion_lbl.pack()

        # Divider
        ttk.Separator(lf, orient="horizontal").pack(fill=tk.X, padx=8, pady=6)

        # Anomaly list
        tk.Label(lf, text="RECENT ANOMALIES", bg=COLOR_PANEL, fg="#6e7681",
                 font=_FONT_SMALL).pack(anchor="w", padx=10)
        self._anomaly_lbls: list[tk.Label] = []
        for _ in range(3):
            al = tk.Label(lf, text="", bg=COLOR_PANEL, fg=COLOR_WARNING,
                          font=_FONT_SMALL, anchor="w", wraplength=230)
            al.pack(anchor="w", padx=10, pady=1)
            self._anomaly_lbls.append(al)

        # Emergency banner (hidden initially)
        self._emrg_frame = tk.Frame(lf, bg=COLOR_CRITICAL)
        self._emrg_lbl   = tk.Label(
            self._emrg_frame, text="\u26a0 EMERGENCY \u26a0",
            bg=COLOR_CRITICAL, fg="white", font=("Segoe UI", 10, "bold"),
        )
        self._emrg_lbl.pack(pady=4)

    # ── Center Panel — Live Charts ────────────────────────────────────────────

    def _build_center_panel(self, body):
        cf = tk.Frame(body, bg=COLOR_BG)
        cf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3, pady=0)

        self._mk_section_label(cf, "LIVE ECG + METRICS")

        # Matplotlib figure
        self._fig = Figure(figsize=(6.8, 5.4), facecolor=COLOR_BG, dpi=96)
        gs  = gridspec.GridSpec(3, 1, figure=self._fig, hspace=0.55,
                                left=0.09, right=0.91, top=0.97, bottom=0.06)
        self._ax_ecg   = self._fig.add_subplot(gs[0])
        self._ax_hr    = self._fig.add_subplot(gs[1])
        self._ax_accel = self._fig.add_subplot(gs[2])

        # ECG subplot
        self._ax_ecg.set_facecolor(COLOR_PANEL)
        self._ax_ecg.set_title("ECG Lead-II", color=COLOR_TEXT, fontsize=8, loc="left")
        self._ax_ecg.set_ylim(-1.5, 2.0)
        self._ax_ecg.set_xlim(0, _ECG_BUF_SIZE)
        self._ax_ecg.tick_params(colors=COLOR_TEXT, labelsize=7)
        self._ax_ecg.set_ylabel("mV", color="#6e7681", fontsize=7)
        self._ecg_line, = self._ax_ecg.plot(
            list(range(_ECG_BUF_SIZE)), list(self._ecg_buf),
            color=COLOR_ECG, linewidth=0.7, alpha=0.9,
        )

        # HR + SpO2 subplot (dual axis)
        self._ax_hr.set_facecolor(COLOR_PANEL)
        self._ax_hr.set_title("Heart Rate / SpO2", color=COLOR_TEXT, fontsize=8, loc="left")
        self._ax_hr.set_ylim(40, 220)
        self._ax_hr.set_xlim(0, _METRIC_BUF)
        self._ax_hr.tick_params(colors=COLOR_SPO2, labelsize=7)
        self._ax_hr.set_ylabel("HR (bpm)", color=COLOR_SPO2, fontsize=7)
        self._hr_line, = self._ax_hr.plot(
            list(range(_METRIC_BUF)), list(self._hr_buf),
            color=COLOR_SPO2, linewidth=1.2, label="HR",
        )
        self._ax_spo2 = self._ax_hr.twinx()
        self._ax_spo2.set_facecolor(COLOR_PANEL)
        self._ax_spo2.set_ylim(85, 101)
        self._ax_spo2.tick_params(colors=COLOR_ACCENT, labelsize=7)
        self._ax_spo2.set_ylabel("SpO2 (%)", color=COLOR_ACCENT, fontsize=7)
        self._spo2_line, = self._ax_spo2.plot(
            list(range(_METRIC_BUF)), list(self._spo2_buf),
            color=COLOR_ACCENT, linewidth=1.2, linestyle="--", label="SpO2",
        )

        # Accel + Resp subplot (dual axis)
        self._ax_accel.set_facecolor(COLOR_PANEL)
        self._ax_accel.set_title("Acceleration / Resp Rate", color=COLOR_TEXT, fontsize=8, loc="left")
        self._ax_accel.set_ylim(0, 3.0)
        self._ax_accel.set_xlim(0, _METRIC_BUF)
        self._ax_accel.tick_params(colors=COLOR_ACCEL, labelsize=7)
        self._ax_accel.set_ylabel("Accel (g)", color=COLOR_ACCEL, fontsize=7)
        self._accel_line, = self._ax_accel.plot(
            list(range(_METRIC_BUF)), list(self._accel_buf),
            color=COLOR_ACCEL, linewidth=1.1, label="Accel",
        )
        self._ax_resp = self._ax_accel.twinx()
        self._ax_resp.set_facecolor(COLOR_PANEL)
        self._ax_resp.set_ylim(0, 55)
        self._ax_resp.tick_params(colors=COLOR_ENV, labelsize=7)
        self._ax_resp.set_ylabel("Resp (br/m)", color=COLOR_ENV, fontsize=7)
        self._resp_line, = self._ax_resp.plot(
            list(range(_METRIC_BUF)), list(self._resp_buf),
            color=COLOR_ENV, linewidth=1.1, linestyle="--", label="Resp",
        )

        # Style all spines
        for ax in [self._ax_ecg, self._ax_hr, self._ax_spo2, self._ax_accel, self._ax_resp]:
            for spine in ax.spines.values():
                spine.set_edgecolor("#30363d")

        self._canvas = FigureCanvasTkAgg(self._fig, master=cf)
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._canvas.draw()

    # ── Right Panel — System Monitor ──────────────────────────────────────────

    def _build_right_panel(self, body):
        rf = tk.Frame(body, bg=COLOR_PANEL, width=298)
        rf.pack(side=tk.LEFT, fill=tk.Y, padx=(3, 0), pady=0)
        rf.pack_propagate(False)

        self._mk_section_label(rf, "SYSTEM MONITOR")

        # Queue size bars
        tk.Label(rf, text="QUEUE SIZES", bg=COLOR_PANEL, fg="#6e7681",
                 font=_FONT_SMALL).pack(anchor="w", padx=10, pady=(4, 0))
        self._queue_bars: dict[str, ttk.Progressbar] = {}
        self._queue_lbl:  dict[str, tk.Label] = {}
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Queue.Horizontal.TProgressbar",
                        troughcolor=COLOR_BG, background=COLOR_ACCENT,
                        bordercolor=COLOR_BG, lightcolor=COLOR_ACCENT,
                        darkcolor=COLOR_ACCENT, thickness=10)
        for qname in QUEUE_NAMES:
            row = tk.Frame(rf, bg=COLOR_PANEL)
            row.pack(fill=tk.X, padx=10, pady=1)
            tk.Label(row, text=qname[:10], bg=COLOR_PANEL, fg=COLOR_TEXT,
                     font=_FONT_SMALL, width=11, anchor="w").pack(side=tk.LEFT)
            pb = ttk.Progressbar(row, maximum=QUEUE_MAX_SIZE, length=110,
                                 style="Queue.Horizontal.TProgressbar")
            pb.pack(side=tk.LEFT, padx=3)
            lbl = tk.Label(row, text="0", bg=COLOR_PANEL, fg="#6e7681",
                           font=_FONT_SMALL, width=3)
            lbl.pack(side=tk.LEFT)
            self._queue_bars[qname] = pb
            self._queue_lbl[qname]  = lbl

        ttk.Separator(rf, orient="horizontal").pack(fill=tk.X, padx=8, pady=6)

        # Node status dots
        tk.Label(rf, text="NODE STATUS", bg=COLOR_PANEL, fg="#6e7681",
                 font=_FONT_SMALL).pack(anchor="w", padx=10, pady=(0, 2))
        dot_canvas = tk.Canvas(rf, bg=COLOR_PANEL, height=90,
                               highlightthickness=0)
        dot_canvas.pack(fill=tk.X, padx=10, pady=2)
        self._node_dots: dict[str, int] = {}
        node_ids = list(NODE_CONFIG.keys())
        cols, rows = 4, 2
        dot_r = 8
        for i, nid in enumerate(node_ids):
            col = i % cols
            row = i // cols
            cx  = 20 + col * 68
            cy  = 14 + row * 40
            oval = dot_canvas.create_oval(
                cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r,
                fill=COLOR_NORMAL, outline="#30363d",
            )
            dot_canvas.create_text(cx, cy + dot_r + 8, text=nid.replace("NODE-", ""),
                                   fill="#6e7681", font=_FONT_SMALL)
            self._node_dots[nid] = oval
        self._dot_canvas = dot_canvas

        ttk.Separator(rf, orient="horizontal").pack(fill=tk.X, padx=8, pady=4)

        # Stats row (latency, throughput, packet loss)
        stats_f = tk.Frame(rf, bg=COLOR_PANEL)
        stats_f.pack(fill=tk.X, padx=10, pady=2)
        self._lbl_latency    = self._stat_row(stats_f, "Latency")
        self._lbl_throughput = self._stat_row(stats_f, "Throughput")
        self._lbl_pkt_loss   = self._stat_row(stats_f, "Packet Loss")

        ttk.Separator(rf, orient="horizontal").pack(fill=tk.X, padx=8, pady=4)

        # Benchmark panel (initially shows placeholder)
        tk.Label(rf, text="BENCHMARK", bg=COLOR_PANEL, fg="#6e7681",
                 font=_FONT_SMALL).pack(anchor="w", padx=10)
        bench_f = tk.Frame(rf, bg=COLOR_BG, padx=8, pady=6)
        bench_f.pack(fill=tk.X, padx=8, pady=4)
        self._bench_labels: dict[str, tk.Label] = {}
        for key in ("Seq", "Par", "Speedup", "Efficiency"):
            row = tk.Frame(bench_f, bg=COLOR_BG)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=f"{key}:", bg=COLOR_BG, fg="#6e7681",
                     font=_FONT_SMALL, width=10, anchor="w").pack(side=tk.LEFT)
            lbl = tk.Label(row, text="—", bg=COLOR_BG, fg=COLOR_TEXT,
                           font=("Consolas", 8, "bold"), anchor="w")
            lbl.pack(side=tk.LEFT)
            self._bench_labels[key] = lbl

        ttk.Separator(rf, orient="horizontal").pack(fill=tk.X, padx=8, pady=4)

        # Event log
        tk.Label(rf, text="EVENT LOG", bg=COLOR_PANEL, fg="#6e7681",
                 font=_FONT_SMALL).pack(anchor="w", padx=10)
        self._event_log = ScrolledText(
            rf, height=9, bg=COLOR_BG, fg=COLOR_TEXT,
            font=_FONT_MONO, state=tk.DISABLED, relief="flat",
            insertbackground=COLOR_TEXT, selectbackground=COLOR_ACCENT,
        )
        self._event_log.pack(fill=tk.BOTH, expand=True, padx=6, pady=(2, 6))
        # Tag colors
        self._event_log.tag_configure("WARNING",  foreground=COLOR_WARNING)
        self._event_log.tag_configure("CRITICAL", foreground=COLOR_CRITICAL)
        self._event_log.tag_configure("RECOVERY", foreground=COLOR_NORMAL)
        self._event_log.tag_configure("BENCHMARK",foreground="#96d0ff")
        self._event_log.tag_configure("DATA",     foreground="#6e7681")
        self._event_log.tag_configure("PHASE",    foreground=COLOR_ACCENT)

    # ── Phase Progress Bar ────────────────────────────────────────────────────

    def _build_phase_bar(self):
        pf = tk.Frame(self.root, bg=COLOR_BG, height=38)
        pf.pack(fill=tk.X, padx=4, pady=(2, 2))
        pf.pack_propagate(False)

        self._phase_canvas = tk.Canvas(
            pf, height=34, bg="#0a0e14",
            highlightthickness=1, highlightbackground="#30363d",
        )
        self._phase_canvas.pack(fill=tk.X, expand=True, padx=4, pady=2)

        # Created after canvas is visible so winfo_width works in update
        self._phase_rect = self._phase_canvas.create_rectangle(
            0, 0, 0, 34, fill=COLOR_ACCENT, outline="",
        )
        self._phase_txt = self._phase_canvas.create_text(
            640, 17, text="RESTING  (30s remaining)",
            fill=COLOR_TEXT, font=("Segoe UI", 9, "bold"),
        )

    # ── Control Buttons ───────────────────────────────────────────────────────

    def _build_buttons(self):
        bf = tk.Frame(self.root, bg="#0a0e14", height=48)
        bf.pack(fill=tk.X, side=tk.BOTTOM)
        bf.pack_propagate(False)

        btn_cfg = dict(font=("Segoe UI", 9, "bold"), relief="flat",
                       padx=12, pady=6, cursor="hand2")

        self._btn_start = tk.Button(
            bf, text="\u25b6 START", bg=COLOR_NORMAL, fg="#000000",
            command=self._on_start, **btn_cfg,
        )
        self._btn_start.pack(side=tk.LEFT, padx=(8, 3), pady=8)

        self._btn_pause = tk.Button(
            bf, text="\u23f8 PAUSE", bg=COLOR_ACCENT, fg="#000000",
            command=self._on_pause, state=tk.DISABLED, **btn_cfg,
        )
        self._btn_pause.pack(side=tk.LEFT, padx=3, pady=8)

        self._btn_stop = tk.Button(
            bf, text="\u23f9 STOP", bg=COLOR_CRITICAL, fg="white",
            command=self._on_stop, state=tk.DISABLED, **btn_cfg,
        )
        self._btn_stop.pack(side=tk.LEFT, padx=3, pady=8)

        tk.Button(
            bf, text="\U0001f4ca BENCHMARK", bg=COLOR_WARNING, fg="#000000",
            command=self._on_benchmark, **btn_cfg,
        ).pack(side=tk.LEFT, padx=3, pady=8)

        tk.Button(
            bf, text="\U0001f4be SAVE LOG", bg=COLOR_PANEL, fg=COLOR_TEXT,
            command=self._on_save_log, **btn_cfg,
        ).pack(side=tk.LEFT, padx=3, pady=8)

        tk.Button(
            bf, text="\u2753 INFO", bg=COLOR_PANEL, fg=COLOR_TEXT,
            command=self._on_info, **btn_cfg,
        ).pack(side=tk.RIGHT, padx=(3, 8), pady=8)

    # =========================================================================
    #  Update Loop
    # =========================================================================

    def _start_update_loop(self):
        self.root.after(UPDATE_INTERVAL_MS, self._update_loop)

    def _update_loop(self):
        try:
            self._update_timer()
            self._update_metrics()
            self._update_charts()
            self._update_node_status()
            self._update_queue_bars()
            self._update_phase_bar()
            self._flash_critical()
            self._update_emergency_banner()
        except Exception:
            pass   # Dashboard tidak crash karena error update
        finally:
            self.root.after(UPDATE_INTERVAL_MS, self._update_loop)

    # ── Timer ─────────────────────────────────────────────────────────────────

    def _update_timer(self):
        if self._ctrl.session_start_time:
            elapsed = time.monotonic() - self._ctrl.session_start_time
            m, s = divmod(int(elapsed), 60)
            self._lbl_timer.config(text=f"{m:02d}:{s:02d}")

    # ── Metrics ───────────────────────────────────────────────────────────────

    def _update_metrics(self):
        s = self._shared

        def _get(key, default=0.0):
            v = s.get(f"fused.{key}", s.get(key, default))
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        hr     = _get("heart_rate_bpm", 70)
        spo2   = _get("spo2_pct",       98)
        steps  = _get("steps_total",    0)
        cad    = _get("cadence_spm",    0)
        resp   = _get("resp_rate_bpm",  15)
        temp   = _get("skin_temp_c",    36.5)
        gsr    = _get("gsr_kohm",       50)
        stress = _get("stress_index",   0)

        # Dynamic color per metric
        def _hr_color():
            if hr > 200: return COLOR_CRITICAL
            if hr > 185: return COLOR_WARNING
            return COLOR_NORMAL

        def _spo2_color():
            if spo2 < 90:  return COLOR_CRITICAL
            if spo2 < 94:  return COLOR_WARNING
            return COLOR_NORMAL

        def _temp_color():
            if temp > 40.5: return COLOR_CRITICAL
            if temp > 39.5: return COLOR_WARNING
            return COLOR_NORMAL

        def _gsr_color():
            if gsr < 3:  return COLOR_WARNING
            return COLOR_NORMAL

        def _resp_color():
            if resp > 50: return COLOR_CRITICAL
            if resp > 40: return COLOR_WARNING
            return COLOR_NORMAL

        updates = {
            "heart_rate_bpm": (f"{hr:.0f}",    _hr_color()),
            "spo2_pct":       (f"{spo2:.1f}",  _spo2_color()),
            "steps_total":    (f"{int(steps)}", COLOR_NORMAL),
            "cadence_spm":    (f"{cad:.1f}",   COLOR_NORMAL),
            "resp_rate_bpm":  (f"{resp:.1f}",  _resp_color()),
            "skin_temp_c":    (f"{temp:.1f}",  _temp_color()),
            "gsr_kohm":       (f"{gsr:.1f}",   _gsr_color()),
            "stress_index":   (f"{stress:.1f}",COLOR_NORMAL),
        }
        for key, (text, color) in updates.items():
            lbl = self._metric_labels.get(key)
            if lbl:
                lbl.config(text=text, fg=color)

        # Exertion badge
        exertion = s.get("fused.exertion_level", "LOW")
        ex_color = _EXERTION_COLORS.get(str(exertion), COLOR_NORMAL)
        self._exertion_frame.config(bg=ex_color)
        self._exertion_lbl.config(bg=ex_color, text=str(exertion),
                                  fg="#000000" if ex_color != COLOR_CRITICAL else "white")

        # Anomaly list from shared state
        anom_count = int(s.get("fused.anomaly_count", 0))
        if anom_count and self._ctrl.anomaly_count > len(self._recent_anomalies):
            # Grab latest phase anomaly string
            phase = s.get("phase", "?")
            anom_str = f"[{phase}] anomalies: {anom_count}"
            if anom_str not in self._recent_anomalies:
                self._recent_anomalies.append(anom_str)
                if len(self._recent_anomalies) > 3:
                    self._recent_anomalies.pop(0)
        for i, lbl in enumerate(self._anomaly_lbls):
            if i < len(self._recent_anomalies):
                lbl.config(text=self._recent_anomalies[i], fg=COLOR_WARNING)
            else:
                lbl.config(text="")

        # System stats
        q_stats = self._qm.get_stats()
        lat = q_stats["avg_queue_latency_ms"]
        pkt = q_stats["total_packet_loss"]
        total = q_stats["total_enqueued"]
        tput = total / max(
            (time.monotonic() - (self._ctrl.session_start_time or time.monotonic())), 1
        )
        self._lbl_latency.config(text=f"{lat:.2f} ms")
        self._lbl_throughput.config(text=f"{tput:.1f}/s")
        self._lbl_pkt_loss.config(text=f"{pkt}")

        # Benchmark panel update (if results available)
        if self._bench_results:
            r = self._bench_results
            self._bench_labels["Seq"].config(
                text=f"{r['sequential']['avg_total_time_s']:.3f} s", fg=COLOR_WARNING)
            self._bench_labels["Par"].config(
                text=f"{r['parallel']['avg_total_time_s']:.3f} s", fg=COLOR_NORMAL)
            self._bench_labels["Speedup"].config(
                text=f"{r['speedup']:.2f} x", fg=COLOR_ACCENT)
            self._bench_labels["Efficiency"].config(
                text=f"{r['amdahl']['efficiency_pct']:.1f} %", fg=COLOR_TEXT)

        # Append to event log buffer
        if self._running:
            phase = s.get("phase", "RESTING")
            entry = (
                f"[{_ts_short()}] [{phase}] "
                f"HR={hr:.0f} SpO2={spo2:.1f}% Stress={stress:.0f}"
            )
            if not self._event_buf or self._event_buf[-1] != entry:
                self._event_buf.append(entry)
                self._append_event_log(entry, "DATA")

    # ── Charts ────────────────────────────────────────────────────────────────

    def _update_charts(self):
        s = self._shared

        # ECG: add simulated ECG samples to buffer
        if self._running:
            from sensor_nodes import generate_ecg_sample
            phase_name = s.get("phase", "RESTING")
            try:
                phase = SessionPhase[phase_name]
            except KeyError:
                phase = SessionPhase.RESTING
            # Add ~8 new ECG samples per 400ms tick (simulating continuous stream)
            t_now = time.perf_counter()
            for i in range(8):
                val = generate_ecg_sample(phase, "Lead-II", t_now + i * 0.002)
                self._ecg_buf.append(val)

        # HR / SpO2 buffers
        hr   = float(s.get("fused.heart_rate_bpm", s.get("heart_rate_bpm", 70)))
        spo2 = float(s.get("fused.spo2_pct",        s.get("spo2_pct", 98)))
        accel= float(s.get("fused.accel_magnitude_g", s.get("accel_magnitude_g", 0.1)))
        resp = float(s.get("fused.resp_rate_bpm",    s.get("resp_rate_bpm", 15)))

        self._hr_buf.append(hr)
        self._spo2_buf.append(spo2)
        self._accel_buf.append(accel)
        self._resp_buf.append(resp)

        x_ecg  = list(range(_ECG_BUF_SIZE))
        x_met  = list(range(_METRIC_BUF))

        self._ecg_line.set_data(x_ecg, list(self._ecg_buf))
        self._hr_line.set_data(x_met,  list(self._hr_buf))
        self._spo2_line.set_data(x_met, list(self._spo2_buf))
        self._accel_line.set_data(x_met, list(self._accel_buf))
        self._resp_line.set_data(x_met,  list(self._resp_buf))

        # Autoscale Y for HR subplot
        hr_vals = list(self._hr_buf)
        if max(hr_vals) > 0:
            lo = max(30, min(hr_vals) - 10)
            hi = min(230, max(hr_vals) + 15)
            self._ax_hr.set_ylim(lo, hi)

        self._canvas.draw_idle()

    # ── Node Status Dots ──────────────────────────────────────────────────────

    def _update_node_status(self):
        now = time.monotonic()
        engine_tracker = getattr(self._engine, "node_timeout_tracker", {})

        for nid, oval_id in self._node_dots.items():
            last = engine_tracker.get(nid, 0)
            age  = now - last if last > 0 else 999

            if age < 1.5:
                color = COLOR_NORMAL    # aktif
            elif age < float(config.NODE_TIMEOUT_S):
                color = COLOR_WARNING   # lambat
            else:
                color = COLOR_CRITICAL  # timeout
            self._dot_canvas.itemconfig(oval_id, fill=color)

    # ── Queue Bars ────────────────────────────────────────────────────────────

    def _update_queue_bars(self):
        sizes = self._qm.get_all_sizes()
        for qname, pb in self._queue_bars.items():
            sz = sizes.get(qname, 0)
            pb["value"] = sz
            self._queue_lbl[qname].config(text=str(sz))

    # ── Phase Progress Bar ────────────────────────────────────────────────────

    def _update_phase_bar(self):
        phase_name = self._shared.get("phase", "RESTING")
        try:
            phase = SessionPhase[phase_name]
        except KeyError:
            phase = SessionPhase.RESTING

        duration = PHASE_DURATION_S.get(phase, 30)
        elapsed  = 0.0
        if self._ctrl.phase_start_time:
            elapsed = time.monotonic() - self._ctrl.phase_start_time
        remaining = max(0.0, duration - elapsed)
        progress  = min(1.0, elapsed / max(duration, 0.001))

        # Get canvas width dynamically
        w = self._phase_canvas.winfo_width() or 1240
        fill_w = int(w * progress)
        color  = _PHASE_COLORS.get(phase_name, COLOR_ACCENT)

        self._phase_canvas.coords(self._phase_rect, 0, 0, fill_w, 34)
        self._phase_canvas.itemconfig(self._phase_rect, fill=color)
        self._phase_canvas.coords(self._phase_txt, w // 2, 17)
        self._phase_canvas.itemconfig(
            self._phase_txt,
            text=f"  {phase_name}   ({remaining:.0f}s remaining)  ",
        )

        # Phase badge in header
        self._badge_canvas.itemconfig(self._badge_rect, fill=color)
        self._badge_canvas.itemconfig(self._badge_text, text=phase_name)

    # ── Flash Effect ──────────────────────────────────────────────────────────

    def _flash_critical(self):
        self._flash_tick += 1
        # Toggle every ~2 ticks (400ms × 2 = 800ms cycle)
        if self._flash_tick % 2 == 0:
            self._flash_on = not self._flash_on

        # Flash HR label if CRITICAL
        hr_lbl = self._metric_labels.get("heart_rate_bpm")
        if hr_lbl and hr_lbl.cget("fg") == COLOR_CRITICAL:
            hr_lbl.config(fg=COLOR_CRITICAL if self._flash_on else COLOR_PANEL)

        # Flash SpO2 label if CRITICAL
        spo2_lbl = self._metric_labels.get("spo2_pct")
        if spo2_lbl and spo2_lbl.cget("fg") == COLOR_CRITICAL:
            spo2_lbl.config(fg=COLOR_CRITICAL if self._flash_on else COLOR_PANEL)

    # ── Emergency Banner ──────────────────────────────────────────────────────

    def _update_emergency_banner(self):
        if self._ctrl.emergency_event.is_set():
            if not self._emrg_frame.winfo_ismapped():
                self._emrg_frame.pack(fill=tk.X, padx=8, pady=4)
        else:
            if self._emrg_frame.winfo_ismapped():
                self._emrg_frame.pack_forget()

    # ── Event Log ─────────────────────────────────────────────────────────────

    def _append_event_log(self, text: str, tag: str = "DATA"):
        self._event_log.config(state=tk.NORMAL)
        self._event_log.insert(tk.END, text + "\n", tag)
        self._event_log.see(tk.END)
        # Keep max 200 lines
        lines = int(self._event_log.index(tk.END).split(".")[0])
        if lines > 200:
            self._event_log.delete("1.0", "50.0")
        self._event_log.config(state=tk.DISABLED)

    # =========================================================================
    #  Button Handlers
    # =========================================================================

    def _on_start(self):
        if self._running:
            return
        self._running = True
        self._paused  = False
        self._btn_start.config(state=tk.DISABLED)
        self._btn_pause.config(state=tk.NORMAL)
        self._btn_stop.config(state=tk.NORMAL)

        self._ctrl.start_session()
        self._session_thread = threading.Thread(
            target=self._ctrl.run,
            args=(self._engine,),
            daemon=True,
            name="PULSE-SessionThread",
        )
        self._session_thread.start()
        self._append_event_log(f"[{_ts_short()}] Session started", "PHASE")

    def _on_pause(self):
        if not self._running:
            return
        if not self._paused:
            self._ctrl.pause()
            self._paused = True
            self._btn_pause.config(text="\u25b6 RESUME", bg=COLOR_NORMAL)
            self._append_event_log(f"[{_ts_short()}] Session paused", "PHASE")
        else:
            self._ctrl.resume()
            self._paused = False
            self._btn_pause.config(text="\u23f8 PAUSE", bg=COLOR_ACCENT)
            self._append_event_log(f"[{_ts_short()}] Session resumed", "PHASE")

    def _on_stop(self):
        if not self._running:
            return
        self._ctrl.stop()
        self._running = False
        self._paused  = False
        self._btn_start.config(state=tk.NORMAL)
        self._btn_pause.config(state=tk.DISABLED, text="\u23f8 PAUSE",
                               bg=COLOR_ACCENT)
        self._btn_stop.config(state=tk.DISABLED)
        self._append_event_log(f"[{_ts_short()}] Session stopped", "CRITICAL")

    def _on_benchmark(self):
        if self._bench_thread and self._bench_thread.is_alive():
            messagebox.showinfo("PULSE", "Benchmark sedang berjalan...")
            return

        self._append_event_log(f"[{_ts_short()}] Benchmark started...", "BENCHMARK")

        def _run_bench():
            results = self._bench.run_full_benchmark()
            self._bench_results = results
            # Show result dialog on main thread
            self.root.after(0, lambda: self._show_bench_result(results))

        self._bench_thread = threading.Thread(target=_run_bench, daemon=True,
                                              name="PULSE-BenchThread")
        self._bench_thread.start()

    def _show_bench_result(self, results: dict):
        r  = results
        sp = r["speedup"]
        eff = r["amdahl"]["efficiency_pct"]
        P   = r["amdahl"]["parallel_fraction_P"]
        Sm  = r["amdahl"]["theoretical_max_speedup"]
        msg = (
            f"PULSE BENCHMARK RESULTS\n"
            f"{'─'*38}\n"
            f"Sequential avg : {r['sequential']['avg_total_time_s']:.3f} s\n"
            f"Parallel avg   : {r['parallel']['avg_total_time_s']:.3f} s\n"
            f"Speedup        : {sp:.2f} x\n"
            f"Efficiency     : {eff:.1f} %\n"
            f"Parallel Frac P: {P*100:.1f} % (Amdahl)\n"
            f"Max Speedup    : {Sm:.2f} x (N->inf)\n"
            f"{'─'*38}\n"
            f"Chart: {r.get('chart_path','')}"
        )
        messagebox.showinfo("PULSE Benchmark", msg)
        self._append_event_log(
            f"[{_ts_short()}] Benchmark: speedup={sp:.2f}x eff={eff:.1f}%", "BENCHMARK"
        )

    def _on_save_log(self):
        self._logger.save_summary()
        path = os.path.abspath("logs/summary.csv")
        messagebox.showinfo("PULSE", f"Log saved:\n{path}")
        self._append_event_log(f"[{_ts_short()}] Summary saved -> {path}", "RECOVERY")

    def _on_info(self):
        info = (
            f"{PROJECT_NAME} v{VERSION}\n"
            f"{PROJECT_FULL}\n"
            f"{INSTITUTION}\n\n"
            f"Konsep IFB-206 yang diimplementasikan:\n"
            f"  1. MIMD (Flynn's Taxonomy)\n"
            f"     8 worker processes = Multiple Instructions\n"
            f"     8 sensor nodes     = Multiple Data streams\n\n"
            f"  2. Producer-Consumer Pattern\n"
            f"     SensorNode -> RAW_Q -> FusionEngine -> FUSION_Q -> Dashboard\n\n"
            f"  3. Pipeline Processing\n"
            f"     RAW -> PROCESSED -> FUSION -> LOG\n\n"
            f"  4. Amdahl's Law\n"
            f"     S = 1/((1-P) + P/N), P = parallel fraction\n\n"
            f"  5. Deadlock Detection & Recovery\n"
            f"     NODE_TIMEOUT_S={config.NODE_TIMEOUT_S}s, backoff={config.RECOVERY_BACKOFF_S}s\n\n"
            f"  6. Distributed Message Queue\n"
            f"     multiprocessing.Queue (bukan threading.Queue)\n"
            f"     untuk bypass GIL pada CPU-bound parallel workloads\n\n"
            f"Terinspirasi dari: CuffnCode (STM32 + pressure sensor)"
        )
        messagebox.showinfo(f"Tentang {PROJECT_NAME}", info)

    # =========================================================================
    #  Helpers
    # =========================================================================

    def _mk_section_label(self, parent, text: str):
        fr = tk.Frame(parent, bg="#21262d", height=24)
        fr.pack(fill=tk.X)
        fr.pack_propagate(False)
        tk.Label(fr, text=f"  {text}", bg="#21262d", fg=COLOR_ACCENT,
                 font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, pady=4)

    def _stat_row(self, parent, label: str) -> tk.Label:
        row = tk.Frame(parent, bg=COLOR_PANEL)
        row.pack(fill=tk.X, pady=1)
        tk.Label(row, text=f"{label}:", bg=COLOR_PANEL, fg="#6e7681",
                 font=_FONT_SMALL, width=12, anchor="w").pack(side=tk.LEFT)
        lbl = tk.Label(row, text="—", bg=COLOR_PANEL, fg=COLOR_TEXT,
                       font=("Consolas", 8, "bold"))
        lbl.pack(side=tk.LEFT)
        return lbl


# =============================================================================
#  Helpers
# =============================================================================

def _ts_short() -> str:
    return time.strftime("%H:%M:%S")


# =============================================================================
#  main() — entry point
# =============================================================================

def main():
    from multiprocessing import Manager
    from queue_manager import PulseQueueManager
    from logger import PulseLogger
    from sensor_nodes import create_all_nodes
    from fusion_engine import FusionEngine
    from session_controller import SessionController
    from benchmarker import PulseBenchmarker

    # Setup komponen sistem
    mgr    = Manager()
    shared = mgr.dict()
    lg     = PulseLogger()
    qm     = PulseQueueManager()
    nodes  = create_all_nodes(shared)
    engine = FusionEngine(queue_manager=qm, logger=lg, shared_athlete_state=shared)
    ctrl   = SessionController(
        queue_manager=qm, logger=lg,
        sensor_nodes=nodes, shared_athlete_state=shared,
    )
    bench  = PulseBenchmarker(fusion_engine=engine, logger=lg)

    # Tkinter root
    root = tk.Tk()
    root.configure(bg=COLOR_BG)

    # Graceful shutdown
    def on_close():
        ctrl.stop_event.set()
        engine.shutdown()
        mgr.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    # Launch dashboard
    PulseDashboard(
        root=root,
        queue_manager=qm,
        logger=lg,
        sensor_nodes_dict=nodes,
        session_controller=ctrl,
        fusion_engine=engine,
        benchmarker=bench,
        shared_state=shared,
    )

    root.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
