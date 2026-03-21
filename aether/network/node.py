import logging
import pickle
import socket
import threading
import time
from typing import Any, Dict, Optional, Tuple

from aether.utils.log import BoundLogger

logger = logging.getLogger(__name__)


class NodeAddress:
    def __init__(self, host: str, port: int):
        self.host = self._normalize_host(host)
        self.port = port

    @staticmethod
    def _normalize_host(host: str) -> str:
        """Convert hostnames like 'localhost' to their standard IP representation.
        This ensures consistent comparisons between NodeAddress instances.
        """
        try:
            return socket.gethostbyname(host)
        except socket.gaierror:
            return host

    def __eq__(self, other):
        if not isinstance(other, NodeAddress):
            return False
        return self.host == other.host and self.port == other.port

    def __hash__(self):
        return hash((self.host, self.port))

    def __repr__(self):
        return f"NodeAddress({self.host}:{self.port})"


class _IdentificationMessage:
    """Internal message type sent as the first message on any new TCP connection
    to identify who is connecting.
    """

    def __init__(self, sender: NodeAddress):
        self.sender = sender


class NetworkNode:
    """TCP-based network node with persistent, bidirectional connections."""

    def __init__(self, address: NodeAddress):
        self.address = address
        self.log = BoundLogger(logger, {"node": str(address)})

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.settimeout(1.0)

        try:
            self.server_socket.bind((address.host, address.port))
            self.server_socket.listen(10)
            self.log.debug("socket bound on %s:%d", address.host, address.port)
        except Exception:
            self.log.error(
                "failed to bind on %s:%d", address.host, address.port, exc_info=True
            )
            raise

        self._connections: Dict[NodeAddress, socket.socket] = {}
        self._connections_lock = threading.Lock()

        self._message_queue: list = []
        self._queue_lock = threading.Lock()
        self._queue_condition = threading.Condition(self._queue_lock)

        self._running = True

        self._server_thread = threading.Thread(
            target=self._accept_connections,
            name=f"tcp-server-{address.port}",
            daemon=True,
        )
        self._server_thread.start()

        time.sleep(0.1)

        self.log.info("TCP node listening on %s:%d", address.host, address.port)

    def _accept_connections(self) -> None:
        """Server thread that continuously accepts incoming TCP connections."""
        while self._running:
            try:
                try:
                    client_socket, client_address = self.server_socket.accept()
                    self.log.debug("accepted connection from %s", client_address)
                except socket.timeout:
                    continue
                except Exception:
                    if self._running:
                        self.log.error("accept error", exc_info=True)
                    continue

                handler = threading.Thread(
                    target=self._handle_connection,
                    args=(client_socket,),
                    name=f"tcp-handler-{self.address.port}-{client_address[1]}",
                    daemon=True,
                )
                handler.start()

            except Exception:
                if self._running:
                    self.log.error("error in accept loop", exc_info=True)

        self.log.debug("accept thread exiting")

    def _handle_connection(self, peer_socket: socket.socket) -> None:
        """Handle incoming connection."""
        peer_address: Optional[NodeAddress] = None

        try:
            peer_socket.settimeout(5.0)

            data = self._recv_full_message(peer_socket)
            if data is None:
                self.log.debug("no identification received, closing connection")
                peer_socket.close()
                return

            msg = pickle.loads(data)

            if not isinstance(msg, _IdentificationMessage):
                self.log.error(
                    "first message was not identification: %s", type(msg).__name__
                )
                peer_socket.close()
                return

            peer_address = msg.sender
            self.log.debug("identified peer as %s", peer_address)

            with self._connections_lock:
                if peer_address in self._connections:
                    self.log.debug(
                        "duplicate connection from %s, closing", peer_address
                    )
                    peer_socket.close()
                    return
                else:
                    self._connections[peer_address] = peer_socket

            while self._running:
                data = self._recv_full_message(peer_socket)
                if data is None:
                    break

                try:
                    msg = pickle.loads(data)

                    with self._queue_condition:
                        self._message_queue.append((msg, peer_address))
                        self._queue_condition.notify()

                    self.log.debug(
                        "queued %s from %s", type(msg).__name__, peer_address
                    )

                except Exception:
                    self.log.error("error unpickling message", exc_info=True)

        except Exception:
            self.log.error("connection handler error", exc_info=True)

        finally:
            if peer_address is not None:
                with self._connections_lock:
                    if (
                        peer_address in self._connections
                        and self._connections[peer_address] == peer_socket
                    ):
                        del self._connections[peer_address]
                        self.log.debug("removed connection to %s", peer_address)

            try:
                peer_socket.close()
            except Exception:
                pass

    def _recv_full_message(self, sock: socket.socket) -> Optional[bytes]:
        """Receive complete message with length prefix."""
        try:
            length_data = self._recv_exactly(sock, 4)
            if length_data is None:
                return None

            message_length = int.from_bytes(length_data, byteorder="big")
            return self._recv_exactly(sock, message_length)

        except Exception:
            self.log.debug("error receiving message", exc_info=True)
            return None

    def _recv_exactly(self, sock: socket.socket, num_bytes: int) -> Optional[bytes]:
        """Receive exactly num_bytes."""
        data = b""
        while len(data) < num_bytes:
            try:
                chunk = sock.recv(num_bytes - len(data))
                if not chunk:
                    return None
                data += chunk
            except socket.timeout:
                return None
            except Exception:
                return None
        return data

    def _send_full_message(self, sock: socket.socket, data: bytes) -> bool:
        """Send complete message with length prefix."""
        try:
            length_bytes = len(data).to_bytes(4, byteorder="big")
            sock.sendall(length_bytes)
            sock.sendall(data)
            return True
        except Exception:
            self.log.debug("error sending message", exc_info=True)
            return False

    def send(self, msg: Any, dest: NodeAddress) -> None:
        """Send message to destination, with up to 3 connection attempts."""
        if dest == self.address:
            return

        try:
            data = pickle.dumps(msg)
        except Exception:
            self.log.error("failed to pickle %s", type(msg).__name__, exc_info=True)
            return

        for attempt in range(3):
            peer_socket = None

            with self._connections_lock:
                if dest in self._connections:
                    peer_socket = self._connections[dest]

            if peer_socket is None:
                try:
                    self.log.debug(
                        "opening new connection to %s (attempt %d)", dest, attempt + 1
                    )
                    peer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    peer_socket.settimeout(5.0)
                    peer_socket.connect((dest.host, dest.port))

                    id_msg = _IdentificationMessage(self.address)
                    if not self._send_full_message(peer_socket, pickle.dumps(id_msg)):
                        peer_socket.close()
                        raise Exception("failed to send identification")

                    with self._connections_lock:
                        if dest not in self._connections:
                            self._connections[dest] = peer_socket

                            receiver = threading.Thread(
                                target=self._handle_outbound_connection,
                                args=(peer_socket, dest),
                                daemon=True,
                            )
                            receiver.start()
                        else:
                            peer_socket.close()
                            peer_socket = self._connections[dest]

                except Exception:
                    if attempt < 2:
                        self.log.debug(
                            "connection attempt %d to %s failed, retrying",
                            attempt + 1,
                            dest,
                        )
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    else:
                        self.log.error(
                            "failed to connect to %s after 3 attempts",
                            dest,
                            exc_info=True,
                        )
                        return

            if self._send_full_message(peer_socket, data):
                self.log.debug("sent %s to %s", type(msg).__name__, dest)
                return
            else:
                with self._connections_lock:
                    if (
                        dest in self._connections
                        and self._connections[dest] == peer_socket
                    ):
                        del self._connections[dest]

                if attempt < 2:
                    self.log.debug(
                        "send attempt %d to %s failed, retrying", attempt + 1, dest
                    )
                    time.sleep(0.5)
                else:
                    self.log.error("failed to send %s to %s", type(msg).__name__, dest)

    def _handle_outbound_connection(
        self, peer_socket: socket.socket, peer_address: NodeAddress
    ) -> None:
        """Handle receiving from an outbound connection."""
        try:
            while self._running:
                data = self._recv_full_message(peer_socket)
                if data is None:
                    break

                try:
                    msg = pickle.loads(data)

                    with self._queue_condition:
                        self._message_queue.append((msg, peer_address))
                        self._queue_condition.notify()

                    self.log.debug(
                        "queued %s from %s", type(msg).__name__, peer_address
                    )

                except Exception:
                    self.log.error("error unpickling message", exc_info=True)

        except Exception:
            self.log.error("outbound handler error", exc_info=True)

        finally:
            with self._connections_lock:
                if (
                    peer_address in self._connections
                    and self._connections[peer_address] == peer_socket
                ):
                    del self._connections[peer_address]

    def receive(
        self, timeout: float = 1.0
    ) -> Tuple[Optional[Any], Optional[NodeAddress]]:
        """Receive a message, blocking up to timeout seconds."""
        with self._queue_condition:
            if not self._message_queue:
                self._queue_condition.wait(timeout)

            if self._message_queue:
                msg, sender = self._message_queue.pop(0)
                return msg, sender
            else:
                return None, None

    def close(self) -> None:
        """Close all connections and shut down the server socket."""
        self._running = False

        with self._connections_lock:
            for peer_socket in list(self._connections.values()):
                try:
                    peer_socket.close()
                except Exception:
                    pass
            self._connections.clear()

        try:
            self.server_socket.close()
        except Exception:
            pass

        if self._server_thread:
            self._server_thread.join(timeout=2.0)

        self.log.info("closed")
