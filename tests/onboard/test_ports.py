"""Tests for kairix.platform.onboard.ports — port detection helpers."""

from __future__ import annotations

import socket

import pytest

from kairix.platform.onboard.ports import find_available_port, is_port_available

pytestmark = pytest.mark.unit


class TestIsPortAvailable:
    """is_port_available detects whether a TCP port is in use."""

    def test_unused_port_returns_true(self) -> None:
        """A port with nothing listening should return True."""
        # Use a high ephemeral port unlikely to be in use
        assert is_port_available(59123) is True

    def test_bound_port_returns_false(self) -> None:
        """A port with something listening should return False."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            assert is_port_available(port) is False
        finally:
            sock.close()


class TestFindAvailablePort:
    """find_available_port returns the first open port in range."""

    def test_returns_preferred_when_free(self) -> None:
        """When the preferred port is free, return it."""
        # Use a high port unlikely to conflict
        port = find_available_port(preferred=59200)
        assert port == 59200

    def test_skips_bound_port(self) -> None:
        """When preferred port is in use, return next available."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 59300))
        sock.listen(1)
        try:
            port = find_available_port(preferred=59300)
            assert port > 59300
        finally:
            sock.close()

    def test_raises_when_no_ports_available(self) -> None:
        """When all ports in range are taken, raise RuntimeError."""
        socks = []
        try:
            for p in range(59400, 59403):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", p))
                s.listen(1)
                socks.append(s)
            with pytest.raises(RuntimeError, match="No available port"):
                find_available_port(preferred=59400, scan_range=3)
        finally:
            for s in socks:
                s.close()
