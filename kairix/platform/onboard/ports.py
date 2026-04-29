"""Port detection helpers for kairix onboarding and MCP server startup."""

from __future__ import annotations

import socket


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Check whether a TCP port is available (not listening).

    Uses a short connect timeout to avoid blocking on filtered ports.
    """
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return False
    except (OSError, TimeoutError):
        return True


def find_available_port(
    preferred: int = 8080,
    host: str = "127.0.0.1",
    scan_range: int = 100,
) -> int:
    """Find the first available port starting from *preferred*.

    Scans up to *scan_range* ports above *preferred*.
    Raises RuntimeError if none are available.
    """
    for port in range(preferred, preferred + scan_range):
        if is_port_available(port, host):
            return port
    raise RuntimeError(f"No available port found in range [{preferred}, {preferred + scan_range})")
