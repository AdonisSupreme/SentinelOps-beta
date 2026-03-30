import asyncio
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass


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


def ping_once(address: str, timeout_ms: int) -> str:
    """
    Run a single ICMP ping using system ping.

    Windows: ping -n 1 -w <ms> <host>
    Linux/macOS: ping -c 1 -W <seconds> <host>
    """
    if os.name == "nt":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout_ms)), address]
    else:
        # -W is timeout in seconds on Linux; macOS differs but still works decently for 1 packet.
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout_ms / 1000))), address]

    result = subprocess.run(cmd, capture_output=True, text=True)
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
    start = time.time()
    try:
        # Use asyncio to avoid blocking under multi-target load.
        conn = asyncio.open_connection(address, port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout_ms / 1000)
        try:
            latency_ms = (time.time() - start) * 1000
            return TCPResult(up=True, latency_ms=int(round(latency_ms)))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
    except (asyncio.TimeoutError, OSError, socket.gaierror):
        return TCPResult(up=False, latency_ms=None)

