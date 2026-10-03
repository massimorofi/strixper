"""On-demand Strix Halo setup compliance audit.

Each check runs a shell command (or reads a file), compares the result with the
recommendation from the tuning guide, and reports PASS / WARN / FAIL with the
measured impact. Commands run non-blocking via asyncio subprocesses.
"""

from __future__ import annotations

import asyncio
import glob
import re
from typing import Any, Optional

BROKEN_FIRMWARE = "20251125"
MIN_KERNEL = (6, 16, 9)
MIN_GTT_BYTES = 120 * 1024**3  # ~124 GB target, 120 GB floor
EXPECTED_TTM_PAGES = 32505856  # ~124 GB pinned-memory ceiling


async def _run(command: str, timeout: float = 10.0) -> tuple[int, str, str]:
    """Run a shell command without blocking the event loop."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode or 0,
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
        )
    except asyncio.TimeoutError:
        return 124, "", f"command timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001 -- report, never crash the audit
        return 127, "", str(exc)


def _result(
    check_id: str,
    name: str,
    command: str,
    expected: str,
    actual: str,
    status: str,
    impact: str,
) -> dict[str, str]:
    return {
        "id": check_id,
        "name": name,
        "command": command,
        "expected": expected,
        "actual": actual,
        "status": status,
        "impact": impact,
    }


# -- Individual checks --------------------------------------------------------


async def check_kernel_version() -> dict[str, str]:
    command = "uname -r"
    code, out, _ = await _run(command)
    actual = out or "unknown"
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", actual)
    ok = bool(match) and tuple(int(x) for x in match.groups()) >= MIN_KERNEL
    return _result(
        "kernel_version",
        "Kernel Version Verification",
        command,
        ">= 6.16.9 (Mainline 6.18.x recommended)",
        actual,
        "PASS" if ok else "FAIL",
        "Required for >64GB unified memory support. Kernel 6.17 broke the "
        "ROCm KFD ABI -- use 6.18.x mainline.",
    )


async def check_iommu_status() -> dict[str, str]:
    command = "cat /proc/cmdline"
    code, out, _ = await _run(command)
    actual = out or "unknown"
    ok = "amd_iommu=off" in actual
    return _result(
        "iommu_status",
        "AMD IOMMU Status",
        command,
        "amd_iommu=off",
        actual,
        "PASS" if ok else "FAIL",
        "+5-12% memory read improvement for LLM decode. Note: disables the "
        "XDNA 2 NPU and DMA-attack protection. `amd_iommu=pt` does NOT work "
        "on this platform.",
    )


async def check_boot_params() -> dict[str, str]:
    command = "cat /proc/cmdline"
    code, out, _ = await _run(command)
    actual = out or "unknown"
    has_gttsize = re.search(r"amdgpu\.gttsize=(\d+)", actual)
    has_ttm = re.search(r"ttm\.pages_limit=(\d+)", actual)
    if has_gttsize and has_ttm:
        status = "PASS"
    elif has_gttsize or has_ttm:
        status = "WARN"
    else:
        status = "FAIL"
    return _result(
        "boot_params",
        "GTT / TTM Kernel Boot Parameters",
        command,
        f"amdgpu.gttsize=126976 ttm.pages_limit={EXPECTED_TTM_PAGES}",
        actual,
        status,
        "Exposes ~124 GB of host memory to the GPU as the unified compute "
        "pool (GTT). Without it, kernels cap visible memory near 15 GB.",
    )


async def check_gtt_size() -> dict[str, str]:
    command = "cat /sys/class/drm/card*/device/mem_info_gtt_total"
    total = 0
    for path in sorted(glob.glob("/sys/class/drm/card*/device/mem_info_gtt_total")):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                total = int(fh.read().strip())
            if total:
                break
        except (OSError, ValueError):
            continue
    if total >= MIN_GTT_BYTES:
        status = "PASS"
    elif total >= 64 * 1024**3:
        status = "WARN"
    else:
        status = "FAIL"
    return _result(
        "gtt_size",
        "GTT Allocation Size (sysfs)",
        command,
        "~124 GB (>= 120 GiB)",
        f"{total} bytes ({total / 1024**3:.1f} GiB)" if total else "unavailable",
        status,
        "The GPU's addressable compute pool. Verify the kernel params took "
        "effect after reboot.",
    )


async def check_firmware() -> dict[str, str]:
    command = "rpm -qa | grep linux-firmware || dpkg -l | grep linux-firmware"
    code, out, _ = await _run(command)
    # Show just the base package version, not the whole package list.
    version = "not found"
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in ("ii", "rc", "hi") and parts[1] == "linux-firmware":
            version = parts[2]
            break
    if version == "not found" and out:
        version = out.splitlines()[0][:80]
    if BROKEN_FIRMWARE in out:
        status = "FAIL"
    elif version == "not found":
        status = "WARN"
    else:
        status = "PASS"
    return _result(
        "firmware_check",
        "Linux Firmware Version",
        command,
        f"Not equal to {BROKEN_FIRMWARE} (20251111 validated)",
        version,
        status,
        f"Firmware {BROKEN_FIRMWARE} critically breaks ROCm on Strix Halo "
        "(instability, crashes). Downgrade to 20251111 and rebuild initramfs "
        "(dracut -f targeting the booted kernel).",
    )


async def check_tuned_profile() -> dict[str, str]:
    command = "tuned-adm active"
    code, out, _ = await _run(command)
    actual = out or "unavailable"
    daemon_down = "not running" in actual.lower()
    if code != 0:
        status = "WARN"
        actual = f"{actual or 'tuned-adm failed'} (exit {code})"
    elif daemon_down:
        status = "WARN"
    elif "accelerator-performance" in actual:
        status = "PASS"
    else:
        status = "WARN"
    return _result(
        "tuned_profile",
        "Active TuneD Profile",
        command,
        "accelerator-performance",
        actual,
        status,
        "Disables high-latency CPU C-states: +5-8% prompt prefill speed. "
        "Apply with `sudo tuned-adm profile accelerator-performance`.",
    )


async def check_udev_rules() -> dict[str, str]:
    command = "ls -l /dev/kfd /dev/dri/renderD* 2>/dev/null"
    code, out, _ = await _run(command)
    actual = out or "no devices found"
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        status = "FAIL"
    else:
        # Non-root access = the render group carries read+write
        # (perms[4:7] in the crw-rw---- style string).
        ok = True
        for line in lines:
            perms = line.split()[0] if line.split() else ""
            group_perms = perms[4:7] if len(perms) >= 7 else ""
            if "r" not in group_perms or "w" not in group_perms:
                ok = False
        status = "PASS" if ok else "WARN"
    return _result(
        "udev_rules",
        "GPU Device Access (udev)",
        command,
        "render group rw on /dev/kfd and /dev/dri/renderD*",
        actual,
        status,
        "Missing renderD rules are the #1 cause of "
        "HSA_STATUS_ERROR_OUT_OF_RESOURCES. Install 99-amd-kfd.rules and add "
        "the user to the video,render groups.",
    )


async def check_rocm_smi() -> dict[str, str]:
    command = "rocm-smi --showuse --json"
    code, out, err = await _run(command)
    if code == 127 or "not found" in (err or "").lower():
        status = "FAIL"
        actual = "rocm-smi is not installed (not found on PATH)"
    elif code != 0:
        status = "FAIL"
        actual = f"rocm-smi failed (exit {code}): {(err or out or '')[:120]}"
    elif not out or ("GPU" not in out and "card" not in out):
        status = "WARN"
        actual = f"rocm-smi ran but returned no GPU data: {(out or 'empty')[:120]}"
    else:
        status = "PASS"
        vcode, vout, _ = await _run("rocm-smi --version")
        version = vout.splitlines()[0].strip() if vout and vcode == 0 else "version unknown"
        actual = f"operational ({version})"
    return _result(
        "rocm_smi",
        "ROCm SMI (rocm-smi) Installed",
        command,
        "rocm-smi installed and returning GPU data",
        actual,
        status,
        "rocm-smi provides live GPU utilization and VRAM usage to the "
        "dashboard. Without it, GPU metrics fall back to sysfs only (no "
        "compute utilization, and VRAM reads lower than rocm-smi reports).",
    )


CHECKS = [
    check_kernel_version,
    check_iommu_status,
    check_boot_params,
    check_gtt_size,
    check_rocm_smi,
    check_firmware,
    check_tuned_profile,
    check_udev_rules,
]


async def run_all_checks() -> dict[str, Any]:
    """Run every compliance check concurrently."""
    from datetime import datetime, timezone

    results = await asyncio.gather(*(check() for check in CHECKS), return_exceptions=True)
    checks: list[dict[str, str]] = []
    for check, res in zip(CHECKS, results):
        if isinstance(res, BaseException):
            checks.append(
                _result(
                    getattr(check, "__name__", "unknown"),
                    check.__name__.replace("check_", "").replace("_", " ").title(),
                    "",
                    "",
                    f"check crashed: {res}",
                    "FAIL",
                    "The check itself failed to execute.",
                )
            )
        else:
            checks.append(res)

    return {
        "evaluated_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "checks": checks,
    }


# -- System summary banner ------------------------------------------------------


async def get_system_info() -> dict[str, Any]:
    """Hardware baseline for the Tab 2 summary banner."""
    cpu_model = "AMD Zen 5"
    threads = 0
    raw = ""
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        pass
    for line in raw.splitlines():
        if line.startswith("model name") and ":" in line:
            cpu_model = line.split(":", 1)[1].strip()
            break
    threads = len(re.findall(r"^processor\s*:", raw, re.MULTILINE)) or (
        __import__("os").cpu_count() or 0
    )

    gpu_name = "AMD Radeon 8060S (gfx1151)"
    code, out, _ = await _run("lspci -d 1002: 2>/dev/null | head -n 1")
    if code == 0 and out:
        gpu_name = out.split(":", 2)[-1].strip() or gpu_name

    return {
        "cpu": f"{cpu_model} ({threads // 2 if threads else '?'} Cores / {threads} Threads)",
        "gpu": gpu_name,
        "bandwidth": "~208 GB/s sustained (256-bit LPDDR5x-8000)",
    }
