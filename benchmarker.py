# =============================================================================
#  benchmarker.py — PULSE: Parallel Unified Live Sensor Engine
#  Perbandingan performa Sequential vs Parallel multi-sensor fusion.
#  Mengukur speedup nyata, efisiensi, dan Amdahl's Law parameter.
# =============================================================================

import os
import statistics
import sys
import time

# pyrefly: ignore [missing-import]
import matplotlib
matplotlib.use("Agg")   # non-interactive backend — aman tanpa display
# pyrefly: ignore [missing-import]
import matplotlib.pyplot as plt
# pyrefly: ignore [missing-import]
import matplotlib.gridspec as gridspec
# pyrefly: ignore [missing-import]
import matplotlib.patches as mpatches

import config
from config import (
    SessionPhase, PHASE_DURATION_S, BENCHMARK_TASK_COUNT, BENCHMARK_REPEAT,
    NUM_WORKER_PROCESSES,
    COLOR_BG, COLOR_PANEL, COLOR_TEXT, COLOR_ACCENT,
    COLOR_NORMAL, COLOR_WARNING, COLOR_CRITICAL,
    COLOR_ECG, COLOR_ACCEL, COLOR_SPO2, COLOR_ENV,
)
from sensor_nodes import (
    generate_ecg_sample, generate_accel_sample,
    generate_spo2_sample, generate_env_sample,
    NODE_CONFIG,
)

# Reconfigure stdout ke UTF-8 agar tabel Unicode tampil di terminal Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# =============================================================================
#  Proporsi fase — berdasarkan PHASE_DURATION_S (total = 225 s)
# =============================================================================
_TOTAL_PHASE_DURATION = sum(PHASE_DURATION_S.values())
_PHASE_WEIGHTS = {
    phase: dur / _TOTAL_PHASE_DURATION
    for phase, dur in PHASE_DURATION_S.items()
}


class PulseBenchmarker:
    """
    Benchmark Sequential vs Parallel fusion processing.

    Mengukur:
      - Wall-clock time total dan per-batch
      - Speedup nyata (S = T_seq / T_par)
      - Efisiensi paralel (E = S / N)
      - Amdahl's Law: parallel fraction P dari speedup terukur
      - Theoretical max speedup jika N → ∞ (batas fundamental)
    """

    def __init__(self, fusion_engine, logger) -> None:
        self._engine  = fusion_engine
        self._logger  = logger
        self.results  : dict = {}

    # =========================================================================
    #  generate_test_batch
    # =========================================================================

    def generate_test_batch(self, n: int = BENCHMARK_TASK_COUNT) -> list:
        
        batches = []

        # Tentukan fase tiap batch secara proporsional
        phase_assignment: list[SessionPhase] = []
        for phase, weight in _PHASE_WEIGHTS.items():
            count = max(1, round(n * weight))
            phase_assignment.extend([phase] * count)

        # Trim atau pad ke tepat n
        phase_assignment = phase_assignment[:n]
        while len(phase_assignment) < n:
            phase_assignment.append(SessionPhase.RESTING)

        # Bangun setiap batch
        for i, phase in enumerate(phase_assignment):
            batch = []
            t_offset = i * 0.002   # offset waktu antar batch untuk ECG continuity

            for node_id, cfg in NODE_CONFIG.items():
                node_type = cfg["type"]

                if node_type == "ECG":
                    lead = cfg.get("lead", "Lead-I")
                    ecg_val = generate_ecg_sample(phase, lead, t_offset)
                    sample  = {
                        "node_id":      node_id,
                        "node_type":    "ECG",
                        "chip":         cfg["chip"],
                        "phase":        phase.name,
                        "ecg_mv":       ecg_val,
                        "lead":         lead,
                        "sample_index": i,
                        "timestamp":    f"bench_{i:04d}",
                    }

                elif node_type == "ACCEL":
                    accel   = generate_accel_sample(phase)
                    sample  = {
                        "node_id":    node_id,
                        "node_type":  "ACCEL",
                        "chip":       cfg["chip"],
                        "phase":      phase.name,
                        "sample_index": i,
                        "timestamp":  f"bench_{i:04d}",
                        **accel,
                    }

                elif node_type == "SPO2":
                    spo2   = generate_spo2_sample(phase)
                    sample = {
                        "node_id":    node_id,
                        "node_type":  "SPO2",
                        "chip":       cfg["chip"],
                        "phase":      phase.name,
                        "sample_index": i,
                        "timestamp":  f"bench_{i:04d}",
                        **spo2,
                    }

                elif node_type == "ENV":
                    env    = generate_env_sample(phase)
                    sample = {
                        "node_id":    node_id,
                        "node_type":  "ENV",
                        "chip":       cfg["chip"],
                        "phase":      phase.name,
                        "sample_index": i,
                        "timestamp":  f"bench_{i:04d}",
                        **env,
                    }
                else:
                    sample = {"node_id": node_id, "node_type": node_type,
                              "phase": phase.name, "sample_index": i}

                batch.append((node_id, sample, phase))

            batches.append(batch)

        return batches

    # =========================================================================
    #  run_sequential
    # =========================================================================

    def run_sequential(self, batches: list) -> dict:
        #Jalankan fusion secara SEQUENTIAL untuk semua batch.

        print(f"  [SEQ] Running {BENCHMARK_REPEAT} repeat(s) x {len(batches)} batches...")
        all_total_times  = []
        per_batch_record = []   # dari repeat pertama

        for rep in range(BENCHMARK_REPEAT):
            t_run_start     = time.perf_counter()
            rep_batch_times = []

            for batch in batches:
                t0     = time.perf_counter()
                self._engine.process_sequential(batch)
                t1     = time.perf_counter()
                rep_batch_times.append((t1 - t0) * 1000.0)

            run_total = time.perf_counter() - t_run_start
            all_total_times.append(run_total)

            if rep == 0:
                per_batch_record = rep_batch_times

            print(f"    repeat {rep + 1}/{BENCHMARK_REPEAT}: {run_total:.3f}s")

        avg_total = statistics.mean(all_total_times)
        std_dev   = statistics.stdev(all_total_times) if len(all_total_times) > 1 else 0.0
        avg_per   = avg_total / len(batches) * 1000.0   # ms

        return {
            "avg_total_time_s":  round(avg_total, 4),
            "avg_per_batch_ms":  round(avg_per, 4),
            "std_dev_s":         round(std_dev, 6),
            "all_times_s":       [round(t, 4) for t in all_total_times],
            "per_batch_times_ms": [round(t, 3) for t in per_batch_record],
        }

    # =========================================================================
    #  run_parallel
    # =========================================================================

    def run_parallel(self, batches: list) -> dict:

       # Jalankan fusion secara PARALLEL untuk semua batch.

       
        print(f"  [PAR] Running {BENCHMARK_REPEAT} repeat(s) x {len(batches)} batches...")
        all_total_times  = []
        per_batch_record = []

        for rep in range(BENCHMARK_REPEAT):
            t_run_start     = time.perf_counter()
            rep_batch_times = []

            for batch in batches:
                t0     = time.perf_counter()
                self._engine.process_parallel(batch)
                t1     = time.perf_counter()
                rep_batch_times.append((t1 - t0) * 1000.0)

            run_total = time.perf_counter() - t_run_start
            all_total_times.append(run_total)

            if rep == 0:
                per_batch_record = rep_batch_times

            print(f"    repeat {rep + 1}/{BENCHMARK_REPEAT}: {run_total:.3f}s")

        avg_total = statistics.mean(all_total_times)
        std_dev   = statistics.stdev(all_total_times) if len(all_total_times) > 1 else 0.0
        avg_per   = avg_total / len(batches) * 1000.0

        return {
            "avg_total_time_s":  round(avg_total, 4),
            "avg_per_batch_ms":  round(avg_per, 4),
            "std_dev_s":         round(std_dev, 6),
            "all_times_s":       [round(t, 4) for t in all_total_times],
            "per_batch_times_ms": [round(t, 3) for t in per_batch_record],
        }

    # =========================================================================
    #  calculate_amdahl
    # =========================================================================

    def calculate_amdahl(self, speedup: float, n_processors: int) -> dict:
        """

        Amdahl's Law:
            S = 1 / ((1-P) + P/N)

        Menyelesaikan untuk P (parallel fraction):
            P = (S - 1) / (S * (1 - 1/N))

        Theoretical maximum speedup (N → ∞):
            S_max = 1 / (1 - P)

        Efisiensi paralel:
            E = S / N

      """
        if n_processors <= 1:
            return {
                "parallel_fraction_P":     0.0,
                "theoretical_max_speedup": 1.0,
                "efficiency_pct":          100.0,
                "speedup_measured":        speedup,
                "n_processors":            n_processors,
            }

        denom = speedup * (1.0 - 1.0 / n_processors)
        if abs(denom) < 1e-9:
            parallel_fraction = 0.0
        else:
            parallel_fraction = (speedup - 1.0) / denom

        # Clamp P ke [0, 1] — floating point bisa sedikit melampaui batas
        parallel_fraction = max(0.0, min(1.0, parallel_fraction))

        serial_fraction   = 1.0 - parallel_fraction
        if serial_fraction < 1e-9:
            theoretical_max = float("inf")
        else:
            theoretical_max = 1.0 / serial_fraction

        efficiency = (speedup / n_processors) * 100.0

        return {
            "parallel_fraction_P":     round(parallel_fraction, 4),
            "theoretical_max_speedup": round(theoretical_max, 3),
            "efficiency_pct":          round(efficiency, 2),
            "speedup_measured":        round(speedup, 4),
            "n_processors":            n_processors,
        }

    # =========================================================================
    #  plot_results
    # =========================================================================

    def plot_results(self, metrics: dict) -> str:
      
        seq  = metrics["sequential"]
        par  = metrics["parallel"]
        amdahl = metrics["amdahl"]
        speedup = metrics["speedup"]

        # --- Setup figure dark theme ---
        fig = plt.figure(figsize=(14, 9), facecolor=COLOR_BG)
        fig.suptitle(
            "PULSE Benchmark — Sequential vs Parallel Fusion",
            color=COLOR_TEXT, fontsize=15, fontweight="bold", y=0.98,
        )
        gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.45, wspace=0.35)

        ax_bar  = fig.add_subplot(gs[0, 0])
        ax_box  = fig.add_subplot(gs[0, 1])
        ax_line = fig.add_subplot(gs[1, 0])
        ax_txt  = fig.add_subplot(gs[1, 1])

        axes = [ax_bar, ax_box, ax_line, ax_txt]
        for ax in axes:
            ax.set_facecolor(COLOR_PANEL)
            for spine in ax.spines.values():
                spine.set_edgecolor("#30363d")
            ax.tick_params(colors=COLOR_TEXT, labelsize=9)
            ax.xaxis.label.set_color(COLOR_TEXT)
            ax.yaxis.label.set_color(COLOR_TEXT)
            ax.title.set_color(COLOR_TEXT)

        # -----------------------------------------------------------------
        #  [0,0] BAR CHART — avg total time
        # -----------------------------------------------------------------
        labels   = ["Sequential", "Parallel"]
        values   = [seq["avg_total_time_s"], par["avg_total_time_s"]]
        colors_b = [COLOR_WARNING, COLOR_NORMAL]
        errs     = [seq["std_dev_s"], par["std_dev_s"]]

        bars = ax_bar.bar(
            labels, values, color=colors_b,
            width=0.45, zorder=3,
            yerr=errs, capsize=6,
            error_kw={"ecolor": COLOR_TEXT, "linewidth": 1.4},
        )
        ax_bar.set_title("Avg Total Time (lower = better)", fontsize=10)
        ax_bar.set_ylabel("Time (seconds)", fontsize=9)
        ax_bar.yaxis.grid(True, color="#30363d", linewidth=0.6, zorder=0)
        ax_bar.set_axisbelow(True)

        for bar, val, err in zip(bars, values, errs):
            ax_bar.text(
                bar.get_x() + bar.get_width() / 2,
                val + err + 0.002,
                f"{val:.3f}s",
                ha="center", va="bottom",
                color=COLOR_TEXT, fontsize=9, fontweight="bold",
            )

        # Speedup annotation
        ax_bar.annotate(
            f"Speedup: {speedup:.2f}x",
            xy=(0.5, 0.95), xycoords="axes fraction",
            ha="center", va="top",
            color=COLOR_ACCENT, fontsize=10, fontweight="bold",
        )

        # -----------------------------------------------------------------
        #  [0,1] BOX PLOT — distribusi waktu semua repeat
        # -----------------------------------------------------------------
        bp_data  = [seq["all_times_s"], par["all_times_s"]]
        bp_colors = [COLOR_WARNING, COLOR_NORMAL]

        bp = ax_box.boxplot(
            bp_data, patch_artist=True,
            medianprops={"color": COLOR_TEXT, "linewidth": 2},
            whiskerprops={"color": COLOR_TEXT},
            capprops={"color": COLOR_TEXT},
            flierprops={"markerfacecolor": COLOR_CRITICAL, "markersize": 5},
        )
        for patch, col in zip(bp["boxes"], bp_colors):
            patch.set_facecolor(col)
            patch.set_alpha(0.7)

        ax_box.set_xticklabels(["Sequential", "Parallel"])
        ax_box.set_title(f"Time Distribution ({BENCHMARK_REPEAT} repeats)", fontsize=10)
        ax_box.set_ylabel("Time (seconds)", fontsize=9)
        ax_box.yaxis.grid(True, color="#30363d", linewidth=0.6, zorder=0)
        ax_box.set_axisbelow(True)

        # -----------------------------------------------------------------
        #  [1,0] LINE CHART — per-batch time (batch-by-batch comparison)
        # -----------------------------------------------------------------
        n_batches = min(
            len(seq["per_batch_times_ms"]),
            len(par["per_batch_times_ms"]),
        )
        x_idx = list(range(1, n_batches + 1))

        ax_line.plot(
            x_idx, seq["per_batch_times_ms"][:n_batches],
            color=COLOR_WARNING, linewidth=1.4, label="Sequential",
            alpha=0.85,
        )
        ax_line.plot(
            x_idx, par["per_batch_times_ms"][:n_batches],
            color=COLOR_NORMAL, linewidth=1.4, label="Parallel",
            alpha=0.85,
        )

        ax_line.set_title("Per-Batch Processing Time", fontsize=10)
        ax_line.set_xlabel("Batch Index", fontsize=9)
        ax_line.set_ylabel("Time (ms)", fontsize=9)
        ax_line.yaxis.grid(True, color="#30363d", linewidth=0.6, zorder=0)
        ax_line.set_axisbelow(True)
        ax_line.legend(
            facecolor=COLOR_PANEL, edgecolor="#30363d",
            labelcolor=COLOR_TEXT, fontsize=8,
        )

        # -----------------------------------------------------------------
        #  [1,1] TEXT SUMMARY — Amdahl's Law breakdown
        # -----------------------------------------------------------------
        ax_txt.axis("off")

        P      = amdahl["parallel_fraction_P"]
        S_max  = amdahl["theoretical_max_speedup"]
        eff    = amdahl["efficiency_pct"]
        n_proc = amdahl["n_processors"]

        summary_lines = [
            ("BENCHMARK SUMMARY", None, COLOR_ACCENT, 11, "bold"),
            ("", None, COLOR_TEXT, 9, "normal"),
            ("Speedup (measured)",     f"{speedup:.3f} x",        COLOR_NORMAL,  9, "normal"),
            ("Efficiency",             f"{eff:.1f} %",             COLOR_NORMAL,  9, "normal"),
            ("",                       "",                         COLOR_TEXT,    9, "normal"),
            ("Amdahl's Law",           "",                         COLOR_ACCENT,  9, "bold"),
            ("Parallel Fraction P",    f"{P * 100:.1f} %",        COLOR_TEXT,    9, "normal"),
            ("Serial Fraction (1-P)",  f"{(1-P)*100:.1f} %",      COLOR_TEXT,    9, "normal"),
            ("Theoretical Max Speed",  f"{S_max:.2f} x  (N→inf)", COLOR_WARNING, 9, "normal"),
            ("",                       "",                         COLOR_TEXT,    9, "normal"),
            ("Workers used",           f"{n_proc}",                COLOR_TEXT,    9, "normal"),
            ("Batches",                f"{BENCHMARK_TASK_COUNT}",  COLOR_TEXT,    9, "normal"),
            ("Repeats",                f"{BENCHMARK_REPEAT}",      COLOR_TEXT,    9, "normal"),
            ("Seq avg",                f"{seq['avg_total_time_s']:.3f} s",  COLOR_WARNING, 9, "normal"),
            ("Par avg",                f"{par['avg_total_time_s']:.3f} s",  COLOR_NORMAL,  9, "normal"),
        ]

        y_pos = 0.97
        line_h = 0.065
        for label, value, color, size, weight in summary_lines:
            if label and value:
                ax_txt.text(0.02, y_pos, label + ":", color=COLOR_TEXT,
                            fontsize=size, fontweight=weight,
                            transform=ax_txt.transAxes, va="top")
                ax_txt.text(0.62, y_pos, value, color=color,
                            fontsize=size, fontweight="bold",
                            transform=ax_txt.transAxes, va="top")
            elif label:
                ax_txt.text(0.02, y_pos, label, color=color,
                            fontsize=size, fontweight=weight,
                            transform=ax_txt.transAxes, va="top")
            y_pos -= line_h

        # Divider line di bawah judul
        ax_txt.axhline(y=0.89, xmin=0.0, xmax=1.0, color="#30363d", linewidth=1)

        # -----------------------------------------------------------------
        #  Simpan figure
        # -----------------------------------------------------------------
        os.makedirs("logs", exist_ok=True)
        out_path = os.path.join("logs", "benchmark_chart.png")
        fig.savefig(out_path, dpi=130, bbox_inches="tight",
                    facecolor=COLOR_BG, edgecolor="none")
        plt.close(fig)

        return os.path.abspath(out_path)

    # =========================================================================
    #  run_full_benchmark
    # =========================================================================

    def run_full_benchmark(self) -> dict:
       
        phase_ref = SessionPhase.SPRINT   # fase referensi untuk logging

        print("\n" + "=" * 55)
        print(f"  PULSE BENCHMARKER  |  {BENCHMARK_TASK_COUNT} batches x {BENCHMARK_REPEAT} repeats")
        print("=" * 55)

        # --- 1. Generate test data ---
        print(f"\n[1/4] Generating {BENCHMARK_TASK_COUNT} synthetic batches...")
        batches = self.generate_test_batch(n=BENCHMARK_TASK_COUNT)
        print(f"      {len(batches)} batches ready ({len(batches[0])} nodes each)")

        # --- 2. Sequential run ---
        print(f"\n[2/4] Sequential mode...")
        seq_result = self.run_sequential(batches)
        print(f"      avg={seq_result['avg_total_time_s']:.4f}s  "
              f"std={seq_result['std_dev_s']:.4f}s")

        # --- 3. Parallel run ---
        print(f"\n[3/4] Parallel mode ({NUM_WORKER_PROCESSES} workers)...")
        par_result = self.run_parallel(batches)
        print(f"      avg={par_result['avg_total_time_s']:.4f}s  "
              f"std={par_result['std_dev_s']:.4f}s")

        # --- 4. Amdahl analysis ---
        seq_t   = seq_result["avg_total_time_s"]
        par_t   = par_result["avg_total_time_s"]
        speedup = seq_t / max(par_t, 1e-9)

        amdahl  = self.calculate_amdahl(speedup, NUM_WORKER_PROCESSES)
        P       = amdahl["parallel_fraction_P"]
        S_max   = amdahl["theoretical_max_speedup"]
        eff     = amdahl["efficiency_pct"]

        metrics = {
            "sequential":  seq_result,
            "parallel":    par_result,
            "amdahl":      amdahl,
            "speedup":     round(speedup, 4),
            "batch_count": len(batches),
            "chart_path":  "",
        }
        self.results = metrics

        # --- 5. Plot ---
        print(f"\n[4/4] Generating benchmark chart...")
        chart_path = self.plot_results(metrics)
        metrics["chart_path"] = chart_path
        print(f"      Saved: {chart_path}")

        # --- 6. ASCII Table ---
        self._print_table(seq_t, par_t, speedup, eff, P, S_max)

        # --- 7. Log ke logger (BENCHMARK events) ---
        self._log_results(metrics, phase_ref)

        return metrics

    # =========================================================================
    #  Private helpers
    # =========================================================================

    def _print_table(
        self,
        seq_t: float, par_t: float, speedup: float,
        eff: float, P: float, S_max: float,
    ) -> None:
        """Cetak tabel ringkasan benchmark ke terminal dengan Unicode box chars."""
        W = 24   # lebar kolom

        def row(label: str, value: str) -> str:
            return f"\u2551 {label:<{W}} \u2566 {value:<{W}} \u2551"

        lines = [
            "\n",
            f"\u2554{'=' * (W + 2)}\u2566{'=' * (W + 2)}\u2557",
            f"\u2551{'  PULSE BENCHMARK RESULTS':^{(W+2)*2+1}}\u2551",
            f"\u2560{'=' * (W + 2)}\u2566{'=' * (W + 2)}\u2563",
            row("Sequential (avg)",      f"{seq_t:.4f} s"),
            row("Parallel (avg)",        f"{par_t:.4f} s"),
            f"\u2560{'=' * (W + 2)}\u2569{'=' * (W + 2)}\u2563",
            row("Speedup",              f"{speedup:.3f} x"),
            row("Efficiency",           f"{eff:.1f} %"),
            row("Parallel Fraction P",  f"{P * 100:.1f} %  (Amdahl)"),
            row("Theoretical Max",      f"{S_max:.2f} x  (N->inf)"),
            f"\u255a{'=' * (W + 2)}\u2569{'=' * (W + 2)}\u255d",
        ]

        for line in lines:
            try:
                print(line)
            except UnicodeEncodeError:
                # Fallback ASCII untuk konsol yang tidak support Unicode
                print(line.encode("ascii", errors="replace").decode("ascii"))

    def _log_results(self, metrics: dict, phase: SessionPhase) -> None:
        """Log metrik benchmark ke PulseLogger sebagai event BENCHMARK."""
        if self._logger is None:
            return

        items = [
            ("seq_avg_time_s",       metrics["sequential"]["avg_total_time_s"]),
            ("seq_std_dev_s",        metrics["sequential"]["std_dev_s"]),
            ("par_avg_time_s",       metrics["parallel"]["avg_total_time_s"]),
            ("par_std_dev_s",        metrics["parallel"]["std_dev_s"]),
            ("speedup",              metrics["speedup"]),
            ("efficiency_pct",       metrics["amdahl"]["efficiency_pct"]),
            ("parallel_fraction_P",  metrics["amdahl"]["parallel_fraction_P"]),
            ("theoretical_max_speedup", metrics["amdahl"]["theoretical_max_speedup"]),
            ("batch_count",          float(metrics["batch_count"])),
        ]
        for metric, value in items:
            self._logger.log_event(
                phase, "BENCHMARKER", "BENCHMARK",
                metric, float(value),
            )

    def __repr__(self) -> str:
        if self.results:
            return (
                f"PulseBenchmarker("
                f"speedup={self.results['speedup']:.3f}x, "
                f"batches={self.results['batch_count']}, "
                f"repeats={BENCHMARK_REPEAT})"
            )
        return "PulseBenchmarker(not yet run)"
