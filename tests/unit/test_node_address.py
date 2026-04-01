"""Unit tests for NodeAddress hostname-based identity model.

Key design decision: NodeAddress stores the host string as-is, without DNS
resolution. This ensures stable identity across Docker container restarts where
the same hostname ('broker-2') may resolve to a different IP each time.
"""

import pytest

from aether.network.node import NodeAddress


class TestHostStorage:
    def test_hostname_stored_as_is(self):
        addr = NodeAddress("broker-2", 8000)
        assert addr.host == "broker-2"

    def test_ip_string_stored_as_is(self):
        addr = NodeAddress("127.0.0.1", 8000)
        assert addr.host == "127.0.0.1"

    def test_localhost_stored_as_is(self):
        # Previously, 'localhost' was resolved to '127.0.0.1' via gethostbyname.
        # Now it is stored as the literal string 'localhost'.
        addr = NodeAddress("localhost", 8000)
        assert addr.host == "localhost"

    def test_port_stored(self):
        addr = NodeAddress("broker-1", 9001)
        assert addr.port == 9001


class TestEquality:
    def test_same_hostname_same_port_equal(self):
        assert NodeAddress("broker-2", 8000) == NodeAddress("broker-2", 8000)

    def test_different_hostname_not_equal(self):
        assert NodeAddress("broker-1", 8000) != NodeAddress("broker-2", 8000)

    def test_same_hostname_different_port_not_equal(self):
        assert NodeAddress("broker-1", 8000) != NodeAddress("broker-1", 8001)

    def test_ip_strings_equal(self):
        assert NodeAddress("127.0.0.1", 8000) == NodeAddress("127.0.0.1", 8000)

    def test_localhost_not_equal_to_ip(self):
        # Explicit documentation of the behavior change from the old DNS-resolving code.
        # Previously these were equal (both normalized to '127.0.0.1').
        # Now they are distinct identities — use one string consistently per deployment.
        assert NodeAddress("localhost", 8000) != NodeAddress("127.0.0.1", 8000)

    def test_not_equal_to_non_nodeaddress(self):
        assert NodeAddress("broker-1", 8000) != "broker-1:8000"
        assert NodeAddress("broker-1", 8000) != ("broker-1", 8000)
        assert NodeAddress("broker-1", 8000) != None  # noqa: E711


class TestHashing:
    def test_same_address_same_hash(self):
        assert hash(NodeAddress("broker-2", 8000)) == hash(NodeAddress("broker-2", 8000))

    def test_different_address_different_hash(self):
        # Not guaranteed by the contract, but holds for our (host, port) hash.
        assert hash(NodeAddress("broker-1", 8000)) != hash(NodeAddress("broker-2", 8000))

    def test_usable_as_set_key(self):
        s = {NodeAddress("broker-1", 8000), NodeAddress("broker-2", 8000)}
        assert len(s) == 2
        assert NodeAddress("broker-1", 8000) in s
        assert NodeAddress("broker-2", 8000) in s

    def test_set_deduplication(self):
        s = {NodeAddress("broker-1", 8000), NodeAddress("broker-1", 8000)}
        assert len(s) == 1

    def test_usable_as_dict_key(self):
        d = {NodeAddress("broker-1", 8000): "value"}
        assert d[NodeAddress("broker-1", 8000)] == "value"


class TestRepr:
    def test_repr_format(self):
        assert repr(NodeAddress("broker-2", 8000)) == "NodeAddress(broker-2:8000)"

    def test_repr_with_ip(self):
        assert repr(NodeAddress("127.0.0.1", 9001)) == "NodeAddress(127.0.0.1:9001)"
