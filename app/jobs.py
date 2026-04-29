from __future__ import annotations

import time
from concurrent.futures import Future
from threading import Lock
from typing import Any, Dict, Optional


class AnalysisJobStore:
    def __init__(self, ttl_seconds: int = 1800) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def put(
        self,
        job_id: str,
        *,
        classification: Dict[str, Any],
        future: Future,
        fallback_future: Optional[Future] = None,
        started_at: Optional[float] = None,
        fallback_timeout: Optional[float] = None,
    ) -> None:
        with self._lock:
            self._purge_expired_locked()
            self._jobs[job_id] = {
                "created_at": time.time(),
                "classification": dict(classification),
                "future": future,
                "fallback_future": fallback_future,
                "started_at": started_at,
                "fallback_timeout": fallback_timeout,
            }

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._purge_expired_locked()
            job = self._jobs.get(job_id)
            if not job:
                return None
            return dict(job)

    def _purge_expired_locked(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if float(job.get("created_at", 0)) < cutoff
        ]
        for job_id in expired:
            self._jobs.pop(job_id, None)
