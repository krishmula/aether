import socket
import pickle
from log_utils import log_info, log_error, log_debug, log_network

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
    
class NetworkNode:
    """
    Network node that can send and receive messages over UDP.
    """
    def __init__(self, address: NodeAddress):
        self.address = address
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        ## we've created the socket, now bind it to the address
        self.socket.bind((address.host, address.port))
        log_info("NetworkNode", f"Listening on {address.host}:{address.port}")


    def send(self, msg: any, dest: NodeAddress) -> None:
        try:
            data = pickle.dumps(msg)
            self.socket.sendto(data, (dest.host, dest.port))  # sending the message over UDP
        except Exception as e:
            log_error("NetworkNode", f"Failed to send to {dest}: {e}")

    def receive(self, timeout: float = 1.0):
        self.socket.settimeout(timeout)
        try:
            data, (host, port) = self.socket.recvfrom(65536)
            msg = pickle.loads(data)

            sender = NodeAddress(host, port)
            return msg, sender
        except socket.timeout:
            return None, None
        except Exception as e:
            log_error("NetworkNode", f"Error receiving message: {e}")
            return None, None
        
    def close(self):
        self.socket.close()
        log_info("NetworkNode", f"Closed {self.address.host}:{self.address.port}")