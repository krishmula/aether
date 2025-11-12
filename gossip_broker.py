import random
import threading
import time
import uuid
from typing import Dict, List, Set

from bootstrap import BootstrapServer
from broker import Broker
from gossip_protocol import GossipMessage, Heartbeat, MembershipUpdate
from log_utils import log_debug, log_error, log_info, log_network, log_success
from message import Message
from network import NetworkNode, NodeAddress
from payload_range import PayloadRange
from subscriber import Subscriber


class GossipBroker:
    def __init__(self, address: NodeAddress, fanout: int = 3, ttl: int = 5) -> None:
        self.address = address
        self.network = NetworkNode(address)

        # re-use existing broker for local subscription management
        self._local_broker = Broker()

        # gossip parameters
        self.fanout = fanout
        self.ttl = ttl

        self.peer_brokers: Set[NodeAddress] = set()

        self.seen_messages: Set[str] = set()

        # threading control
        self.running = False
        self.recv_thread: threading.Thread = None
        self.heartbeat_thread: threading.Thread = None

    def register(self, subscriber: Subscriber, payload_range: PayloadRange) -> None:
        self._local_broker.register(subscriber, payload_range)

    def unregister(self, subscriber: Subscriber) -> None:
        self._local_broker.unregister(subscriber)

    def add_peer(self, peer: NodeAddress) -> None:
        if peer != self.address:
            # print(f"PEER BEFORE ADDING TO PEER_BROKERS of {self.address} IS: {peer}")
            # it's a set, so it handles duplicated by itself. we just don't add self.
            self.peer_brokers.add(peer)
            log_network(f"Broker:{self.address.port}", "PEER ADDED", f"{peer}")

    def start(self) -> None:
        # thread 1: receive messages from network on a loop
        self.running = True
        self.recv_thread = threading.Thread(
            target=self._receive_loop,
            name=f"broker-{self.address.port}-recv",
            daemon=True,
        )
        self.recv_thread.start()

        # thread 2: send heartbeats
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"broker-{self.address.port}-hb",
            daemon=True,
        )
        self.heartbeat_thread.start()

        log_success(
            f"Broker:{self.address.port}",
            f"Started with {len(self.peer_brokers)} peer(s)",
        )

    def stop(self) -> None:
        """
        stop all background threads and close network connections.
        """
        self.running = False
        if self.recv_thread:
            self.recv_thread.join(timeout=2.0)
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=2.0)
        self.network.close()

    def _handle_gossip_message(self, gossip_msg: GossipMessage) -> None:
        if gossip_msg.msg_id in self.seen_messages:
            log_info(
                "GossipMessage",
                f"Already seen this message, it's a duplicate. FROM BROKER {self.address}",
            )
            # we've already seen this message, it's a duplicate
            return

        self.seen_messages.add(gossip_msg.msg_id)

        # deliver to local subscribers
        self._local_broker.publish(gossip_msg.msg)

        if gossip_msg.ttl > 0:
            self._gossip_to_peers(gossip_msg)

    def _gossip_to_peers(self, gossip_msg: GossipMessage) -> None:
        """
        Gossip the message payload to it's peer brokers.
        """
        gossip_msg.ttl -= 1

        # no peers to gossip to
        if len(self.peer_brokers) == 0:
            return

        # print(f"PEER LIST OF BROKER {self.address} is: {self.peer_brokers}")

        # number of peers to gossip to
        num_targets = min(self.fanout, len(self.peer_brokers))
        targets = random.sample(list(self.peer_brokers), num_targets)

        # gossip to each peer in the targets array
        # and then, we can even remove the failed peers from our peer list (laterrrr..)
        for peer in targets:
            try:
                # log_info("PEER AND SELF", f"Peer is: {peer}, Self is: {self.address}")
                self.network.send(gossip_msg, peer)
            except Exception as e:
                log_error(
                    f"Broker:{self.address.port}", f"Failed to gossip to {peer}: {e}"
                )

    def _receive_loop(self) -> None:
        # message received can either be the actual payload gossiped, or a heartbeat
        while self.running:
            msg, sender = self.network.receive(timeout=1.0)
            if msg is None:
                continue

            try:
                if isinstance(msg, GossipMessage):
                    self._handle_gossip_message(msg)
                elif isinstance(msg, Heartbeat):
                    self.add_peer(sender)
                elif isinstance(msg, MembershipUpdate):
                    for broker_addr in msg.brokers:
                        self.add_peer(broker_addr)
                elif isinstance(msg, Message):
                    msg_id = str(uuid.uuid4())
                    gossip_msg = GossipMessage(
                        msg=msg, msg_id=msg_id, ttl=self.ttl, source=self.address
                    )
                    self._handle_gossip_message(gossip_msg)
                else:
                    log_error(
                        f"Broker:{self.address.port}",
                        f"Unknown message type from {sender}: {type(msg)}",
                    )

            except Exception as e:
                log_error(
                    f"Broker:{self.address.port}",
                    f"Error handling message from {sender}: {e}",
                )

    def _heartbeat_loop(self) -> None:
        sequence = 0
        while self.running:
            time.sleep(5.0)
            sequence += 1
            hb = Heartbeat(sender=self.address, sequence=sequence)
            for peer in self.peer_brokers:
                try:
                    self.network.send(hb, peer)
                except Exception as e:
                    pass

    def get_count(self, sub: Subscriber, payload) -> int:
        return self._local_broker.get_count(sub, payload)
