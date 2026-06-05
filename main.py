# =============================================================================
#  main.py — PULSE: Parallel Unified Live Sensor Engine
#  Entry point utama sistem monitoring atlet wearable.
#  IFB-206 Parallel & Distributed Systems — ITENAS Bandung
# =============================================================================

import multiprocessing
import sys
import time

# Set matplotlib backend SEBELUM import modul lain yang import matplotlib,
# agar benchmarker (Agg) dan dashboard (TkAgg) tidak konflik.
# TkAgg mendukung save-to-file sekaligus tampilan GUI.
# pyrefly: ignore [missing-import]
import matplotlib
matplotlib.use("TkAgg")

# =============================================================================
#  _status_line — helper cetak status berjajar
# =============================================================================

def _ok(step: int, total: int, desc: str, detail: str = "") -> None:
    ANSI_GREEN  = "\033[92m"
    ANSI_RESET  = "\033[0m"
    label = f"[{step}/{total}] {desc}"
    dots  = "." * max(1, 54 - len(label))
    suffix = f" {detail}" if detail else ""
    try:
        print(f"  {label} {dots} {ANSI_GREEN}OK{ANSI_RESET}{suffix}")
    except UnicodeEncodeError:
        print(f"  {label} {dots} OK{suffix}")


def _header() -> None:
    """Cetak ASCII art header ke terminal."""
    lines = [
        "\u2554" + "\u2550" * 62 + "\u2557",
        "\u2551   P U L S E  \u2014  Parallel Unified Live Sensor Engine         \u2551",
        "\u2551   Wearable Athlete Monitoring System                         \u2551",
        "\u2551   IFB-206 Parallel & Distributed Systems \u2014 ITENAS Bandung    \u2551",
        "\u255a" + "\u2550" * 62 + "\u255d",
    ]
    try:
        print()
        for line in lines:
            print(line)
        print()
    except UnicodeEncodeError:
        print()
        print("=" * 64)
        print("  PULSE -- Parallel Unified Live Sensor Engine")
        print("  Wearable Athlete Monitoring System")
        print("  IFB-206 Parallel & Distributed Systems -- ITENAS Bandung")
        print("=" * 64)
        print()


def _build_initial_shared_state(shared, node_ids: list) -> None:
    """Isi shared_athlete_state dengan nilai awal (baseline RESTING)."""
    initial = {
        "heart_rate_bpm":    65.0,
        "spo2_pct":          99.0,
        "steps_total":       0,
        "cadence_spm":       0.0,
        "skin_temp_c":       36.5,
        "gsr_kohm":          60.0,
        "resp_rate_bpm":     14.0,
        "stress_index":      0.0,
        "exertion_level":    "LOW",
        "ecg_quality":       100.0,
        "current_phase":     "RESTING",
        "phase":             "RESTING",
        "anomalies":         [],
        "node_status":       {nid: "IDLE" for nid in node_ids},
        "total_samples":     0,
        "session_elapsed_s": 0.0,
        "benchmark_done":    False,
        "benchmark_results": {},
    }
    for k, v in initial.items():
        shared[k] = v


def _run_preflight(bench, n_tasks: int = 20) -> None:
    """
    Jalankan pre-flight benchmark ringan sebelum dashboard diluncurkan.
    Memberikan gambaran awal performa sistem pada hardware target.
    """
    ANSI_CYAN   = "\033[96m"
    ANSI_YELLOW = "\033[93m"
    ANSI_GREEN  = "\033[92m"
    ANSI_RESET  = "\033[0m"

    print(f"\n{ANSI_CYAN}Running pre-flight benchmark ({n_tasks} tasks)...{ANSI_RESET}")

    batches  = bench.generate_test_batch(n=n_tasks)
    amdahl   = None

    # Sequential (1 repeat, cukup untuk estimasi)
    t0 = time.perf_counter()
    for batch in batches:
        bench._engine.process_sequential(batch)
    seq_t = time.perf_counter() - t0

    # Parallel (1 repeat)
    t0 = time.perf_counter()
    for batch in batches:
        bench._engine.process_parallel(batch)
    par_t = time.perf_counter() - t0

    speedup  = seq_t / max(par_t, 1e-9)
    from config import NUM_WORKER_PROCESSES
    amdahl   = bench.calculate_amdahl(speedup, NUM_WORKER_PROCESSES)
    eff      = amdahl["efficiency_pct"]

    print(f"  Sequential  : {seq_t:.3f} s  ({n_tasks} batches)")
    print(f"  Parallel    : {par_t:.3f} s  ({n_tasks} batches)")
    print(f"  Speedup     : {ANSI_GREEN}{speedup:.2f}x{ANSI_RESET}")
    print(f"  Efficiency  : {ANSI_YELLOW}{eff:.1f}%{ANSI_RESET}  "
          f"(Amdahl P={amdahl['parallel_fraction_P']*100:.1f}%)")
    print(f"  Theo. Max   : {amdahl['theoretical_max_speedup']:.2f}x  (N -> inf)")


# =============================================================================
#  main
# =============================================================================

def main() -> None:
    import tkinter as tk

    from multiprocessing import Manager

    from logger import PulseLogger
    from queue_manager import PulseQueueManager
    from sensor_nodes import create_all_nodes
    from fusion_engine import FusionEngine
    from session_controller import SessionController
    from benchmarker import PulseBenchmarker
    from dashboard import PulseDashboard
    from config import NODE_CONFIG

    # Reconfigure stdout untuk UTF-8 (Windows PowerShell cp1252 workaround)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    _header()
    print("  Initializing PULSE subsystems...\n")

    TOTAL = 7

    # [1/7] Logger
    lg = PulseLogger()
    _ok(1, TOTAL, "Logger initialized", "-> logs/pulse.log")

    # [2/7] Shared state
    mgr    = Manager()
    shared = mgr.dict()
    node_ids = list(NODE_CONFIG.keys())
    _build_initial_shared_state(shared, node_ids)
    _ok(2, TOTAL, "Shared athlete state created", f"({len(shared)} keys)")

    # [3/7] Sensor nodes
    nodes = create_all_nodes(shared)
    _ok(3, TOTAL, "Sensor nodes ready", f"({len(nodes)} nodes)")

    # [4/7] Queue manager
    qm = PulseQueueManager()
    _ok(4, TOTAL, "Queue manager ready", "(4 queues)")

    # [5/7] Fusion engine
    engine = FusionEngine(queue_manager=qm, logger=lg, shared_athlete_state=shared)
    _ok(5, TOTAL, "Fusion engine ready", f"({engine._pool._processes} workers)")

    # [6/7] Session controller
    ctrl = SessionController(
        queue_manager=qm,
        logger=lg,
        sensor_nodes=nodes,
        shared_athlete_state=shared,
    )
    _ok(6, TOTAL, "Session controller ready")

    # [7/7] Benchmarker
    bench = PulseBenchmarker(fusion_engine=engine, logger=lg)
    _ok(7, TOTAL, "Benchmarker ready")

    # Pre-flight benchmark
    _run_preflight(bench, n_tasks=20)

    print("\nLaunching PULSE Dashboard...")
    print("  Close the window to end the session.\n")

    # Tkinter root
    root = tk.Tk()
    root.configure(bg="#0d1117")

    def on_close():
        ctrl.stop_event.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    # Launch dashboard — blocking via mainloop()
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

    # ── Cleanup ────────────────────────────────────────────────────────────────
    _cleanup(engine, lg, mgr)


def _cleanup(engine, lg, mgr) -> None:
    """Graceful shutdown setelah window ditutup."""
    ANSI_GREEN = "\033[92m"
    ANSI_RESET = "\033[0m"

    print()
    print("  Shutting down PULSE subsystems...")

    try:
        engine.shutdown()
        print(f"    FusionEngine pool       ... {ANSI_GREEN}closed{ANSI_RESET}")
    except Exception:
        pass

    try:
        lg.save_summary()
        print(f"    Session summary saved   ... {ANSI_GREEN}logs/summary.csv{ANSI_RESET}")
    except Exception:
        pass

    try:
        mgr.shutdown()
        print(f"    Manager process         ... {ANSI_GREEN}stopped{ANSI_RESET}")
    except Exception:
        pass

    print()
    print("  [PULSE] Session ended. Data saved to logs/")
    print()


# =============================================================================
#  Entry point
# =============================================================================

if __name__ == "__main__":
    multiprocessing.freeze_support()   # Windows .exe compatibility
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  [PULSE] KeyboardInterrupt received — shutting down gracefully.")
        print("  [PULSE] Session data (if any) saved to logs/")
        sys.exit(0)
