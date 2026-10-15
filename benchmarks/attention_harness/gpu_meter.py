"""GPU-time metering for an attempt.

``gpu_seconds`` is utilization-weighted: it integrates the host GPU utilization
(0-100 %) over the attempt's wall time, so a fully busy GPU for 10 s is 10
GPU-seconds and a half-busy one 5. The reading is the GPU's total load while the
attempt ran, not only this attempt's processes (NVML per-process utilization
needs privileges that a research workstation does not give), and the record says
so. Where no GPU reading exists the result is ``None`` ("not tracked"), never 0.
"""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Callable

COVERAGE = "host_gpu_utilization_during_attempt; includes other processes on the same GPU"


def nvidia_smi_utilization() -> float | None:
    """Summed utilization of all GPUs as a fraction of one GPU (e.g. 1.5 = 150 %)."""
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True).stdout
        values = [float(line) for line in output.split() if line.strip()]
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return sum(values) / 100.0 if values else None


class GpuMeter:
    def __init__(self, sampler: Callable[[], float | None] | None = None, *,
                 interval: float = 1.0, monotonic: Callable[[], float] = time.monotonic) -> None:
        self.sampler = sampler or nvidia_smi_utilization
        self.interval = interval
        self.monotonic = monotonic
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._samples: list[tuple[float, float]] = []
        self._started: float | None = None

    def _sample(self) -> None:
        try:
            value = self.sampler()
        except Exception:
            value = None
        if value is not None:
            self._samples.append((self.monotonic(), float(value)))

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self._sample()

    def start(self) -> "GpuMeter":
        self._started = self.monotonic()
        self._sample()
        self._thread = threading.Thread(target=self._run, daemon=True, name="gpu-meter")
        self._thread.start()
        return self

    def stop(self) -> dict[str, object]:
        """Finish metering. ``gpu_seconds`` is None when no reading was possible."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 6)
        self._sample()
        ended = self.monotonic()
        wall = max(0.0, ended - (self._started if self._started is not None else ended))
        if len(self._samples) < 2:
            seconds = None if not self._samples else self._samples[0][1] * wall
        else:
            seconds = 0.0
            for (t0, u0), (t1, u1) in zip(self._samples, self._samples[1:]):
                seconds += (u0 + u1) / 2.0 * max(0.0, t1 - t0)
        return {"gpu_seconds": None if seconds is None else round(seconds, 6),
                "wall_seconds": round(wall, 6), "samples": len(self._samples),
                "coverage": COVERAGE}
