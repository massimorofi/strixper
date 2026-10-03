"""GPU telemetry via the `rocm-smi` CLI.

rocm-smi is the authoritative source for AMD GPU metrics. It reports higher
VRAM usage than the raw amdgpu sysfs files (it accounts for driver-reserved
allocations) and exposes GPU compute utilization, which sysfs does not.

The command is invoked as a short-lived subprocess on each poll. Any failure
(missing binary, timeout, unparsable output) is reported as
``{"available": False, "error": ...}`` so the dashboard degrades gracefully
rather than breaking.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

GIB = 1024**3


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class RocmMonitor:
    """Reads GPU utilization and VRAM usage from `rocm-smi`."""

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout

    async def snapshot(self) -> dict[str, Any]:
        """Return GPU utilization + VRAM usage, or an unavailable marker."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "rocm-smi",
                "--showuse",
                "--showmeminfo",
                "vram",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
        except FileNotFoundError:
            return {"available": False, "error": "rocm-smi not found on PATH"}
        except asyncio.TimeoutError:
            return {
                "available": False,
                "error": f"rocm-smi timed out after {self.timeout}s",
            }
        except Exception as exc:  # noqa: BLE001 -- never break the poll loop
            return {"available": False, "error": str(exc)}

        if proc.returncode != 0:
            return {
                "available": False,
                "error": f"rocm-smi exited {proc.returncode}: "
                f"{stderr.decode(errors='replace').strip()[:200]}",
            }

        try:
            data = json.loads(stdout.decode(errors="replace"))
        except (json.JSONDecodeError, ValueError) as exc:
            return {"available": False, "error": f"could not parse rocm-smi JSON: {exc}"}

        card = self._first_card(data)
        if card is None:
            return {"available": False, "error": "no GPU card reported by rocm-smi"}

        gpu_util = _to_float(card.get("GPU use (%)"))
        vram_total = _to_float(card.get("VRAM Total Memory (B)"))
        vram_used = _to_float(card.get("VRAM Total Used Memory (B)"))

        vram_pct = round(vram_used / vram_total * 100, 2) if vram_total else 0.0

        return {
            "available": True,
            "gpu_util_pct": gpu_util if gpu_util is not None else 0.0,
            "vram_used_gb": round(vram_used / GIB, 2) if vram_used else 0.0,
            "vram_total_gb": round(vram_total / GIB, 2) if vram_total else 0.0,
            "vram_usage_pct": vram_pct,
        }

    @staticmethod
    def _first_card(data: Any) -> Optional[dict]:
        """Return the first `cardN` entry from the rocm-smi JSON payload."""
        if not isinstance(data, dict):
            return None
        for key in sorted(data.keys()):
            if key.startswith("card") and isinstance(data[key], dict):
                return data[key]
        return None
