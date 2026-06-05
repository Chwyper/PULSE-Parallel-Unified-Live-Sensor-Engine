# =============================================================================
#  config.py — PULSE: Parallel Unified Live Sensor Engine
#  Semua konstanta dan parameter global sistem.
#  Tidak ada fungsi, tidak ada class — hanya konstanta, dict, dan Enum.
# =============================================================================

from enum import Enum, auto

# =============================================================================
#  SECTION 1: PROJECT INFO
#  Identitas proyek dan metadata akademik.
# =============================================================================

PROJECT_NAME  = "PULSE"
PROJECT_FULL  = "Parallel Unified Live Sensor Engine"
VERSION       = "1.0.0"
INSTITUTION   = "ITENAS Bandung — IFB-206"

# =============================================================================
#  SECTION 2: SESSION PHASES
#  Fase sesi monitoring atlet dari istirahat hingga pemulihan.
#  PHASE_DURATION_S: durasi tiap fase dalam detik.
# =============================================================================

class SessionPhase(Enum):
    RESTING  = auto()   # Istirahat — baseline fisiologis
    WARMUP   = auto()   # Pemanasan — aktivitas ringan
    SPRINT   = auto()   # Sprint — intensitas puncak
    COOLDOWN = auto()   # Pendinginan — penurunan intensitas
    RECOVERY = auto()   # Pemulihan — kembali ke baseline

PHASE_DURATION_S = {
    SessionPhase.RESTING:  5,    # dipercepat untuk demo (aslinya 30s)
    SessionPhase.WARMUP:   5,    # (aslinya 60s)
    SessionPhase.SPRINT:   5,    # (aslinya 45s)
    SessionPhase.COOLDOWN: 5,    # (aslinya 60s)
    SessionPhase.RECOVERY: 5,    # (aslinya 30s)
}

# =============================================================================
#  SECTION 3: SENSOR NODE CONFIG
#  Konfigurasi 8 node sensor wearable dengan spesifikasi chip dan parameter
#  sampling. Terinspirasi dari platform CuffnCode (STM32 + pressure sensor).
# =============================================================================

NODE_CONFIG = {
    "NODE-ECG1": {
        "type": "ECG",
        "chip": "ADS1298",
        "lead": "Lead-I",
        "sample_rate": 500,        # Hz
    },
    "NODE-ECG2": {
        "type": "ECG",
        "chip": "ADS1298",
        "lead": "Lead-II",
        "sample_rate": 500,
    },
    "NODE-ECG3": {
        "type": "ECG",
        "chip": "ADS1298",
        "lead": "Lead-III",
        "sample_rate": 500,
    },
    "NODE-ECG4": {
        "type": "ECG",
        "chip": "ADS1298",
        "lead": "aVR",
        "sample_rate": 500,
    },
    "NODE-ECG5": {
        "type": "ECG",
        "chip": "ADS1298",
        "lead": "aVL",
        "sample_rate": 500,
    },
    "NODE-ACCEL": {
        "type": "ACCEL",
        "chip": "MPU6050",
        "axes": ["X", "Y", "Z"],
        "sample_rate": 100,        # Hz — 3-axis accelerometer
    },
    "NODE-SPO2": {
        "type": "SPO2",
        "chip": "MAX30102",
        "sample_rate": 25,         # Hz — oksimetri pulse
    },
    "NODE-ENV": {
        "type": "ENV",
        "chip": "MULTI",
        "sensors": ["temp", "gsr", "resp"],
        "sample_rate": 10,         # Hz — suhu kulit, GSR, respirasi
    },
}

# =============================================================================
#  SECTION 4: PHYSIOLOGICAL RANGES PER PHASE
#  Rentang normal parameter fisiologis untuk setiap fase sesi.
#  Format: PHASE_PHYSIO[SessionPhase] = {param: (min, max)}
#  Digunakan oleh simulator untuk membangkitkan nilai sintetis yang realistis.
# =============================================================================

PHASE_PHYSIO = {
    SessionPhase.RESTING: {
        "heart_rate_bpm":       (55, 75),
        "spo2_pct":             (98, 100),
        "skin_temp_c":          (36.0, 37.0),
        "gsr_kohm":             (50, 80),
        "resp_rate_bpm":        (12, 16),
        "accel_magnitude_g":    (0.05, 0.15),
    },
    SessionPhase.WARMUP: {
        "heart_rate_bpm":       (85, 110),
        "spo2_pct":             (97, 99),
        "skin_temp_c":          (37.0, 38.0),
        "gsr_kohm":             (20, 50),
        "resp_rate_bpm":        (18, 24),
        "accel_magnitude_g":    (0.5, 0.8),
    },
    SessionPhase.SPRINT: {
        "heart_rate_bpm":       (160, 185),
        "spo2_pct":             (94, 97),
        "skin_temp_c":          (38.5, 40.0),
        "gsr_kohm":             (5, 20),
        "resp_rate_bpm":        (28, 40),
        "accel_magnitude_g":    (1.8, 2.5),
    },
    SessionPhase.COOLDOWN: {
        "heart_rate_bpm":       (120, 145),
        "spo2_pct":             (96, 98),
        "skin_temp_c":          (38.0, 39.0),
        "gsr_kohm":             (10, 30),
        "resp_rate_bpm":        (22, 28),
        "accel_magnitude_g":    (0.6, 1.0),
    },
    SessionPhase.RECOVERY: {
        "heart_rate_bpm":       (70, 90),
        "spo2_pct":             (97, 99),
        "skin_temp_c":          (36.5, 37.5),
        "gsr_kohm":             (30, 60),
        "resp_rate_bpm":        (14, 18),
        "accel_magnitude_g":    (0.1, 0.3),
    },
}

# =============================================================================
#  SECTION 5: ANOMALY THRESHOLDS
#  Batas deteksi anomali fisiologis dengan dua level severitas: WARNING dan
#  CRITICAL. Digunakan oleh modul anomaly detector untuk triase real-time.
#  Nilai None berarti tidak ada batas untuk arah tersebut.
# =============================================================================

ANOMALY_THRESHOLDS = {
    "heart_rate_bpm": {
        "WARNING":  {"above": 185, "below": None},
        "CRITICAL": {"above": 200, "below": None},
    },
    "spo2_pct": {
        "WARNING":  {"above": None, "below": 94},    # hipoksia ringan
        "CRITICAL": {"above": None, "below": 90},    # hipoksia berat
    },
    "skin_temp_c": {
        "WARNING":  {"above": 39.5, "below": None},  # hipertermia
        "CRITICAL": {"above": 40.5, "below": None},  # heat stroke risk
    },
    "gsr_kohm": {
        "WARNING":  {"above": None, "below": 3},     # stres ekstrem
        "CRITICAL": {"above": None, "below": None},  # tidak didefinisikan
    },
    "resp_rate_bpm": {
        "WARNING":  {"above": 40, "below": None},    # takipnea
        "CRITICAL": {"above": 50, "below": None},    # distres pernapasan
    },
}

# =============================================================================
#  SECTION 6: DISTRIBUTED QUEUE CONFIG
#  Konfigurasi antrian pesan antar proses pada arsitektur multi-node.
#  Pipeline: RAW_DATA_Q → PROCESSED_Q → FUSION_Q → LOG_Q
# =============================================================================

NUM_QUEUES     = 4
QUEUE_NAMES    = ["RAW_DATA_Q", "PROCESSED_Q", "FUSION_Q", "LOG_Q"]
QUEUE_MAX_SIZE = 100    # maksimum item per antrian sebelum backpressure
QUEUE_TIMEOUT_S = 1.0   # detik — timeout saat get/put blocking

# =============================================================================
#  SECTION 7: PARALLEL PROCESSING CONFIG
#  Parameter proses paralel menggunakan multiprocessing.
#  NUM_WORKER_PROCESSES sesuai jumlah sensor node (1 proses per node).
#  PROCESSING_DELAY_S: simulasi latensi pemrosesan per tipe node (range min-max).
# =============================================================================

NUM_WORKER_PROCESSES = 8   # 5 ECG + 1 ACCEL + 1 SPO2 + 1 ENV

PROCESSING_DELAY_S = {
    "ECG":   (0.08, 0.15),   # detik — analisis morfologi gelombang
    "ACCEL": (0.03, 0.06),   # detik — komputasi magnitude & stride
    "SPO2":  (0.05, 0.10),   # detik — algoritma SpO2 photoplethysmography
    "ENV":   (0.02, 0.04),   # detik — normalisasi multi-sensor env
}

# =============================================================================
#  SECTION 8: COMMUNICATION SIMULATION
#  Simulasi karakteristik komunikasi wireless BLE/WiFi pada wearable.
#  Latensi meningkat saat fase SPRINT karena interferensi RF dan gerakan.
# =============================================================================

BASE_LATENCY_MS = 15        # ms — latensi dasar BLE/WiFi wearable

PHASE_LATENCY_FACTOR = {
    SessionPhase.RESTING:  0,   # ms tambahan
    SessionPhase.WARMUP:   0,
    SessionPhase.SPRINT:   8,   # interferensi tinggi saat sprint
    SessionPhase.COOLDOWN: 3,   # penurunan interferensi bertahap
    SessionPhase.RECOVERY: 0,
}

PACKET_LOSS_RATE = 0.015    # 1.5% — probabilitas paket hilang
ACK_TIMEOUT_MS   = 500      # ms — timeout acknowledgement

# =============================================================================
#  SECTION 9: DEADLOCK CONFIG
#  Parameter deteksi dan pemulihan deadlock antar proses/node.
#  Backoff eksponensial untuk menghindari livelock saat recovery.
# =============================================================================

NODE_TIMEOUT_S        = 2.5   # detik — waktu tunggu sebelum dinyatakan deadlock
MAX_RECOVERY_ATTEMPTS = 3     # jumlah maksimum percobaan recovery
RECOVERY_BACKOFF_S    = 0.8   # detik — interval dasar backoff antar percobaan

# =============================================================================
#  SECTION 10: BENCHMARK CONFIG
#  Parameter untuk pengujian performa throughput dan latency sistem.
#  Setiap benchmark diulang BENCHMARK_REPEAT kali lalu diambil rata-rata.
# =============================================================================

BENCHMARK_TASK_COUNT = 80   # jumlah task per satu run benchmark
BENCHMARK_REPEAT     = 3    # jumlah run untuk rata-rata pengukuran

# =============================================================================
#  SECTION 11: DASHBOARD CONFIG
#  Konfigurasi tampilan dashboard real-time berbasis Tkinter/Matplotlib.
#  Color palette mengikuti tema GitHub Dark untuk kontras tinggi di lingkungan
#  lapangan (outdoor athlete monitoring).
# =============================================================================

# --- Layout ---
WINDOW_SIZE         = "1600x900"   # resolusi jendela utama
UPDATE_INTERVAL_MS  = 400          # ms — interval refresh UI
CHART_WINDOW        = 100          # jumlah data points yang ditampilkan di chart

# --- Warna Latar & Panel ---
COLOR_BG            = "#0d1117"    # background utama (GitHub Dark)
COLOR_PANEL         = "#161b22"    # background panel/card
COLOR_TEXT          = "#f0f6fc"    # teks utama

# --- Warna Status ---
COLOR_ACCENT        = "#58a6ff"    # highlight / aksen utama (biru)
COLOR_NORMAL        = "#3fb950"    # status normal (hijau)
COLOR_WARNING       = "#d29922"    # peringatan (kuning/amber)
COLOR_CRITICAL      = "#f85149"    # kritis / darurat (merah)

# --- Warna Sinyal per Tipe Node ---
COLOR_ECG           = "#58a6ff"    # ECG — biru
COLOR_ACCEL         = "#bc8cff"    # Accelerometer — ungu
COLOR_SPO2          = "#ff7b72"    # SpO2 — oranye-merah
COLOR_ENV           = "#3fb950"    # Environmental — hijau
