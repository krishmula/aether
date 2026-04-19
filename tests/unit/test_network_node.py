"""Unit tests for NetworkNode socket read helpers."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock

from aether.network.node import NetworkNode


def _make_node() -> NetworkNode:
    node = NetworkNode.__new__(NetworkNode)
    node._running = True
    return node


class TestPersistentSocketReads:
    def test_recv_exactly_persistent_retries_after_timeout(self) -> None:
        node = _make_node()
        sock = MagicMock()
        sock.recv.side_effect = [
            socket.timeout(),
            b"ab",
            socket.timeout(),
            b"cd",
        ]

        data = node._recv_exactly_persistent(sock, 4)

        assert data == b"abcd"

    def test_recv_full_message_persistent_reads_length_prefixed_payload(self) -> None:
        node = _make_node()
        sock = MagicMock()
        sock.recv.side_effect = [
            socket.timeout(),
            b"\x00\x00",
            b"\x00\x05",
            socket.timeout(),
            b"he",
            b"llo",
        ]

        data = node._recv_full_message_persistent(sock)

        assert data == b"hello"
