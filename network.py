import pickle
import socket
import threading
import time
import traceback
from typing import Any, Dict, Optional, Tuple

from log_utils import log_debug, log_error, log_info, log_network


class NodeAddress:
    def __init__(self, host: str, port: int):
        self.host = self._normalize_host(host)
        self.port = port

    @staticmethod
    def _normalize_host(host: str) -> str:
        """
        Convert hostnames like 'localhost' to their standard IP representation.
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
    """
    Internal message type sent as the first message on any new TCP connection
    to identify who is connecting.
    """

    def __init__(self, sender: NodeAddress):
        self.sender = sender


class NetworkNode:
    """
    TCP-based network node with persistent, bidirectional connections.
    """

    def __init__(self, address: NodeAddress):
        self.address = address

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self.server_socket.settimeout(1.0)

        try:
            self.server_socket.bind((address.host, address.port))
            self.server_socket.listen(10)
            log_debug(
                "NetworkNode",
                f"Socket bound and listening on {address.host}:{address.port}",
            )
        except Exception as e:
            log_error(
                "NetworkNode",
                f"Failed to bind/listen on {address.host}:{address.port}: {e}",
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

        log_info("NetworkNode", f"TCP node listening on {address.host}:{address.port}")

    def _accept_connections(self) -> None:
        """
        Server thread that continuously accepts incoming TCP connections.
        """

        while self._running:
            try:

                try:
                    client_socket, client_address = self.server_socket.accept()
                    log_debug(
                        f"NetworkNode:{self.address.port}",
                        f"*** ACCEPTED connection from {client_address} ***",
                    )
                except socket.timeout:
                    continue
                except Exception as e:
                    if self._running:
                        log_error(
                            f"NetworkNode:{self.address.port}", f"Accept error: {e}"
                        )
                    continue

                handler = threading.Thread(
                    target=self._handle_connection,
                    args=(client_socket,),
                    name=f"tcp-handler-{self.address.port}-{client_address[1]}",
                    daemon=True,
                )
                handler.start()

            except Exception as e:
                if self._running:
                    log_error(
                        f"NetworkNode:{self.address.port}",
                        f"Error in accept loop: {e}\n{traceback.format_exc()}",
                    )

        log_debug(f"NetworkNode:{self.address.port}", "=== Accept thread EXITING ===")

    def _handle_connection(self, peer_socket: socket.socket) -> None:
        """Handle incoming connection"""
        peer_address: Optional[NodeAddress] = None

        try:
            peer_socket.settimeout(5.0)

            data = self._recv_full_message(peer_socket)
            if data is None:
                log_debug(
                    f"NetworkNode:{self.address.port}", "No identification received"
                )
                peer_socket.close()
                return

            msg = pickle.loads(data)

            if not isinstance(msg, _IdentificationMessage):
                log_error(
                    f"NetworkNode:{self.address.port}",
                    f"First message was not identification: {type(msg)}",
                )
                peer_socket.close()
                return

            peer_address = msg.sender
            log_debug(
                f"NetworkNode:{self.address.port}", f"Identified peer as {peer_address}"
            )

            with self._connections_lock:
                if peer_address in self._connections:
                    log_debug(
                        f"NetworkNode:{self.address.port}",
                        f"Duplicate connection to {peer_address}, closing",
                    )
                    peer_socket.close()
                    return
                else:
                    self._connections[peer_address] = peer_socket
                    log_debug(
                        f"NetworkNode:{self.address.port}",
                        f"Stored connection to {peer_address}",
                    )

            while self._running:
                data = self._recv_full_message(peer_socket)
                if data is None:
                    break

                try:
                    msg = pickle.loads(data)

                    with self._queue_condition:
                        self._message_queue.append((msg, peer_address))
                        self._queue_condition.notify()

                    log_debug(
                        f"NetworkNode:{self.address.port}",
                        f"Queued {type(msg).__name__} from {peer_address}",
                    )

                except Exception as e:
                    log_error(
                        f"NetworkNode:{self.address.port}", f"Error unpickling: {e}"
                    )

        except Exception as e:
            log_error(
                f"NetworkNode:{self.address.port}", f"Connection handler error: {e}"
            )

        finally:
            if peer_address is not None:
                with self._connections_lock:
                    if (
                        peer_address in self._connections
                        and self._connections[peer_address] == peer_socket
                    ):
                        del self._connections[peer_address]
                        log_debug(
                            f"NetworkNode:{self.address.port}",
                            f"Removed connection to {peer_address}",
                        )

            try:
                peer_socket.close()
            except:
                pass

    def _recv_full_message(self, sock: socket.socket) -> Optional[bytes]:
        """Receive complete message with length prefix"""
        try:
            length_data = self._recv_exactly(sock, 4)
            if length_data is None:
                return None

            message_length = int.from_bytes(length_data, byteorder="big")

            message_data = self._recv_exactly(sock, message_length)
            return message_data

        except Exception as e:
            log_debug(
                f"NetworkNode:{self.address.port}", f"Error receiving message: {e}"
            )
            return None

    def _recv_exactly(self, sock: socket.socket, num_bytes: int) -> Optional[bytes]:
        """Receive exactly num_bytes"""
        data = b""
        while len(data) < num_bytes:
            try:
                chunk = sock.recv(num_bytes - len(data))
                if not chunk:
                    return None
                data += chunk
            except socket.timeout:
                log_debug(f"NetworkNode:{self.address.port}", "Receive timeout")
                return None
            except Exception as e:
                log_debug(f"NetworkNode:{self.address.port}", f"Receive error: {e}")
                return None
        return data

    def _send_full_message(self, sock: socket.socket, data: bytes) -> bool:
        """Send complete message with length prefix"""
        try:
            length = len(data)
            length_bytes = length.to_bytes(4, byteorder="big")
            sock.sendall(length_bytes)
            sock.sendall(data)
            return True
        except Exception as e:
            log_debug(f"NetworkNode:{self.address.port}", f"Error sending: {e}")
            return False

    def send(self, msg: Any, dest: NodeAddress) -> None:
        """Send message to destination with retries"""
        if dest == self.address:
            return

        try:
            data = pickle.dumps(msg)
        except Exception as e:
            log_error(f"NetworkNode:{self.address.port}", f"Failed to pickle: {e}")
            return

        for attempt in range(3):
            peer_socket = None

            with self._connections_lock:
                if dest in self._connections:
                    peer_socket = self._connections[dest]
                    log_debug(
                        f"NetworkNode:{self.address.port}",
                        f"Reusing connection to {dest}",
                    )

            if peer_socket is None:
                try:
                    log_debug(
                        f"NetworkNode:{self.address.port}",
                        f"Creating new connection to {dest}",
                    )
                    peer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    peer_socket.settimeout(5.0)
                    peer_socket.connect((dest.host, dest.port))

                    id_msg = _IdentificationMessage(self.address)
                    id_data = pickle.dumps(id_msg)

                    if not self._send_full_message(peer_socket, id_data):
                        peer_socket.close()
                        raise Exception("Failed to send identification")

                    log_debug(
                        f"NetworkNode:{self.address.port}", f"Connected to {dest}"
                    )

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

                except Exception as e:
                    if attempt < 2:
                        log_debug(
                            f"NetworkNode:{self.address.port}",
                            f"Connection attempt {attempt+1} failed, retrying: {e}",
                        )
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    else:
                        log_error(
                            f"NetworkNode:{self.address.port}",
                            f"Failed to connect to {dest}: {e}",
                        )
                        return

            if self._send_full_message(peer_socket, data):
                log_debug(
                    f"NetworkNode:{self.address.port}",
                    f"Sent {type(msg).__name__} to {dest}",
                )
                return
            else:
                with self._connections_lock:
                    if (
                        dest in self._connections
                        and self._connections[dest] == peer_socket
                    ):
                        del self._connections[dest]

                if attempt < 2:
                    log_debug(
                        f"NetworkNode:{self.address.port}",
                        f"Send attempt {attempt+1} failed, retrying",
                    )
                    time.sleep(0.5)
                else:
                    log_error(
                        f"NetworkNode:{self.address.port}", f"Failed to send to {dest}"
                    )

    def _handle_outbound_connection(
        self, peer_socket: socket.socket, peer_address: NodeAddress
    ) -> None:
        """Handle receiving from outbound connection"""
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

                    log_debug(
                        f"NetworkNode:{self.address.port}",
                        f"Queued {type(msg).__name__} from {peer_address}",
                    )

                except Exception as e:
                    log_error(
                        f"NetworkNode:{self.address.port}", f"Error unpickling: {e}"
                    )

        except Exception as e:
            log_error(
                f"NetworkNode:{self.address.port}", f"Outbound handler error: {e}"
            )

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
        """Receive a message"""
        with self._queue_condition:
            if not self._message_queue:
                self._queue_condition.wait(timeout)

            if self._message_queue:
                msg, sender = self._message_queue.pop(0)
                return msg, sender
            else:
                return None, None

    def close(self) -> None:
        """Close node"""
        self._running = False

        with self._connections_lock:
            for peer_socket in list(self._connections.values()):
                try:
                    peer_socket.close()
                except:
                    pass
            self._connections.clear()

        try:
            self.server_socket.close()
        except:
            pass

        if self._server_thread:
            self._server_thread.join(timeout=2.0)

        log_info("NetworkNode", f"Closed network node at {self.address}")
