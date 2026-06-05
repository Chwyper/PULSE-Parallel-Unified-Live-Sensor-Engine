# =============================================================================
#  fusion_engine.py — PULSE: Parallel Unified Live Sensor Engine
#  Jantung sistem: parallel feature extraction + multi-sensor data fusion.
# =============================================================================

import math
import multiprocessing
import os
import random
import statistics
import time
from collections import deque
from datetime import datetime

import config
from config import (
    NODE_CONFIG, PHASE_PHYSIO, PROCESSING_DELAY_S,
    ANOMALY_THRESHOLDS, NODE_TIMEOUT_S, MAX_RECOVERY_ATTEMPTS,
    RECOVERY_BACKOFF_S, NUM_WORKER_PROCESSES, SessionPhase,
)



def process_node_data(args: tuple) -> dict:
    """
    Feature extraction untuk satu channel sensor node.

    Top-level function — dipanggil oleh multiprocessing.Pool.map() di
    setiap worker process secara paralel untuk masing-masing node.

    Parameters
    ----------
    args : tuple — (node_id: str, raw_sample: dict, phase: SessionPhase)

    Returns
    -------
    dict:
        node_id           : str
        node_type         : str
        features          : dict   — hasil feature extraction
        processing_time_ms: float  — durasi ekstraksi dalam ms
        worker_pid        : int    — PID proses worker
        anomalies         : list   — daftar anomali terdeteksi
    """
    node_id, raw_sample, phase = args
    node_type = NODE_CONFIG[node_id]["type"]
    t_start   = time.perf_counter()

    features  : dict = {}
    anomalies : list = []

    # -------------------------------------------------------------------------
    #  ECG Feature Extraction
    #  Simulasikan window kecil di sekitar sample saat ini untuk analisis
    #  statistik (dalam sistem real, ini adalah rolling buffer 500 Hz).
    # -------------------------------------------------------------------------
    if node_type == "ECG":
        ecg_val   = raw_sample.get("ecg_mv", 0.0)
        lead      = raw_sample.get("lead", "Lead-I")

        # Simulasi window 20-sample (40 ms @ 500 Hz) di sekitar nilai saat ini
        window_size = 20
        window = [
            ecg_val + random.gauss(0, 0.015)
            for _ in range(window_size)
        ]
        # Pastikan sample aktual ada dalam window
        window[window_size // 2] = ecg_val

        # R-peak amplitude: nilai absolut maksimum dalam window
        r_peak_amp = max(abs(v) for v in window)

        # RMS voltage: root-mean-square menggambarkan energi sinyal
        rms = math.sqrt(sum(v ** 2 for v in window) / len(window))

        # Estimasi heart rate dari fase fisiologis + variasi
        hr_min, hr_max = PHASE_PHYSIO[phase]["heart_rate_bpm"]
        hr_est = random.uniform(hr_min, hr_max)

        # Deteksi artefak gerak: std window > 0.5 mV
        mean_w = sum(window) / len(window)
        variance = sum((v - mean_w) ** 2 for v in window) / len(window)
        std_w  = math.sqrt(variance)
        motion_artifact = std_w > 0.5

        features = {
            "lead":               lead,
            "r_peak_amp_mv":      round(r_peak_amp, 4),
            "rms_mv":             round(rms, 4),
            "hr_estimate_bpm":    round(hr_est, 1),
            "window_std_mv":      round(std_w, 4),
            "motion_artifact":    motion_artifact,
        }

        if motion_artifact:
            anomalies.append({
                "type":    "MOTION_ARTIFACT",
                "node":    node_id,
                "detail":  f"ECG std={std_w:.3f}mV > 0.5mV threshold",
            })

        # Cek anomali HR
        _check_threshold("heart_rate_bpm", hr_est, anomalies, node_id)

    # -------------------------------------------------------------------------
    #  ACCEL Feature Extraction
    # -------------------------------------------------------------------------
    elif node_type == "ACCEL":
        mag    = raw_sample.get("magnitude", 0.0)
        ax     = raw_sample.get("X", 0.0)
        ay     = raw_sample.get("Y", 0.0)
        az     = raw_sample.get("Z", 0.0)

        # Step count estimasi: zero-crossing pada magnitude minus gravitasi (1g)
        # Simulasi dengan window sintetis dari magnitude saat ini
        # Dalam sistem real: zero-crossing pada high-pass filtered vertical axis
        win_size  = 30
        gravity   = 1.0
        mag_win   = [
            mag - gravity + random.gauss(0, 0.05)
            for _ in range(win_size)
        ]
        # Hitung zero-crossing (sign change) dari filtered magnitude
        crossings = sum(
            1 for i in range(1, len(mag_win))
            if (mag_win[i - 1] < 0) != (mag_win[i] < 0)
        )
        # Setiap 2 crossing ≈ 1 langkah; skala ke per-menit
        step_count = crossings // 2
        cadence_spm = step_count * (60.0 / (win_size / 100.0))   # 100 Hz sample rate

        # Deteksi jatuh: magnitude > 4g (impak mendadak)
        fall_detected = mag > 4.0

        features = {
            "magnitude_g":  round(mag, 4),
            "axis_x":       round(ax, 4),
            "axis_y":       round(ay, 4),
            "axis_z":       round(az, 4),
            "step_count":   step_count,
            "cadence_spm":  round(cadence_spm, 1),
            "fall_detected": fall_detected,
        }

        if fall_detected:
            anomalies.append({
                "type":   "FALL_DETECTED",
                "node":   node_id,
                "detail": f"Magnitude={mag:.2f}g > 4g fall threshold",
            })

    # -------------------------------------------------------------------------
    #  SpO2 Feature Extraction
    # -------------------------------------------------------------------------
    elif node_type == "SPO2":
        spo2 = raw_sample.get("spo2_pct", 98.0)
        hr   = raw_sample.get("heart_rate_bpm", 70.0)
        pi   = raw_sample.get("perfusion_index", 5.0)

        # Validasi rentang normal olahraga (94–100%)
        spo2_normal = 94.0 <= spo2 <= 100.0

        # Trend SpO2: bandingkan dengan midpoint range fase
        spo2_min, spo2_max = PHASE_PHYSIO[phase]["spo2_pct"]
        spo2_mid   = (spo2_min + spo2_max) / 2
        spo2_trend = "STABLE"
        if spo2 < spo2_mid - 1.0:
            spo2_trend = "FALLING"
        elif spo2 > spo2_mid + 1.0:
            spo2_trend = "RISING"

        # Pulse quality: dari perfusion index (0.5–10)
        pulse_quality = min(100.0, pi * 10.0)

        features = {
            "spo2_pct":       round(spo2, 2),
            "heart_rate_bpm": round(hr, 1),
            "perfusion_index": round(pi, 2),
            "spo2_normal":    spo2_normal,
            "spo2_trend":     spo2_trend,
            "pulse_quality":  round(pulse_quality, 1),
        }

        # Cek ambang batas anomali
        _check_threshold("spo2_pct", spo2, anomalies, node_id)

    # -------------------------------------------------------------------------
    #  ENV Feature Extraction
    # -------------------------------------------------------------------------
    elif node_type == "ENV":
        temp      = raw_sample.get("skin_temp_c", 37.0)
        gsr       = raw_sample.get("gsr_kohm", 50.0)
        resp_rate = raw_sample.get("resp_rate_bpm", 15.0)

        # Heat Index sederhana dari suhu kulit + GSR proxy
        # GSR rendah = berkeringat = efek pendinginan berkurang saat kelembapan tinggi
        # Formula adaptasi: HI = T + (0.5 × T_excess) - (gsr_factor × cooling)
        gsr_factor     = max(0.0, min(1.0, (gsr - 3.0) / 77.0))  # norm [3,80] → [0,1]
        cooling_effect = gsr_factor * 0.8
        heat_index     = temp + max(0, temp - 37.0) * 0.5 - cooling_effect
        heat_index     = round(heat_index, 2)

        # Respiratory efficiency score (0–100)
        # Efisiensi optimal pada ~15 napas/menit; buruk jika terlalu cepat/lambat
        resp_optimal   = 15.0
        resp_dev       = abs(resp_rate - resp_optimal)
        resp_score     = max(0.0, 100.0 - resp_dev * 3.0)

        features = {
            "skin_temp_c":    round(temp, 2),
            "gsr_kohm":       round(gsr, 2),
            "resp_rate_bpm":  round(resp_rate, 1),
            "heat_index":     heat_index,
            "resp_efficiency": round(resp_score, 1),
        }

        # Cek anomali
        _check_threshold("skin_temp_c",   temp,      anomalies, node_id)
        _check_threshold("gsr_kohm",      gsr,       anomalies, node_id)
        _check_threshold("resp_rate_bpm", resp_rate, anomalies, node_id)

    # Simulasi latensi pemrosesan chip sesuai node type
    d_min, d_max = PROCESSING_DELAY_S.get(node_type, (0.01, 0.02))
    time.sleep(random.uniform(d_min, d_max))

    processing_time_ms = (time.perf_counter() - t_start) * 1000.0

    return {
        "node_id":             node_id,
        "node_type":           node_type,
        "features":            features,
        "processing_time_ms":  round(processing_time_ms, 3),
        "worker_pid":          os.getpid(),
        "anomalies":           anomalies,
    }


def _check_threshold(param: str, value: float, anomalies: list, node_id: str) -> None:
    """
    Helper: cek satu nilai terhadap ANOMALY_THRESHOLDS dan append ke anomalies.
    Top-level agar bisa dipanggil oleh process_node_data di worker process.
    """
    thresholds = ANOMALY_THRESHOLDS.get(param)
    if not thresholds:
        return

    for level in ("CRITICAL", "WARNING"):   # CRITICAL dicek lebih dulu
        spec = thresholds.get(level, {})
        above = spec.get("above")
        below = spec.get("below")

        triggered = False
        direction = ""
        threshold_val = None

        if above is not None and value > above:
            triggered    = True
            direction    = "above"
            threshold_val = above
        elif below is not None and value < below:
            triggered    = True
            direction    = "below"
            threshold_val = below

        if triggered:
            anomalies.append({
                "type":      level,
                "node":      node_id,
                "param":     param,
                "value":     round(value, 3),
                "threshold": threshold_val,
                "direction": direction,
            })
            break   # jangan double-append WARNING jika sudah CRITICAL


# =============================================================================
#  CLASS: FusionEngine
# =============================================================================

class FusionEngine:
  

    def __init__(self, queue_manager, logger, shared_athlete_state) -> None:
        self._queue_manager       = queue_manager
        self._logger              = logger
        self._shared_state        = shared_athlete_state

        # Pool worker: 1 proses per sensor node
        self._pool = multiprocessing.Pool(processes=NUM_WORKER_PROCESSES)

        # Lock untuk akses shared_athlete_state
        self._lock = multiprocessing.Lock()

        # Inisialisasi timeout tracker: semua node dianggap baru aktif
        now = time.monotonic()
        self.node_timeout_tracker: dict[str, float] = {
            node_id: now for node_id in NODE_CONFIG
        }

        # Riwayat fusi (max 100 entri)
        self.fusion_history: deque = deque(maxlen=100)

        # Akumulator langkah kaki sepanjang sesi
        self._steps_total: int = 0

        # Cache nilai terakhir per node (untuk fallback recovery)
        self._last_known: dict[str, dict] = {}

    # -------------------------------------------------------------------------
    #  process_parallel
    # -------------------------------------------------------------------------

    def process_parallel(self, raw_samples: list) -> dict:
        
        t_wall_start = time.perf_counter()

        # pool.map: distribusikan semua 8 tuple ke pool secara paralel
        # Pool akan assign ke worker dari pool — pada mesin multi-core
        # beberapa worker akan jalan secara bersamaan di core berbeda
        processed_list: list[dict] = self._pool.map(
            process_node_data, raw_samples
        )

        wall_ms = (time.perf_counter() - t_wall_start) * 1000.0

        # Update timeout tracker untuk semua node yang berhasil diproses
        now = time.monotonic()
        for result in processed_list:
            nid = result["node_id"]
            self.node_timeout_tracker[nid] = now
            self._last_known[nid] = result

        # Fusi semua hasil menjadi satu set metrik atlet
        fused = self.fuse_results(processed_list)

        per_node_times = {
            r["node_id"]: r["processing_time_ms"]
            for r in processed_list
        }
        worker_pids = list({r["worker_pid"] for r in processed_list})

        return {
            "fused_metrics":       fused,
            "processing_time_ms":  round(wall_ms, 3),
            "per_node_times":      per_node_times,
            "worker_pids":         sorted(worker_pids),
            "anomaly_count":       len(fused.get("anomalies", [])),
            "mode":                "PARALLEL",
        }

    # -------------------------------------------------------------------------
    #  process_sequential
    # -------------------------------------------------------------------------

    def process_sequential(self, raw_samples: list) -> dict:
      
        t_wall_start = time.perf_counter()

        processed_list: list[dict] = []
        for args in raw_samples:
            result = process_node_data(args)
            processed_list.append(result)

        wall_ms = (time.perf_counter() - t_wall_start) * 1000.0

        now = time.monotonic()
        for result in processed_list:
            self.node_timeout_tracker[result["node_id"]] = now
            self._last_known[result["node_id"]] = result

        fused = self.fuse_results(processed_list)

        per_node_times = {
            r["node_id"]: r["processing_time_ms"]
            for r in processed_list
        }

        return {
            "fused_metrics":       fused,
            "processing_time_ms":  round(wall_ms, 3),
            "per_node_times":      per_node_times,
            "worker_pids":         [os.getpid()],   # hanya main process
            "anomaly_count":       len(fused.get("anomalies", [])),
            "mode":                "SEQUENTIAL",
        }

    # -------------------------------------------------------------------------
    #  fuse_results
    # -------------------------------------------------------------------------

    def fuse_results(self, processed_list: list) -> dict:
        
        athlete_metrics = {}
        all_anomalies   = []

        ecg_hr_values   = []
        ecg_artifact_count = 0
        ecg_total       = 0

        for result in processed_list:
            ntype    = result["node_type"]
            features = result["features"]
            all_anomalies.extend(result.get("anomalies", []))

            if ntype == "ECG":
                ecg_total += 1
                if not features.get("motion_artifact", False):
                    ecg_hr_values.append(features.get("hr_estimate_bpm", 0))
                else:
                    ecg_artifact_count += 1

            elif ntype == "ACCEL":
                self._steps_total += features.get("step_count", 0)
                athlete_metrics["steps_total"]  = self._steps_total
                athlete_metrics["cadence_spm"]  = features.get("cadence_spm", 0.0)
                athlete_metrics["fall_detected"] = features.get("fall_detected", False)

            elif ntype == "SPO2":
                athlete_metrics["spo2_pct"]      = features.get("spo2_pct", 0.0)
                athlete_metrics["spo2_trend"]    = features.get("spo2_trend", "STABLE")
                athlete_metrics["pulse_quality"] = features.get("pulse_quality", 0.0)

            elif ntype == "ENV":
                athlete_metrics["skin_temp_c"]    = features.get("skin_temp_c", 0.0)
                athlete_metrics["gsr_kohm"]       = features.get("gsr_kohm", 0.0)
                athlete_metrics["resp_rate_bpm"]  = features.get("resp_rate_bpm", 0.0)
                athlete_metrics["heat_index"]     = features.get("heat_index", 0.0)
                athlete_metrics["resp_efficiency"] = features.get("resp_efficiency", 0.0)

        # --- Heart Rate: rata-rata ECG nodes yang bersih ---
        if ecg_hr_values:
            athlete_metrics["heart_rate_bpm"] = round(
                statistics.mean(ecg_hr_values), 1
            )
        else:
            athlete_metrics["heart_rate_bpm"] = 0.0

        # --- ECG Quality: persentase channel bebas artefak ---
        athlete_metrics["ecg_quality"] = round(
            ((ecg_total - ecg_artifact_count) / ecg_total * 100)
            if ecg_total > 0 else 0.0, 1
        )

        # --- Stress Index (0–100): composite dari HR + resp + GSR ---
        hr_norm   = _normalize(athlete_metrics.get("heart_rate_bpm", 70), 50, 200)
        resp_norm = _normalize(athlete_metrics.get("resp_rate_bpm", 15), 8, 50)
        gsr       = athlete_metrics.get("gsr_kohm", 50)
        # GSR: makin rendah = makin stres → invert normalisasi
        gsr_norm  = 1.0 - _normalize(gsr, 0, 80)
        stress_index = round((hr_norm * 0.4 + resp_norm * 0.35 + gsr_norm * 0.25) * 100, 1)
        athlete_metrics["stress_index"] = stress_index

        # --- Exertion Level dari HR ---
        hr = athlete_metrics.get("heart_rate_bpm", 70)
        if hr < 100:
            exertion = "LOW"
        elif hr < 140:
            exertion = "MODERATE"
        elif hr < 170:
            exertion = "HIGH"
        else:
            exertion = "EXTREME"
        athlete_metrics["exertion_level"] = exertion

        # --- Timestamp fusi ---
        athlete_metrics["fused_at"] = datetime.now().isoformat(timespec="milliseconds")
        athlete_metrics["anomalies"] = all_anomalies

        # Update shared_athlete_state
        try:
            with self._lock:
                for k, v in athlete_metrics.items():
                    if k != "anomalies":   # list tidak di-share langsung
                        self._shared_state[f"fused.{k}"] = v
                self._shared_state["fused.anomaly_count"] = len(all_anomalies)
        except Exception:
            pass

        # Tambah ke history (deque auto-truncates ke 100)
        self.fusion_history.append(athlete_metrics)

        return athlete_metrics

    # -------------------------------------------------------------------------
    #  detect_node_timeout
    # -------------------------------------------------------------------------

    def detect_node_timeout(self) -> list[str]:
        
        now     = time.monotonic()
        timed_out = []
        for node_id, last_active in self.node_timeout_tracker.items():
            if (now - last_active) > NODE_TIMEOUT_S:
                timed_out.append(node_id)
        return timed_out

    # -------------------------------------------------------------------------
    #  recover_node
    # -------------------------------------------------------------------------

    def recover_node(self, node_id: str, sensor_nodes: dict) -> bool:
       
        from sensor_nodes import PacketLossError   # import lokal untuk hindari circular

        phase_name = self._shared_state.get("phase", "RESTING")
        try:
            phase = SessionPhase[phase_name]
        except KeyError:
            phase = SessionPhase.RESTING

        for attempt in range(1, MAX_RECOVERY_ATTEMPTS + 1):
            if self._logger:
                self._logger.log_event(
                    phase, node_id, "RECOVERY",
                    "recovery_attempt", float(attempt),
                    latency_ms=0,
                )

            # ---- Attempt 1: Re-sample ----
            if attempt == 1:
                node = sensor_nodes.get(node_id)
                if node is not None:
                    try:
                        sample = node.acquire_sample(phase)
                        # Proses sample recovery dan update timeout tracker
                        result = process_node_data((node_id, sample, phase))
                        self.node_timeout_tracker[node_id] = time.monotonic()
                        self._last_known[node_id] = result
                        if self._logger:
                            self._logger.log_event(
                                phase, node_id, "RECOVERY",
                                "re_sample_success", 1.0,
                            )
                        return True
                    except (PacketLossError, Exception):
                        pass   # lanjut ke attempt berikutnya

            # ---- Attempt 2: Last Known Value ----
            elif attempt == 2:
                if node_id in self._last_known:
                    # "Pinned" nilai terakhir — tandai sebagai stale
                    self._last_known[node_id]["stale"] = True
                    self.node_timeout_tracker[node_id] = time.monotonic()
                    if self._logger:
                        self._logger.log_event(
                            phase, node_id, "RECOVERY",
                            "last_known_value_used", 1.0,
                        )
                    return True

            # ---- Attempt 3: Exclude ----
            elif attempt == MAX_RECOVERY_ATTEMPTS:
                if self._logger:
                    self._logger.log_event(
                        phase, node_id, "WARNING",
                        "node_excluded_from_fusion", 0.0,
                    )
                break

            # Backoff sebelum attempt berikutnya
            time.sleep(RECOVERY_BACKOFF_S)

        return False

    # -------------------------------------------------------------------------
    #  shutdown
    # -------------------------------------------------------------------------

    def shutdown(self) -> None:
       
        self._pool.close()
        self._pool.join()

    def __repr__(self) -> str:
        return (
            f"FusionEngine("
            f"workers={NUM_WORKER_PROCESSES}, "
            f"history={len(self.fusion_history)}/100, "
            f"steps_total={self._steps_total})"
        )


# =============================================================================
#  HELPER FUNCTIONS (module-level)
# =============================================================================

def _normalize(value: float, lo: float, hi: float) -> float:
    """Normalisasi nilai ke [0.0, 1.0]; clamp jika di luar range."""
    if hi == lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))
