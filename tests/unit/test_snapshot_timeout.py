import socket
import time
import unittest

from aether.gossip.broker import GossipBroker
from aether.snapshot import SnapshotMarker
from aether.network.node import NodeAddress


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestSnapshotTimeout(unittest.TestCase):
    def _make_broker(self) -> GossipBroker:
        return GossipBroker(NodeAddress("127.0.0.1", _free_port()))

    def test_timeout_noop_when_idle(self) -> None:
        broker = self._make_broker()
        try:
            broker._check_snapshot_timeout()
            self.assertIsNone(broker._snapshot_in_progress)
        finally:
            broker.network.close()

    def test_timeout_noop_before_threshold(self) -> None:
        broker = self._make_broker()
        try:
            with broker._lock:
                broker._snapshot_in_progress = "snap-1"
                broker._snapshot_started_at = time.time()
                broker._snapshot_timeout = 30.0
                broker._channels_recording = {NodeAddress("127.0.0.1", 7001): []}
                broker._channels_closed = set()
            broker._check_snapshot_timeout()
            self.assertEqual(broker._snapshot_in_progress, "snap-1")
        finally:
            broker.network.close()

    def test_timeout_clears_stuck_snapshot_state(self) -> None:
        broker = self._make_broker()
        try:
            with broker._lock:
                broker._snapshot_in_progress = "snap-2"
                broker._snapshot_started_at = time.time() - 31.0
                broker._snapshot_timeout = 30.0
                broker._channels_recording = {
                    NodeAddress("127.0.0.1", 7001): [],
                    NodeAddress("127.0.0.1", 7002): [],
                }
                broker._channels_closed = {NodeAddress("127.0.0.1", 7001)}
            broker._check_snapshot_timeout()
            self.assertIsNone(broker._snapshot_in_progress)
            self.assertIsNone(broker._snapshot_recorded_state)
            self.assertIsNone(broker._snapshot_started_at)
            self.assertEqual(broker._channels_recording, {})
            self.assertEqual(broker._channels_closed, set())
        finally:
            broker.network.close()

    def test_first_marker_sets_snapshot_started_at(self) -> None:
        broker = self._make_broker()
        try:
            sender = NodeAddress("127.0.0.1", 7101)
            other_peer = NodeAddress("127.0.0.1", 7102)
            broker.add_peer(sender)
            broker.add_peer(other_peer)

            marker = SnapshotMarker(
                snapshot_id="snap-marker-1",
                initiator_address=sender,
                timestamp=time.time(),
            )

            broker._handle_snapshot_marker(marker, sender)

            with broker._lock:
                self.assertEqual(broker._snapshot_in_progress, "snap-marker-1")
                self.assertIsNotNone(broker._snapshot_started_at)
        finally:
            broker.network.close()

    def test_latest_local_snapshot_set_on_completion(self) -> None:
        broker = self._make_broker()
        try:
            import time
            from aether.snapshot import BrokerSnapshot

            snap = BrokerSnapshot(
                snapshot_id="snap-xyz",
                broker_address=broker.address,
                peer_brokers=set(),
                remote_subscribers={},
                seen_message_ids=set(),
                timestamp=time.time(),
            )
            with broker._lock:
                broker._snapshot_in_progress = "snap-xyz"
                broker._snapshot_recorded_state = snap
                broker._channels_recording = {}
                broker._channels_closed = set()

            broker._check_snapshot_complete()

            with broker._lock:
                self.assertIsNotNone(broker._latest_local_snapshot)
                self.assertEqual(broker._latest_local_snapshot.snapshot_id, "snap-xyz")
                self.assertIsNone(broker._snapshot_in_progress)
        finally:
            broker.network.close()


if __name__ == "__main__":
    unittest.main()
