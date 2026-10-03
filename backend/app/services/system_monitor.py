"""Local host telemetry for the Strix Halo box.

GTT / VRAM come from the amdgpu sysfs interface; RAM and CPU from procfs.
All reads are cheap filesystem reads, safe to call from the event loop.
"""

from __future__ import annotations

import glob
import os
from typing import Optional

GIB = 1024**3
MIB = 1024**2


def _read_file(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


class SystemMonitor:
    """Reads GPU (GTT/VRAM), system RAM and CPU utilisation."""

    def __init__(self) -> None:
        self._prev_cpu: Optional[tuple[int, int]] = None  # (total, busy) jiffies

    # -- GPU (amdgpu sysfs) --------------------------------------------------

    @staticmethod
    def _read_int_first(filename: str) -> Optional[int]:
        """Read the first matching amdgpu sysfs file, as an int."""
        for path in sorted(glob.glob(f"/sys/class/drm/card*/device/{filename}")):
            raw = _read_file(path)
            if raw is None:
                continue
            try:
                return int(raw.strip())
            except ValueError:
                continue
        return None

    def read_gpu(self) -> dict:
        total_bytes = self._read_int_first("mem_info_gtt_total")
        used_bytes = self._read_int_first("mem_info_gtt_used")
        vram_total = self._read_int_first("mem_info_vram_total")
        vram_used = self._read_int_first("mem_info_vram_used")

        gtt_total_gb = round(total_bytes / GIB, 2) if total_bytes else 0.0
        gtt_used_gb = round(used_bytes / GIB, 2) if used_bytes else 0.0
        gtt_pct = (
            round(used_bytes / total_bytes * 100, 2)
            if total_bytes and used_bytes is not None
            else 0.0
        )

        return {
            "gtt_total_gb": gtt_total_gb,
            "gtt_used_gb": gtt_used_gb,
            "gtt_usage_pct": gtt_pct,
            "vram_total_mb": round(vram_total / MIB, 1) if vram_total else 0.0,
            "vram_used_mb": round(vram_used / MIB, 1) if vram_used else 0.0,
        }

    # -- RAM (procfs) ---------------------------------------------------------

    @staticmethod
    def read_memory() -> dict:
        raw = _read_file("/proc/meminfo") or ""
        info: dict[str, int] = {}
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                try:
                    info[parts[0].rstrip(":")] = int(parts[1])  # values are kB
                except ValueError:
                    continue
        total_kb = info.get("MemTotal", 0)
        avail_kb = info.get("MemAvailable", info.get("MemFree", 0))
        used_kb = max(total_kb - avail_kb, 0)
        total_gb = round(total_kb / 1024 / 1024, 2)
        used_gb = round(used_kb / 1024 / 1024, 2)
        pct = round(used_kb / total_kb * 100, 2) if total_kb else 0.0
        return {"ram_total_gb": total_gb, "ram_used_gb": used_gb, "ram_usage_pct": pct}

    # -- CPU (procfs) ---------------------------------------------------------

    def read_cpu(self) -> dict:
        raw = _read_file("/proc/stat") or ""
        fields: list[int] = []
        for line in raw.splitlines():
            if line.startswith("cpu "):
                try:
                    fields = [int(x) for x in line.split()[1:]]
                except ValueError:
                    fields = []
                break

        usage = 0.0
        if len(fields) >= 4:
            total = sum(fields)
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
            busy = total - idle
            if self._prev_cpu is not None and total > self._prev_cpu[0]:
                d_total = total - self._prev_cpu[0]
                d_busy = busy - self._prev_cpu[1]
                usage = round(max(d_busy / d_total, 0.0) * 100, 2)
            self._prev_cpu = (total, busy)

        return {"usage_pct": usage, "active_threads": os.cpu_count() or 0}

    # -- Combined -------------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "gpu": self.read_gpu(),
            "memory": self.read_memory(),
            "cpu": self.read_cpu(),
        }
