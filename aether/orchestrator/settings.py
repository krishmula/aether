"""Orchestrator configuration — all values sourced from environment variables."""

from pydantic_settings import BaseSettings


class OrchestratorSettings(BaseSettings):
    bootstrap_host: str
    bootstrap_port: int
    aether_image: str
    docker_network: str


settings = OrchestratorSettings()
