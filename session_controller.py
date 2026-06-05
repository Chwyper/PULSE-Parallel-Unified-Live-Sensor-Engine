# =============================================================================
#  session_controller.py — PULSE: Parallel Unified Live Sensor Engine
#  Producer utama: mengatur sesi lari, transisi fase, dan distribusi data.
# =============================================================================

import threading
import time
from datetime import datetime

import config
from config import (
    SessionPhase, PHASE_DURATION_S, PHASE_LATENCY_FACTOR,
    BASE_LATENCY_MS, ANOMALY_THRESHOLDS,
)
from sensor_nodes import PacketLossError


class SessionController:
    
    # Urutan fase yang tetap selama sesi
    PHASE_SEQUENCE = [
        SessionPhase.RESTING,
        SessionPhase.WARMUP,
        SessionPhase.SPRINT,
        SessionPhase.COOLDOWN,
        SessionPhase.RECOVERY,
    ]

    def __init__(
        self,
        queue_manager,
        logger,
        sensor_nodes: dict,
        shared_athlete_state,
    ) -> None:
        self._queue_manager       = queue_manager
        self._logger              = logger
        self._sensor_nodes        = sensor_nodes    # {node_id: SensorNode}
        self._shared_state        = shared_athlete_state

        # --- State fase ---
        self.current_phase        = SessionPhase.RESTING
        self.current_phase_index  = 0
        self.phase_start_time: float | None   = None
        self.session_start_time: float | None = None

        # --- Counters ---
        self.total_samples    = 0
        self.packet_loss_count = 0
        self.anomaly_count    = 0

        # Statistik per fase: {SessionPhase: {samples, duration_s, anomalies}}
        self.phase_stats: dict = {
            p: {"samples": 0, "duration_s": 0.0, "anomalies": 0}
            for p in self.PHASE_SEQUENCE
        }

        # --- Threading events ---
        self.pause_event     = threading.Event()   # set = pause loop
        self.stop_event      = threading.Event()   # set = hentikan sesi
        self.emergency_event = threading.Event()   # set = darurat medis

        # --- Lock untuk operasi stats (aman dari thread dashboard) ---
        self._stats_lock = threading.Lock()

    # =========================================================================
    #  start_session
    # =========================================================================

    def start_session(self) -> None:
      
        now = time.monotonic()
        self.session_start_time  = now
        self.phase_start_time    = now
        self.current_phase       = self.PHASE_SEQUENCE[0]
        self.current_phase_index = 0
        self.total_samples       = 0
        self.packet_loss_count   = 0
        self.anomaly_count       = 0

        # Reset semua events
        self.pause_event.clear()
        self.stop_event.clear()
        self.emergency_event.clear()

        # Log dan update shared state
        self._shared_state["phase"]         = self.current_phase.name
        self._shared_state["session_start"] = datetime.now().isoformat()

        self._logger.log_phase_transition(
            old_phase=self.current_phase,    # RESTING → RESTING (initial)
            new_phase=self.current_phase,
        )
        self._logger.log_event(
            self.current_phase, "SYSTEM", "DATA",
            "session_start", 0.0,
        )

    # =========================================================================
    #  check_phase_transition
    # =========================================================================

    def check_phase_transition(self) -> bool:
        
        if self.phase_start_time is None:
            return False

        elapsed = time.monotonic() - self.phase_start_time
        target_duration = PHASE_DURATION_S.get(self.current_phase, 30)

        if elapsed < target_duration:
            return False

        # Catat durasi aktual fase yang baru selesai
        with self._stats_lock:
            self.phase_stats[self.current_phase]["duration_s"] = round(elapsed, 2)

        old_phase = self.current_phase

        # Cek apakah ini fase terakhir
        if self.current_phase_index >= len(self.PHASE_SEQUENCE) - 1:
            # Sesi selesai
            self.stop_event.set()
            self._logger.log_event(
                old_phase, "SYSTEM", "DATA",
                "session_complete",
                time.monotonic() - self.session_start_time,
            )
            return False

        # Advance ke fase berikutnya
        self.current_phase_index += 1
        new_phase = self.PHASE_SEQUENCE[self.current_phase_index]
        self.current_phase  = new_phase
        self.phase_start_time = time.monotonic()

        # Log transisi
        self._logger.log_phase_transition(old_phase, new_phase)

        # Simulasi latensi komunikasi fase baru
        extra_latency = PHASE_LATENCY_FACTOR.get(new_phase, 0)
        total_latency = BASE_LATENCY_MS + extra_latency

        # Enqueue phase change event ke FUSION_Q dan LOG_Q
        phase_event = {
            "event_type":      "PHASE_CHANGE",
            "old_phase":       old_phase.name,
            "new_phase":       new_phase.name,
            "timestamp":       datetime.now().isoformat(timespec="milliseconds"),
            "latency_ms":      total_latency,
        }
        self._queue_manager.enqueue("FUSION_Q", phase_event, source_node="SESSION")
        self._queue_manager.enqueue("LOG_Q",    phase_event, source_node="SESSION")

        # Update shared state
        self._shared_state["phase"] = new_phase.name

        return True

    # =========================================================================
    #  collect_one_cycle
    # =========================================================================

    def collect_one_cycle(self) -> list:
       
        samples = []
        phase   = self.current_phase

        for node_id, node in self._sensor_nodes.items():
            sample = None

            # --- Attempt 1 ---
            try:
                sample = node.acquire_sample(phase)
            except PacketLossError:
                self._increment_packet_loss()
                # --- Attempt 2 (single retry) ---
                try:
                    sample = node.acquire_sample(phase)
                except PacketLossError:
                    self._increment_packet_loss()
                    # Log loss dan skip node cycle ini
                    self._logger.log_event(
                        phase, node_id, "WARNING",
                        "packet_loss_double", 0.0,
                        latency_ms=BASE_LATENCY_MS,
                    )
                    self._queue_manager.increment_packet_loss(2)
                    continue
                except Exception as exc:
                    self._logger.log_event(
                        phase, node_id, "WARNING",
                        "acquire_error", 0.0,
                    )
                    continue
            except Exception as exc:
                self._logger.log_event(
                    phase, node_id, "WARNING",
                    "acquire_error", 0.0,
                )
                continue

            if sample is not None:
                samples.append((node_id, sample, phase))

        return samples

    # =========================================================================
    #  run — loop utama sesi
    # =========================================================================

    def run(self, fusion_engine) -> None:
        
        if self.session_start_time is None:
            self.start_session()

        cycle_count = 0

        while not self.stop_event.is_set():

            # --- 1. Pause check ---
            if self.pause_event.is_set():
                time.sleep(0.1)
                continue

            # --- 2. Phase transition check ---
            self.check_phase_transition()

            # Cek lagi setelah transisi (bisa saja langsung stop)
            if self.stop_event.is_set():
                break

            # --- 3. Collect raw samples ---
            raw_samples = self.collect_one_cycle()
            if not raw_samples:
                # Semua node drop — tunggu sebentar dan retry
                time.sleep(0.1)
                continue

            # --- 4. Enqueue raw samples ke RAW_DATA_Q ---
            for node_id, sample, phase in raw_samples:
                self._queue_manager.enqueue(
                    "RAW_DATA_Q", sample, source_node=node_id
                )

            # --- 5. Parallel feature extraction + fusion ---
            try:
                fused_result = fusion_engine.process_parallel(raw_samples)
            except Exception as exc:
                self._logger.log_event(
                    self.current_phase, "FUSION", "WARNING",
                    "fusion_error", 0.0,
                )
                time.sleep(0.1)
                continue

            fused_metrics = fused_result.get("fused_metrics", {})

            # --- 6. Enqueue fused result ke FUSION_Q ---
            fused_packet = {
                **{k: v for k, v in fused_metrics.items() if k != "anomalies"},
                "processing_time_ms": fused_result.get("processing_time_ms", 0),
                "mode":               fused_result.get("mode", "PARALLEL"),
                "cycle":              cycle_count,
            }
            self._queue_manager.enqueue(
                "FUSION_Q", fused_packet, source_node="FUSION_ENGINE"
            )

            # --- 7. Handle anomaly ---
            anomalies = fused_metrics.get("anomalies", [])
            if anomalies:
                self.handle_anomaly(anomalies, fused_metrics)

            # --- 8. Update counters ---
            with self._stats_lock:
                n_collected = len(raw_samples)
                self.total_samples += n_collected
                self.phase_stats[self.current_phase]["samples"] += n_collected

            cycle_count += 1

            # Update shared state dengan info sesi
            self._shared_state["total_samples"]    = self.total_samples
            self._shared_state["packet_loss_total"] = self.packet_loss_count
            self._shared_state["cycle_count"]      = cycle_count

            # --- 9. Throttle ke update interval (400 ms dari config) ---
            time.sleep(config.UPDATE_INTERVAL_MS / 1000.0)

        # --- Loop selesai ---
        session_duration = time.monotonic() - (self.session_start_time or 0)
        self._logger.log_event(
            self.current_phase, "SYSTEM", "DATA",
            "session_ended",
            round(session_duration, 2),
        )

    # =========================================================================
    #  handle_anomaly
    # =========================================================================

    def handle_anomaly(self, anomalies: list, fused_metrics: dict) -> None:
        
        phase = self.current_phase
        seen_critical = False

        for anomaly in anomalies:
            level     = anomaly.get("type", "WARNING")
            param     = anomaly.get("param", anomaly.get("type", "unknown"))
            value     = anomaly.get("value", 0.0)
            node_id   = anomaly.get("node", "FUSION")
            threshold = anomaly.get("threshold")

            latency_ms = BASE_LATENCY_MS + PHASE_LATENCY_FACTOR.get(phase, 0)

            if level == "CRITICAL":
                # Cek kondisi darurat medis spesifik
                is_emergency = False

                if param == "spo2_pct" and value < 90.0:
                    is_emergency = True
                elif param == "heart_rate_bpm" and value > 200.0:
                    is_emergency = True
                elif anomaly.get("type") == "FALL_DETECTED":
                    is_emergency = True

                if is_emergency and not seen_critical:
                    self.emergency_event.set()
                    seen_critical = True

                self._logger.log_event(
                    phase, node_id, "CRITICAL",
                    param, float(value),
                    latency_ms=latency_ms,
                )

                # Enqueue ke LOG_Q untuk persistent record
                self._queue_manager.enqueue(
                    "LOG_Q",
                    {
                        "event_type": "CRITICAL",
                        "param":      param,
                        "value":      value,
                        "threshold":  threshold,
                        "node":       node_id,
                        "phase":      phase.name,
                    },
                    source_node="SESSION_CONTROLLER",
                )

            elif level == "WARNING":
                self._logger.log_event(
                    phase, node_id, "WARNING",
                    param, float(value),
                    latency_ms=latency_ms,
                )
                self._queue_manager.enqueue(
                    "LOG_Q",
                    {
                        "event_type": "WARNING",
                        "param":      param,
                        "value":      value,
                        "threshold":  threshold,
                        "node":       node_id,
                        "phase":      phase.name,
                    },
                    source_node="SESSION_CONTROLLER",
                )

            elif level in ("MOTION_ARTIFACT", "FALL_DETECTED"):
                # Artefak non-klinis tapi perlu dicatat
                self._logger.log_event(
                    phase, node_id, "WARNING",
                    level.lower(), float(value),
                    latency_ms=latency_ms,
                )

        with self._stats_lock:
            self.anomaly_count += len(anomalies)
            self.phase_stats[phase]["anomalies"] += len(anomalies)

    # =========================================================================
    #  get_session_stats
    # =========================================================================

    def get_session_stats(self) -> dict:
        
        now = time.monotonic()
        session_duration = (
            now - self.session_start_time
            if self.session_start_time is not None
            else 0.0
        )

        with self._stats_lock:
            phase_snapshot = {
                phase.name: dict(stats)
                for phase, stats in self.phase_stats.items()
            }
            total_samples    = self.total_samples
            packet_loss      = self.packet_loss_count
            anomaly_count    = self.anomaly_count

        return {
            "session_duration_s":  round(session_duration, 2),
            "current_phase":       self.current_phase.name,
            "current_phase_index": self.current_phase_index,
            "total_samples":       total_samples,
            "packet_loss_count":   packet_loss,
            "anomaly_count":       anomaly_count,
            "emergency_triggered": self.emergency_event.is_set(),
            "is_paused":           self.pause_event.is_set(),
            "is_stopped":          self.stop_event.is_set(),
            "phase_stats":         phase_snapshot,
            "queue_stats":         self._queue_manager.get_stats(),
        }

    # =========================================================================
    #  Control helpers (thread-safe, bisa dipanggil dari thread lain)
    # =========================================================================

    def pause(self) -> None:
        """Pause loop — node tidak mengambil sample."""
        self.pause_event.set()
        self._logger.log_event(
            self.current_phase, "SYSTEM", "DATA", "session_paused", 0.0
        )

    def resume(self) -> None:
        """Resume dari pause."""
        self.pause_event.clear()
        self._logger.log_event(
            self.current_phase, "SYSTEM", "DATA", "session_resumed", 0.0
        )

    def stop(self) -> None:
        """Hentikan sesi secara paksa (emergency stop)."""
        self.stop_event.set()
        self._logger.log_event(
            self.current_phase, "SYSTEM", "CRITICAL", "session_force_stop", 0.0
        )

    def clear_emergency(self) -> None:
        """Reset emergency event setelah penanganan (misal, atlet sudah aman)."""
        self.emergency_event.clear()

    # =========================================================================
    #  Private helpers
    # =========================================================================

    def _increment_packet_loss(self) -> None:
        with self._stats_lock:
            self.packet_loss_count += 1

    def __repr__(self) -> str:
        return (
            f"SessionController("
            f"phase={self.current_phase.name}, "
            f"samples={self.total_samples}, "
            f"losses={self.packet_loss_count}, "
            f"anomalies={self.anomaly_count}, "
            f"emergency={self.emergency_event.is_set()})"
        )
