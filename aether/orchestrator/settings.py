"""Orchestrator configuration — all values sourced from environment variables."""

from pydantic_settings import BaseSettings


class OrchestratorSettings(BaseSettings):
    bootstrap_host: str = "bootstrap"
    bootstrap_port: int = 7000
    aether_image: str = "aether:latest"
    docker_network: str = "aether-net"


settings = OrchestratorSettings()
