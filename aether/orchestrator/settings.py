"""Orchestrator configuration — all values sourced from environment variables."""

from typing import Optional

from pydantic_settings import BaseSettings


class OrchestratorSettings(BaseSettings):
    bootstrap_host: str = "bootstrap"
    bootstrap_port: int = 7000
    aether_image: str = "aether:latest"
    docker_network: str = "aether-net"
    orchestrator_url: str = "http://orchestrator:8001"

    # Logging
    log_level: str = "DEBUG"
    # Base URL of the OTel Collector OTLP/HTTP endpoint. When set, the
    # orchestrator ships its own structured logs to the collector in addition
    # to stdout. Set via OTEL_ENDPOINT env var in docker-compose.yml.
    # Dynamic containers (brokers/publishers/subscribers) pick this up from
    # config.docker.yaml instead — no per-container injection needed.
    otel_endpoint: Optional[str] = None

    # Recovery settings
    snapshot_max_age: float = 45.0
    health_poll_interval: float = 0.5
    # HealthMonitor (background broker /status polling). Under load, brokers
    # may respond slowly; keep thresholds conservative to avoid false failover.
    health_broker_poll_interval: float = 2.0
    health_broker_failure_threshold: int = 8
    health_broker_request_timeout: float = 5.0
    health_broker_startup_grace_seconds: float = 45.0
    health_timeout: float = 15.0
    status_fetch_timeout: float = 5.0
    snapshot_monitor_poll_interval: float = 2.0
    recovery_timeout: float = 30.0
    recovery_debounce_window: float = 60.0
    component_stop_timeout: int = 1


settings = OrchestratorSettings()
