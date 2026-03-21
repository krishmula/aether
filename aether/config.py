"""Configuration loader for distributed pub-sub system."""

import os
from dataclasses import dataclass
from typing import List, Optional

import yaml

from aether.network.node import NodeAddress


@dataclass
class BrokerConfig:
    id: int
    host: str
    port: int

    def to_address(self) -> NodeAddress:
        return NodeAddress(self.host, self.port)


@dataclass
class Config:
    # Bootstrap
    bootstrap_host: str
    bootstrap_port: int

    # Brokers
    brokers: List[BrokerConfig]

    # Subscribers
    subscriber_host: str
    subscriber_base_port: int
    subscribers_per_broker: int

    # Publishers
    publisher_host: str
    publisher_base_port: int
    publisher_count: int

    # Gossip settings
    fanout: int
    ttl: int
    heartbeat_interval: float
    heartbeat_timeout: float

    # Snapshot settings
    snapshot_interval: float

    # Logging settings
    log_level: str = "INFO"
    log_file: Optional[str] = None
    log_json_console: bool = False

    @property
    def bootstrap_address(self) -> NodeAddress:
        return NodeAddress(self.bootstrap_host, self.bootstrap_port)

    @property
    def broker_addresses(self) -> List[NodeAddress]:
        return [b.to_address() for b in self.brokers]

    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        with open(path, "r") as f:
            data = yaml.safe_load(f)

        brokers = [
            BrokerConfig(id=b["id"], host=b["host"], port=b["port"])
            for b in data["brokers"]
        ]

        return cls(
            bootstrap_host=data["bootstrap"]["host"],
            bootstrap_port=data["bootstrap"]["port"],
            brokers=brokers,
            subscriber_host=data["subscribers"]["local_host"],
            subscriber_base_port=data["subscribers"]["base_port"],
            subscribers_per_broker=data["subscribers"]["count_per_broker"],
            publisher_host=data["publishers"]["local_host"],
            publisher_base_port=data["publishers"]["base_port"],
            publisher_count=data["publishers"]["count"],
            fanout=data["gossip"]["fanout"],
            ttl=data["gossip"]["ttl"],
            heartbeat_interval=data["gossip"]["heartbeat_interval"],
            heartbeat_timeout=data["gossip"]["heartbeat_timeout"],
            snapshot_interval=data["snapshot"]["interval"],
            log_level=data.get("logging", {}).get("level", "INFO"),
            log_file=data.get("logging", {}).get("log_file"),
            log_json_console=data.get("logging", {}).get("json_console", False),
        )

    @classmethod
    def from_env_or_file(cls, default_path: str = "config.yaml") -> "Config":
        """Load config from AETHER_CONFIG env var or default path."""
        path = os.environ.get("AETHER_CONFIG", default_path)
        return cls.from_yaml(path)


# Global config instance (loaded lazily)
_config: Optional[Config] = None


def get_config(path: str = "config.yaml") -> Config:
    """Get or load the configuration."""
    global _config
    if _config is None:
        _config = Config.from_env_or_file(path)
    return _config


def reload_config(path: str = "config.yaml") -> Config:
    """Force reload the configuration."""
    global _config
    _config = Config.from_yaml(path)
    return _config
