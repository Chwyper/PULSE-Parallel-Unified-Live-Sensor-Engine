# =============================================================================
#  sensor_nodes.py — PULSE: Parallel Unified Live Sensor Engine
#  Simulator untuk semua 8 sensor node wearable.
#  Menghasilkan data fisiologis sintetis yang realistis per fase sesi.
# =============================================================================

import math
import random
import time
from datetime import datetime

# pyrefly: ignore [missing-import]
import numpy as np

import config
from config import NODE_CONFIG, PHASE_PHYSIO, PROCESSING_DELAY_S
from config import PACKET_LOSS_RATE, SessionPhase

# =============================================================================
#  Lead offset map
#  Tiap lead ECG punya perbedaan sudut proyeksi terhadap sumbu jantung.
#  Nilai = faktor pengali amplitudo + offset fase (dalam radian × π).
#  aVR secara klinis terbalik (amplitudo negatif), aVL lebih kecil.
# =============================================================================

_LEAD_PARAMS: dict[str, dict] = {
    "Lead-I":   {"amp_factor": 1.00, "phase_offset": 0.00},
    "Lead-II":  {"amp_factor": 1.15, "phase_offset": 0.05 * math.pi},
    "Lead-III": {"amp_factor": 0.85, "phase_offset": 0.10 * math.pi},
    "aVR":      {"amp_factor": -0.70, "phase_offset": -0.15 * math.pi},
    "aVL":      {"amp_factor": 0.55, "phase_offset": 0.07 * math.pi},
}

# =============================================================================
#  EXCEPTION
# =============================================================================

class PacketLossError(Exception):
    """Dilempar saat paket data disimulasikan hilang (BLE/WiFi packet loss)."""
    pass

# =============================================================================
#  GENERATOR: ECG
# =============================================================================

def _gaussian(t: float, mu: float, sigma: float) -> float:
    """Gaussian bell curve — dipakai untuk membentuk gelombang P dan T."""
    return math.exp(-0.5 * ((t - mu) / sigma) ** 2)


def generate_ecg_sample(
    phase: SessionPhase,
    lead: str,
    t: float,
) -> float:
   
    physio      = PHASE_PHYSIO[phase]
    hr_min, hr_max = physio["heart_rate_bpm"]
    # Heart rate sedikit bervariasi per-sample (±2 bpm) untuk realisme
    heart_rate  = random.uniform(hr_min, hr_max)
    rr_interval = 60.0 / heart_rate           # durasi satu siklus R-R (detik)

    # Posisi dalam siklus jantung saat ini (0.0 – 1.0)
    lead_cfg    = _LEAD_PARAMS.get(lead, _LEAD_PARAMS["Lead-I"])
    phase_off   = lead_cfg["phase_offset"]
    amp_factor  = lead_cfg["amp_factor"]

    # Waktu dalam siklus setelah normalisasi + offset lead
    t_cycle = (t % rr_interval) / rr_interval  # [0, 1)
    t_shifted = (t_cycle + phase_off / (2 * math.pi)) % 1.0

    # --- Gelombang P (0.10–0.20 siklus) ---
    # Amplitudo 0.15 mV, lebar sigma 0.04
    p_wave = 0.15 * _gaussian(t_shifted, mu=0.15, sigma=0.04)

    # --- Kompleks QRS (0.38–0.45 siklus) ---
    # Q kecil negatif, R spike tinggi, S kecil negatif
    q_wave  = -0.10 * _gaussian(t_shifted, mu=0.38, sigma=0.012)
    r_wave  =  1.20 * _gaussian(t_shifted, mu=0.42, sigma=0.018)
    s_wave  = -0.25 * _gaussian(t_shifted, mu=0.46, sigma=0.012)

    # --- Gelombang T (0.60–0.72 siklus) ---
    t_wave  =  0.35 * _gaussian(t_shifted, mu=0.65, sigma=0.055)

    # Superposisi semua komponen × faktor lead
    signal = amp_factor * (p_wave + q_wave + r_wave + s_wave + t_wave)

    # Gaussian noise: std = 0.02 mV (simulasi elektroda noise)
    noise  = np.random.normal(0, 0.02)

    # Baseline wander lambat (gerakan tubuh) — sangat kecil
    baseline_wander = 0.03 * math.sin(2 * math.pi * 0.15 * t)

    return float(np.clip(signal + noise + baseline_wander, -1.5, 2.0))


# =============================================================================
#  GENERATOR: ACCELEROMETER
# =============================================================================

def generate_accel_sample(phase: SessionPhase) -> dict:
   
    physio          = PHASE_PHYSIO[phase]
    mag_min, mag_max = physio["accel_magnitude_g"]
    target_mag      = random.uniform(mag_min, mag_max)

    # Buat unit vektor acak di bola satuan (Marsaglia method)
    while True:
        x = random.uniform(-1, 1)
        y = random.uniform(-1, 1)
        z = random.uniform(-1, 1)
        norm = math.sqrt(x**2 + y**2 + z**2)
        if 0.001 < norm <= 1.0:
            break

    # Skala ke target magnitude
    x = (x / norm) * target_mag
    y = (y / norm) * target_mag
    z = (z / norm) * target_mag

    # Komponen gravitasi statis dominan di Z (atlet tegak)
    gravity_component = 1.0 - target_mag * 0.3   # makin aktif, makin berkurang
    z += max(gravity_component, 0.0)

    # Noise sensor MPU6050: ±0.01 g
    noise_std = 0.01
    x += np.random.normal(0, noise_std)
    y += np.random.normal(0, noise_std)
    z += np.random.normal(0, noise_std)

    magnitude = math.sqrt(x**2 + y**2 + z**2)

    return {
        "X":         round(float(x), 4),
        "Y":         round(float(y), 4),
        "Z":         round(float(z), 4),
        "magnitude": round(float(magnitude), 4),
    }


# =============================================================================
#  GENERATOR: SpO2
# =============================================================================

def generate_spo2_sample(phase: SessionPhase) -> dict:
    
    physio              = PHASE_PHYSIO[phase]
    spo2_min, spo2_max  = physio["spo2_pct"]
    hr_min, hr_max      = physio["heart_rate_bpm"]

    spo2 = random.uniform(spo2_min, spo2_max)
    hr   = random.uniform(hr_min, hr_max)

    # PI: makin tinggi SpO2 → makin baik perfusi (range 0.5–10.0)
    # Normalisasi SpO2 dari [90–100] ke faktor [0.0–1.0]
    spo2_norm = (spo2 - 90.0) / 10.0
    pi_base   = 0.5 + spo2_norm * 9.5       # [0.5, 10.0]
    pi        = pi_base * random.uniform(0.85, 1.15)   # ±15% variasi
    pi        = float(np.clip(pi, 0.5, 10.0))

    return {
        "spo2_pct":       round(float(spo2), 2),
        "heart_rate_bpm": round(float(hr), 1),
        "perfusion_index": round(pi, 2),
    }


# =============================================================================
#  GENERATOR: ENVIRONMENTAL (multi-sensor)
# =============================================================================

def generate_env_sample(phase: SessionPhase) -> dict:
    
    physio                  = PHASE_PHYSIO[phase]
    temp_min, temp_max      = physio["skin_temp_c"]
    gsr_min, gsr_max        = physio["gsr_kohm"]
    resp_min, resp_max      = physio["resp_rate_bpm"]

    skin_temp   = random.uniform(temp_min, temp_max)
    gsr         = random.uniform(gsr_min, gsr_max)
    resp_rate   = random.uniform(resp_min, resp_max)

    # Noise kecil untuk masing-masing sensor
    skin_temp  += np.random.normal(0, 0.05)   # ±0.05 °C
    gsr        += np.random.normal(0, gsr * 0.02)  # ±2% GSR
    resp_rate  += np.random.normal(0, 0.3)    # ±0.3 napas/menit

    return {
        "skin_temp_c":   round(float(np.clip(skin_temp, 34.0, 42.0)), 2),
        "gsr_kohm":      round(float(max(gsr, 0.1)), 2),
        "resp_rate_bpm": round(float(np.clip(resp_rate, 4.0, 60.0)), 1),
    }


# =============================================================================
#  CLASS: SensorNode
# =============================================================================

class SensorNode:
    

    def __init__(self, node_id: str, shared_athlete_state) -> None:
        if node_id not in NODE_CONFIG:
            raise ValueError(f"Unknown node_id: '{node_id}'. Valid: {list(NODE_CONFIG.keys())}")

        self.node_id             = node_id
        self._cfg                = NODE_CONFIG[node_id]
        self.node_type: str      = self._cfg["type"]
        self.chip: str           = self._cfg["chip"]
        self.sample_rate: int    = self._cfg["sample_rate"]
        self.shared_athlete_state = shared_athlete_state

        # Counter waktu kontinu untuk ECG synthesis
        self.t: float            = 0.0
        self._dt: float          = 1.0 / self.sample_rate   # step waktu per sample

        # Counter sample dan loss
        self.sample_index: int   = 0
        self.packet_loss_count: int = 0

        # Lead ECG (hanya untuk tipe ECG)
        self._lead: str | None   = self._cfg.get("lead")

    # -------------------------------------------------------------------------
    #  acquire_sample
    # -------------------------------------------------------------------------

    def acquire_sample(self, phase: SessionPhase) -> dict:
       
        # --- Step 1: Generate payload sensor ---
        payload = self._generate_payload(phase)

        # --- Step 2: Simulasi latensi pemrosesan ---
        delay_min, delay_max = PROCESSING_DELAY_S[self.node_type]
        time.sleep(random.uniform(delay_min, delay_max))

        # --- Step 3: Simulasi packet loss ---
        if random.random() < PACKET_LOSS_RATE:
            self.packet_loss_count += 1
            raise PacketLossError(
                f"[{self.node_id}] Packet lost "
                f"(total_loss={self.packet_loss_count}, "
                f"phase={phase.name}, index={self.sample_index})"
            )

        # --- Step 4: Bangun paket lengkap ---
        sample = {
            # Metadata
            "node_id":      self.node_id,
            "node_type":    self.node_type,
            "chip":         self.chip,
            "phase":        phase.name,
            "timestamp":    datetime.now().isoformat(timespec="milliseconds"),
            "sample_index": self.sample_index,
            # Payload sensor
            **payload,
        }

        # --- Step 5: Update shared state (nilai flat untuk dashboard/fusion) ---
        try:
            self._update_shared_state(phase, payload)
        except Exception:
            # Shared state update non-fatal — lanjutkan meski gagal
            pass

        # --- Step 6: Increment counters ---
        self.sample_index += 1
        self.t += self._dt

        return sample

    # -------------------------------------------------------------------------
    #  Private helpers
    # -------------------------------------------------------------------------

    def _generate_payload(self, phase: SessionPhase) -> dict:
        """Dispatch ke generator yang sesuai berdasarkan node_type."""
        if self.node_type == "ECG":
            lead = self._lead or "Lead-I"
            return {"ecg_mv": generate_ecg_sample(phase, lead, self.t), "lead": lead}

        elif self.node_type == "ACCEL":
            return generate_accel_sample(phase)

        elif self.node_type == "SPO2":
            return generate_spo2_sample(phase)

        elif self.node_type == "ENV":
            return generate_env_sample(phase)

        else:
            raise ValueError(f"Unknown node type: '{self.node_type}'")

    def _update_shared_state(self, phase: SessionPhase, payload: dict) -> None:
       
        # Prefixed keys — unique per node
        for field, value in payload.items():
            self.shared_athlete_state[f"{self.node_id}.{field}"] = value

        # Top-level physiological keys (dipakai oleh fusion processor)
        if self.node_type == "SPO2":
            self.shared_athlete_state["spo2_pct"]       = payload.get("spo2_pct", 0)
            self.shared_athlete_state["heart_rate_bpm"] = payload.get("heart_rate_bpm", 0)
        elif self.node_type == "ACCEL":
            self.shared_athlete_state["accel_magnitude_g"] = payload.get("magnitude", 0)
        elif self.node_type == "ENV":
            self.shared_athlete_state["skin_temp_c"]   = payload.get("skin_temp_c", 0)
            self.shared_athlete_state["gsr_kohm"]      = payload.get("gsr_kohm", 0)
            self.shared_athlete_state["resp_rate_bpm"] = payload.get("resp_rate_bpm", 0)

        # Update timestamp dan fase aktif
        self.shared_athlete_state["last_update"] = datetime.now().isoformat(timespec="milliseconds")
        self.shared_athlete_state["phase"]        = phase.name

    def __repr__(self) -> str:
        return (
            f"SensorNode(id={self.node_id!r}, type={self.node_type!r}, "
            f"chip={self.chip!r}, sr={self.sample_rate}Hz, "
            f"samples={self.sample_index}, losses={self.packet_loss_count})"
        )


# =============================================================================
#  FACTORY FUNCTION
# =============================================================================

def create_all_nodes(shared_state) -> dict[str, SensorNode]:
   
    nodes: dict[str, SensorNode] = {}
    for node_id in NODE_CONFIG:
        nodes[node_id] = SensorNode(node_id, shared_state)
    return nodes
