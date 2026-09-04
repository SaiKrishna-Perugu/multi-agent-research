"""
Lightweight in-process metrics: counters behind a lock, no external
dependency. Resets on restart, not aggregated across multiple instances --
fine for a single Cloud Run instance / portfolio demo, documented as a real
limitation (see README) rather than presented as production-grade.
"""

import threading
from collections import deque

_lock = threading.Lock()
_request_count = 0
_error_count = 0
_latencies_ms = deque(maxlen=1000)  # capped so this never grows unbounded
_reports_started = 0
_reports_finalized = 0
_revisions_requested = 0


def record_request(latency_ms: float, error: bool = False) -> None:
    global _request_count, _error_count
    with _lock:
        _request_count += 1
        if error:
            _error_count += 1
        _latencies_ms.append(latency_ms)


def record_report_started() -> None:
    global _reports_started
    with _lock:
        _reports_started += 1


def record_report_finalized() -> None:
    global _reports_finalized
    with _lock:
        _reports_finalized += 1


def record_revision_requested() -> None:
    global _revisions_requested
    with _lock:
        _revisions_requested += 1


def _percentile(sorted_values: list, pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(int(len(sorted_values) * pct), len(sorted_values) - 1)
    return sorted_values[idx]


def get_metrics_snapshot() -> dict:
    with _lock:
        latencies = sorted(_latencies_ms)
        return {
            "request_count": _request_count,
            "error_count": _error_count,
            "error_rate": round(_error_count / _request_count, 4)
            if _request_count
            else 0.0,
            "latency_ms_p50": _percentile(latencies, 0.50),
            "latency_ms_p95": _percentile(latencies, 0.95),
            "latency_ms_p99": _percentile(latencies, 0.99),
            "reports_started": _reports_started,
            "reports_finalized": _reports_finalized,
            "revisions_requested": _revisions_requested,
        }
