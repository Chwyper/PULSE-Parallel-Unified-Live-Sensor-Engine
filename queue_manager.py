# =============================================================================
#  queue_manager.py — PULSE: Parallel Unified Live Sensor Engine
#  Distributed queue architecture berbasis multiprocessing.Queue.
# =============================================================================

import multiprocessing
import time
from multiprocessing import Queue, Value, Lock
from ctypes import c_uint64, c_double

import config
from config import QUEUE_NAMES, QUEUE_MAX_SIZE, QUEUE_TIMEOUT_S


class PulseQueueManager:
    
    def __init__(self) -> None:
        # --- Inisialisasi 4 queue pipeline ---
        self._queues: dict[str, Queue] = {
            name: Queue(maxsize=QUEUE_MAX_SIZE)
            for name in QUEUE_NAMES
        }

        # --- Shared counters (aman diakses antar proses) ---
        self._total_enqueued    = Value(c_uint64, 0)
        self._total_dequeued    = Value(c_uint64, 0)
        self._total_dropped     = Value(c_uint64, 0)
        self._total_packet_loss = Value(c_uint64, 0)

        # Untuk menghitung avg_queue_latency_ms
        self._latency_sum   = Value(c_double, 0.0)
        self._latency_count = Value(c_uint64, 0)

        # Lock tunggal untuk semua operasi counter
        self._counter_lock = Lock()

    # -------------------------------------------------------------------------
    #  enqueue
    # -------------------------------------------------------------------------

    def enqueue(self, queue_name: str, data: dict, source_node: str = "") -> bool:
        
        if queue_name not in self._queues:
            raise KeyError(f"Unknown queue: '{queue_name}'. Valid: {QUEUE_NAMES}")

        # Injeksi metadata
        item = {
            "_enqueue_time": time.perf_counter(),
            "_source":       source_node,
            **data,
        }

        q = self._queues[queue_name]
        try:
            q.put_nowait(item)          # non-blocking put
            with self._counter_lock:
                self._total_enqueued.value += 1
            return True
        except Exception:
            # Queue penuh — drop paket dan catat
            with self._counter_lock:
                self._total_dropped.value += 1
            return False

    # -------------------------------------------------------------------------
    #  dequeue
    # -------------------------------------------------------------------------

    def dequeue(self, queue_name: str) -> dict | None:
       
        if queue_name not in self._queues:
            raise KeyError(f"Unknown queue: '{queue_name}'. Valid: {QUEUE_NAMES}")

        q = self._queues[queue_name]
        try:
            item = q.get(timeout=QUEUE_TIMEOUT_S)
        except Exception:
            # Timeout — antrian kosong, bukan error
            return None

        # Hitung latensi dalam milidetik
        enqueue_time = item.get("_enqueue_time", time.perf_counter())
        latency_ms   = (time.perf_counter() - enqueue_time) * 1000.0
        item["queue_latency_ms"] = round(latency_ms, 3)

        with self._counter_lock:
            self._total_dequeued.value += 1
            self._latency_sum.value   += latency_ms
            self._latency_count.value += 1

        return item

    # -------------------------------------------------------------------------
    #  pipeline_transfer
    # -------------------------------------------------------------------------

    def pipeline_transfer(
        self,
        from_q: str,
        to_q: str,
        transform_fn=None,
    ) -> bool:
        
        item = self.dequeue(from_q)
        if item is None:
            return False      # from_q kosong saat timeout

        # Jalankan transformasi jika ada
        if transform_fn is not None:
            try:
                item = transform_fn(item)
                if item is None:
                    # Transform memilih untuk membuang item (filter)
                    return False
            except Exception as exc:
                # Log error ke LOG_Q lalu drop
                err_packet = {
                    "event_type":  "PIPELINE_ERROR",
                    "from_q":      from_q,
                    "to_q":        to_q,
                    "error":       str(exc),
                    "original_source": item.get("_source", "unknown"),
                }
                self.enqueue("LOG_Q", err_packet, source_node="pipeline_transfer")
                return False

        # Masukkan ke antrian tujuan
        success = self.enqueue(to_q, item, source_node=item.get("_source", ""))
        return success

    # -------------------------------------------------------------------------
    #  get_all_sizes
    # -------------------------------------------------------------------------

    def get_all_sizes(self) -> dict[str, int]:
       
        return {
            name: q.qsize()
            for name, q in self._queues.items()
        }

    # -------------------------------------------------------------------------
    #  get_stats
    # -------------------------------------------------------------------------

    def get_stats(self) -> dict:
        
        with self._counter_lock:
            enqueued    = self._total_enqueued.value
            dequeued    = self._total_dequeued.value
            dropped     = self._total_dropped.value
            pkt_loss    = self._total_packet_loss.value
            lat_sum     = self._latency_sum.value
            lat_count   = self._latency_count.value

        drop_rate   = (dropped / enqueued * 100.0) if enqueued > 0 else 0.0
        avg_latency = (lat_sum / lat_count) if lat_count > 0 else 0.0

        return {
            "total_enqueued":      int(enqueued),
            "total_dequeued":      int(dequeued),
            "total_dropped":       int(dropped),
            "total_packet_loss":   int(pkt_loss),
            "drop_rate_pct":       round(drop_rate, 3),
            "avg_queue_latency_ms": round(avg_latency, 4),
            "queue_sizes":         self.get_all_sizes(),
        }

    # -------------------------------------------------------------------------
    #  increment_packet_loss (helper untuk sensor_nodes)
    # -------------------------------------------------------------------------

    def increment_packet_loss(self, count: int = 1) -> None:
        
        with self._counter_lock:
            self._total_packet_loss.value += count

    # -------------------------------------------------------------------------
    #  __repr__
    # -------------------------------------------------------------------------

    def __repr__(self) -> str:
        sizes = self.get_all_sizes()
        sizes_str = ", ".join(f"{k}={v}" for k, v in sizes.items())
        stats = self.get_stats()
        return (
            f"PulseQueueManager("
            f"enqueued={stats['total_enqueued']}, "
            f"dequeued={stats['total_dequeued']}, "
            f"dropped={stats['total_dropped']}, "
            f"drop_rate={stats['drop_rate_pct']:.2f}%, "
            f"sizes=[{sizes_str}])"
        )
