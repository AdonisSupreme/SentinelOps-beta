import asyncio
import math
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import settings


@dataclass(frozen=True)
class ICMPResult:
    up: bool
    bytes_val: int | None
    latency_ms: int | None
    ttl: int | None
    raw: str


@dataclass(frozen=True)
class TCPResult:
    up: bool
    latency_ms: int | None


_WIN_BYTES_RE = re.compile(r"bytes=(\d+)", re.IGNORECASE)
_WIN_LAT_RE = re.compile(r"time[=<]\s?(\d+)\s*ms", re.IGNORECASE)
_WIN_TTL_RE = re.compile(r"TTL=(\d+)", re.IGNORECASE)

_NIX_BYTES_RE = re.compile(r"(\d+)\s+bytes", re.IGNORECASE)
_NIX_LAT_RE = re.compile(r"time[=<]\s?([\d.]+)\s*ms", re.IGNORECASE)
_NIX_TTL_RE = re.compile(r"ttl=(\d+)", re.IGNORECASE)


@lru_cache(maxsize=1)
def resolve_ping_executable() -> str:
    override = (settings.NETWORK_SENTINEL_PING_EXECUTABLE or "").strip()
    candidates: list[str] = []
    if override:
        candidates.append(override)

    detected = shutil.which("ping")
    if detected:
        candidates.append(detected)

    if os.name == "nt":
        candidates.append("ping")
    else:
        candidates.extend(["/usr/bin/ping", "/bin/ping", "/usr/sbin/ping", "/sbin/ping"])

    for candidate in candidates:
        if not candidate:
            continue
        if candidate == "ping":
            return candidate
        if Path(candidate).exists():
            return candidate

    return "ping"


def get_ping_runtime_details() -> dict[str, str]:
    return {
        "platform": platform.system() or os.name,
        "ping_executable": resolve_ping_executable(),
    }


def _build_ping_command(address: str, timeout_ms: int) -> list[str]:
    ping_executable = resolve_ping_executable()
    timeout_ms = max(250, int(timeout_ms))

    if os.name == "nt":
        return [ping_executable, "-n", "1", "-w", str(timeout_ms), address]

    timeout_seconds = max(1, int(math.ceil(timeout_ms / 1000)))
    if platform.system().lower() == "darwin":
        return [ping_executable, "-c", "1", "-W", str(timeout_ms), address]

    return [ping_executable, "-c", "1", "-W", str(timeout_seconds), address]


def ping_once(address: str, timeout_ms: int) -> str:
    """
    Run a single ICMP ping using system ping.

    Windows: ping -n 1 -w <ms> <host>
    Linux/macOS: ping -c 1 -W <seconds> <host>
    """
    cmd = _build_ping_command(address, timeout_ms)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(2.0, (int(timeout_ms) / 1000) + 1.0),
            check=False,
        )
    except FileNotFoundError:
        return f"PING_EXECUTABLE_NOT_FOUND | executable={cmd[0]}"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        return f"{stdout}{stderr}PING_TIMEOUT | executable={cmd[0]}"

    return (result.stdout or "") + (result.stderr or "")


def parse_ping(output: str) -> ICMPResult:
    """
    Parse ping output from Windows or *nix into basic metrics.
    """
    up = ("Reply from" in output) or ("bytes from" in output.lower())

    if os.name == "nt":
        bytes_match = _WIN_BYTES_RE.search(output)
        latency_match = _WIN_LAT_RE.search(output)
        ttl_match = _WIN_TTL_RE.search(output)

        bytes_val = int(bytes_match.group(1)) if bytes_match else None
        latency_ms = int(latency_match.group(1)) if latency_match else None
        ttl = int(ttl_match.group(1)) if ttl_match else None
    else:
        bytes_match = _NIX_BYTES_RE.search(output)
        latency_match = _NIX_LAT_RE.search(output)
        ttl_match = _NIX_TTL_RE.search(output)

        bytes_val = int(bytes_match.group(1)) if bytes_match else None
        latency_ms = int(float(latency_match.group(1))) if latency_match else None
        ttl = int(ttl_match.group(1)) if ttl_match else None

    if not up:
        return ICMPResult(up=False, bytes_val=None, latency_ms=None, ttl=None, raw=output)

    return ICMPResult(up=True, bytes_val=bytes_val, latency_ms=latency_ms, ttl=ttl, raw=output)


async def check_tcp(address: str, port: int, timeout_ms: int) -> TCPResult:
    start = time.perf_counter()
    try:
        # Use asyncio to avoid blocking under multi-target load.
        conn = asyncio.open_connection(address, port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout_ms / 1000)
        try:
            latency_ms = (time.perf_counter() - start) * 1000
            return TCPResult(up=True, latency_ms=int(round(latency_ms)))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
    except (asyncio.TimeoutError, OSError, socket.gaierror):
        return TCPResult(up=False, latency_ms=None)

