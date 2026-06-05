# =============================================================================
#  logger.py — PULSE: Parallel Unified Live Sensor Engine
#  Thread-safe logging: CSV persistence, ANSI terminal output, analytics.
# =============================================================================

import csv
import logging
import os
import threading
import time
from datetime import datetime

import config

# =============================================================================
#  ANSI Color Codes
# =============================================================================

_ANSI = {
    "RESET":     "\033[0m",
    "DATA":      "",                    # default terminal color
    "WARNING":   "\033[93m",            # kuning
    "CRITICAL":  "\033[91m",            # merah
    "DEADLOCK":  "\033[91m\033[1m",     # merah bold
    "RECOVERY":  "\033[92m",            # hijau
    "BENCHMARK": "\033[96m",            # cyan
    "PHASE":     "\033[95m\033[1m",     # magenta bold — untuk transisi fase
    "DIM":       "\033[2m",             # redup — untuk timestamp
}

# Kolom CSV session log
_CSV_COLUMNS = [
    "timestamp", "phase", "node_id", "event_type",
    "metric", "value", "latency_ms", "pid",
]

# Kolom CSV summary
_SUMMARY_COLUMNS = [
    "metric", "value"
]


class PulseLogger:
    """
    Logger terpusat untuk sistem PULSE.

    Bertanggung jawab atas:
    - Penulisan event ke logs/session_log.csv (thread-safe)
    - Penulisan ke logs/pulse.log via Python logging
    - Output terminal berwarna ANSI per event_type
    - Analitik sesi (get_analytics)
    - Ringkasan sesi (save_summary)
    """

    def __init__(self) -> None:
        # --- Buat folder logs/ jika belum ada ---
        os.makedirs("logs", exist_ok=True)

        # --- Simpan waktu mulai sesi ---
        self.session_start_time: float = time.time()
        self._pid: int = os.getpid()

        # --- Lock tunggal untuk semua write operation ---
        self._lock = threading.Lock()

        # --- Inisialisasi CSV session log ---
        self._csv_path = os.path.join("logs", "session_log.csv")
        with open(self._csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
            writer.writeheader()

        # --- Inisialisasi Python logging ke pulse.log ---
        self._log_path = os.path.join("logs", "pulse.log")
        logging.basicConfig(
            filename=self._log_path,
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            encoding="utf-8",
        )
        self._logger = logging.getLogger("PULSE")

        # --- Cache in-memory untuk analytics (list of row dicts) ---
        self._events: list[dict] = []

        self._logger.info(
            f"[INIT] PulseLogger started | pid={self._pid} | "
            f"session_start={datetime.fromtimestamp(self.session_start_time).isoformat()}"
        )
        print(
            f"{_ANSI['RECOVERY']}[PULSE] Logger initialized "
            f"-> {self._csv_path} | {self._log_path}{_ANSI['RESET']}"
        )

    # -------------------------------------------------------------------------
    #  log_event
    # -------------------------------------------------------------------------

    def log_event(
        self,
        phase: "config.SessionPhase",
        node_id: str,
        event_type: str,
        metric: str,
        value: float,
        latency_ms: float = 0,
    ) -> None:
        
        ts = datetime.now().isoformat(timespec="milliseconds")
        phase_name = phase.name if isinstance(phase, config.SessionPhase) else str(phase)

        row = {
            "timestamp":  ts,
            "phase":      phase_name,
            "node_id":    node_id,
            "event_type": event_type,
            "metric":     metric,
            "value":      value,
            "latency_ms": latency_ms,
            "pid":        self._pid,
        }

        with self._lock:
            # Tulis ke CSV
            with open(self._csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
                writer.writerow(row)

            # Simpan ke cache in-memory
            self._events.append(row)

            # Tulis ke Python log file
            log_msg = (
                f"[{event_type}] phase={phase_name} node={node_id} "
                f"metric={metric} value={value} latency={latency_ms}ms"
            )
            if event_type in ("CRITICAL", "DEADLOCK"):
                self._logger.error(log_msg)
            elif event_type == "WARNING":
                self._logger.warning(log_msg)
            else:
                self._logger.info(log_msg)

            # Print terminal berwarna
            color  = _ANSI.get(event_type, "")
            reset  = _ANSI["RESET"] if color else ""
            dim    = _ANSI["DIM"]
            prefix = f"{dim}[{ts}]{_ANSI['RESET']} "
            print(
                f"{prefix}{color}[{event_type:<9}] "
                f"{phase_name:<8} | {node_id:<11} | "
                f"{metric:<22} = {value:>10.4f}  "
                f"(latency: {latency_ms:.1f}ms){reset}"
            )

    # -------------------------------------------------------------------------
    #  log_phase_transition
    # -------------------------------------------------------------------------

    def log_phase_transition(
        self,
        old_phase: "config.SessionPhase",
        new_phase: "config.SessionPhase",
    ) -> None:
        """
        Log pergantian fase dengan separator visual di terminal.
        Juga mencatat sebagai event ke CSV dan pulse.log.
        """
        old_name = old_phase.name if isinstance(old_phase, config.SessionPhase) else str(old_phase)
        new_name = new_phase.name if isinstance(new_phase, config.SessionPhase) else str(new_phase)
        separator = "=" * 38

        color = _ANSI["PHASE"]
        reset = _ANSI["RESET"]

        with self._lock:
            # Terminal output
            print(f"\n{color}{separator}")
            print(f"  PHASE TRANSITION: {old_name} -> {new_name}")
            print(f"{separator}{reset}\n")

            # Log ke file
            msg = f"[PHASE TRANSITION] {old_name} -> {new_name}"
            self._logger.info(msg)

            # Catat ke CSV sebagai event khusus
            ts = datetime.now().isoformat(timespec="milliseconds")
            row = {
                "timestamp":  ts,
                "phase":      new_name,
                "node_id":    "SYSTEM",
                "event_type": "PHASE_TRANSITION",
                "metric":     "phase",
                "value":      0,
                "latency_ms": 0,
                "pid":        self._pid,
            }
            with open(self._csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
                writer.writerow(row)
            self._events.append(row)

    # -------------------------------------------------------------------------
    #  get_analytics
    # -------------------------------------------------------------------------

    def get_analytics(self) -> dict:
        
        with self._lock:
            events = list(self._events)   # snapshot agar lock segera dilepas

        total = len(events)

        events_by_type: dict[str, int] = {}
        events_by_node: dict[str, int] = {}
        latencies: list[float] = []
        data_count = 0
        anomaly_count = 0

        for ev in events:
            etype = ev["event_type"]
            node  = ev["node_id"]

            events_by_type[etype] = events_by_type.get(etype, 0) + 1
            events_by_node[node]  = events_by_node.get(node, 0) + 1

            try:
                lat = float(ev["latency_ms"])
                latencies.append(lat)
            except (ValueError, TypeError):
                pass

            if etype == "DATA":
                data_count += 1
            if etype in ("WARNING", "CRITICAL"):
                anomaly_count += 1

        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        max_latency = max(latencies) if latencies else 0.0
        session_duration = time.time() - self.session_start_time
        data_quality = (data_count / total * 100) if total > 0 else 0.0

        return {
            "total_events":       total,
            "events_by_type":     events_by_type,
            "events_by_node":     events_by_node,
            "avg_latency_ms":     round(avg_latency, 3),
            "max_latency_ms":     round(max_latency, 3),
            "anomaly_count":      anomaly_count,
            "session_duration_s": round(session_duration, 2),
            "data_quality_pct":   round(data_quality, 2),
        }

    # -------------------------------------------------------------------------
    #  save_summary
    # -------------------------------------------------------------------------

    def save_summary(self) -> None:
        """
        Simpan ringkasan sesi ke logs/summary.csv dan cetak tabel ASCII
        ke terminal.
        """
        analytics = self.get_analytics()
        summary_path = os.path.join("logs", "summary.csv")

        # --- Bangun baris ringkasan ---
        rows: list[tuple[str, str]] = [
            ("session_duration_s",  str(analytics["session_duration_s"])),
            ("total_events",        str(analytics["total_events"])),
            ("anomaly_count",       str(analytics["anomaly_count"])),
            ("avg_latency_ms",      str(analytics["avg_latency_ms"])),
            ("max_latency_ms",      str(analytics["max_latency_ms"])),
            ("data_quality_pct",    f"{analytics['data_quality_pct']}%"),
        ]

        # events_by_type → baris terpisah
        for etype, count in sorted(analytics["events_by_type"].items()):
            rows.append((f"events.{etype}", str(count)))

        # events_by_node → baris terpisah
        for node, count in sorted(analytics["events_by_node"].items()):
            rows.append((f"node.{node}", str(count)))

        # --- Tulis CSV ---
        with self._lock:
            with open(summary_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["metric", "value"])
                writer.writerows(rows)

        self._logger.info(f"[SUMMARY] saved → {summary_path}")

        # --- Tabel ASCII terminal ---
        col_w = 34
        val_w = 20
        border_top    = f"+{'-' * col_w}+{'-' * val_w}+"
        border_mid    = f"+{'-' * col_w}+{'-' * val_w}+"
        border_bot    = f"+{'-' * col_w}+{'-' * val_w}+"
        row_fmt       = "| {:<{cw}} | {:>{vw}} |"

        cyan  = _ANSI["BENCHMARK"]
        reset = _ANSI["RESET"]

        print(f"\n{cyan}{border_top}")
        print(f"| {'PULSE SESSION SUMMARY':^{col_w + val_w - 1}} |")
        print(border_mid)
        print(row_fmt.format("Metric", "Value", cw=col_w - 1, vw=val_w - 1))
        print(border_mid)
        for metric, value in rows:
            print(row_fmt.format(metric, value, cw=col_w - 1, vw=val_w - 1))
        print(f"{border_bot}{reset}\n")
        print(f"{_ANSI['RECOVERY']}[PULSE] Summary saved -> {summary_path}{reset}")
