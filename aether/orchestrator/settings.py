"""Orchestrator configuration — all values sourced from environment variables."""

from pydantic_settings import BaseSettings


class OrchestratorSettings(BaseSettings):
    bootstrap_host: str = "bootstrap"
    bootstrap_port: int = 7000
    aether_image: str = "aether:latest"
    docker_network: str = "aether-net"
    orchestrator_url: str = "http://orchestrator:8001"

    # Recovery settings
    snapshot_max_age: float = 30.0
    health_poll_interval: float = 0.5
    health_timeout: float = 15.0
    recovery_timeout: float = 30.0
    recovery_debounce_window: float = 60.0


settings = OrchestratorSettings()
