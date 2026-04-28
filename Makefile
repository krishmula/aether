# Makefile for aether distributed messaging system
# Targets documented in docs/instructions.md and docs/roadmap.md

.PHONY: demo status logs clean build build-benchmark build-dashboard test lint up down restart ps check-ports check-benchmark-ports purge-network benchmark benchmark-up benchmark-down benchmark-charts

# Colors for better output
GREEN = \033[0;32m
YELLOW = \033[1;33m
RED = \033[0;31m
BLUE = \033[0;34m
NC = \033[0m # No Color

# Phase 2.6: only bootstrap and orchestrator are compose-managed.
# All pub-sub components (brokers, publishers, subscribers) are created
# dynamically by the orchestrator via the Docker SDK.
BOOTSTRAP_STATUS_PORT = 17100
ORCHESTRATOR_PORT     = 9000
DASHBOARD_PORT        = 3000
ALL_PORTS = $(BOOTSTRAP_STATUS_PORT) $(ORCHESTRATOR_PORT) $(DASHBOARD_PORT)
BENCHMARK_PORTS = $(BOOTSTRAP_STATUS_PORT) $(ORCHESTRATOR_PORT)

# Default target
all: help

help:
	@echo "Aether Distributed Messaging System"
	@echo ""
	@echo "Available targets:"
	@echo "  $(YELLOW)make demo$(NC)        - Build, start bootstrap+orchestrator, seed 3+2+3 topology"
	@echo "  $(YELLOW)make status$(NC)      - Query system state via orchestrator API"
	@echo "  $(YELLOW)make logs$(NC)        - Tail all container logs"
	@echo "  $(YELLOW)make clean$(NC)       - Stop and remove all containers (compose + dynamic)"
	@echo "  $(YELLOW)make build$(NC)       - Build Docker images"
	@echo "  $(YELLOW)make test$(NC)        - Run unit tests"
	@echo "  $(YELLOW)make lint$(NC)        - Run lint checks"
	@echo "  $(YELLOW)make up$(NC)          - Start compose services in background"
	@echo "  $(YELLOW)make down$(NC)        - Stop compose services"
	@echo "  $(YELLOW)make restart$(NC)     - Restart compose services"
	@echo "  $(YELLOW)make ps$(NC)          - Show container status"
	@echo "  $(YELLOW)make benchmark$(NC)    - Run strict benchmark verification flow"
	@echo "  $(YELLOW)make benchmark-charts$(NC) - Regenerate charts from existing results"
	@echo "  $(YELLOW)make check-ports$(NC) - Check if required ports are available"
	@echo ""
	@echo "See docs/instructions.md for detailed usage"

demo: build check-ports purge-network
	@echo "$(YELLOW)Starting bootstrap + orchestrator...$(NC)"
	@docker-compose up -d
	@echo "$(YELLOW)Waiting for orchestrator to be ready$(NC)"
	@until curl -sf http://localhost:$(ORCHESTRATOR_PORT)/docs >/dev/null 2>&1; do \
		printf '$(YELLOW).$(NC)'; sleep 2; \
	done
	@echo ""
	@echo "$(YELLOW)Seeding demo topology (3 brokers, 2 publishers, 3 subscribers)...$(NC)"
	@curl -sf -X POST http://localhost:$(ORCHESTRATOR_PORT)/api/seed | python3 -m json.tool
	@echo ""
	@echo "$(GREEN)Demo ready!$(NC)"
	@echo "  $(BLUE)API docs:$(NC)      http://localhost:$(ORCHESTRATOR_PORT)/docs"
	@echo "  $(BLUE)System state:$(NC)  http://localhost:$(ORCHESTRATOR_PORT)/api/state"
	@echo "  $(BLUE)Live events:$(NC)   ws://localhost:$(ORCHESTRATOR_PORT)/ws/events"
	@echo "  $(BLUE)Dashboard:$(NC)     http://localhost:$(DASHBOARD_PORT)"

status:
	@echo "$(YELLOW)System state (via orchestrator):$(NC)"
	@curl -sf http://localhost:$(ORCHESTRATOR_PORT)/api/state | python3 -m json.tool

logs:
	@echo "$(YELLOW)Tailing logs from all containers...$(NC)"
	@echo "$(BLUE)Press Ctrl+C to stop log output$(NC)"
	@docker-compose logs -f

clean:
	@echo "$(YELLOW)Stopping dynamic aether containers (brokers, publishers, subscribers)...$(NC)"
	-@docker ps -aq --filter "label=component_type" | xargs -r docker rm -f
	@echo "$(YELLOW)Stopping compose-managed services (bootstrap + orchestrator)...$(NC)"
	@docker-compose down -v --remove-orphans
	$(MAKE) purge-network

purge-network:
	@if docker network inspect aether-net >/dev/null 2>&1; then \
		label=$$(docker network inspect aether-net --format '{{index .Labels "com.docker.compose.network"}}' 2>/dev/null); \
		if [ "$$label" != "aether-net" ]; then \
			echo "$(YELLOW)Removing stale aether-net network (missing compose labels)...$(NC)"; \
			docker network rm aether-net >/dev/null 2>&1 || true; \
		fi \
	fi

build:
	@echo "$(YELLOW)Building Docker images...$(NC)"
	@docker-compose build

build-benchmark:
	@echo "$(YELLOW)Building benchmark service images...$(NC)"
	@docker-compose build bootstrap orchestrator

build-dashboard:
	@echo "$(YELLOW)Building dashboard image...$(NC)"
	@docker-compose build dashboard

test:
	@echo "$(YELLOW)Running unit tests...$(NC)"
	pytest tests/unit/ --tb=short

lint:
	@echo "$(YELLOW)Running lint checks...$(NC)"
	ruff check .
	mypy aether/

# Helper targets
up:
	@echo "$(YELLOW)Starting compose services in background...$(NC)"
	@docker-compose up -d

down:
	@echo "$(YELLOW)Stopping compose services...$(NC)"
	@docker-compose down

restart:
	@echo "$(YELLOW)Restarting compose services...$(NC)"
	@docker-compose restart

ps:
	@echo "$(YELLOW)Container status:$(NC)"
	@docker-compose ps

check-ports:
	@echo "$(YELLOW)Checking if required ports are available...$(NC)"
	@failed=0; \
	for port in $(ALL_PORTS); do \
		if lsof -i :$$port >/dev/null 2>&1; then \
			echo "  $(RED)✗$(NC) Port $$port is already in use"; \
			failed=1; \
		else \
			echo "  $(GREEN)✓$(NC) Port $$port is available"; \
		fi; \
	done; \
	if [ $$failed -eq 1 ]; then \
		echo ""; \
		echo "$(RED)Error: Some ports are already in use.$(NC)"; \
		echo "Please stop the processes using these ports or modify docker-compose.yml"; \
		exit 1; \
	fi
	@echo "$(GREEN)All required ports are available.$(NC)"

check-benchmark-ports:
	@echo "$(YELLOW)Checking benchmark ports...$(NC)"
	@failed=0; \
	for port in $(BENCHMARK_PORTS); do \
		if lsof -i :$$port >/dev/null 2>&1; then \
			echo "  $(RED)✗$(NC) Port $$port is already in use"; \
			failed=1; \
		else \
			echo "  $(GREEN)✓$(NC) Port $$port is available"; \
		fi; \
	done; \
	if [ $$failed -eq 1 ]; then \
		echo ""; \
		echo "$(RED)Error: Benchmark ports are already in use.$(NC)"; \
		exit 1; \
	fi
	@echo "$(GREEN)Benchmark ports are available.$(NC)"

benchmark-up: build-benchmark check-benchmark-ports purge-network
	@echo "$(YELLOW)Starting benchmark-only services...$(NC)"
	@docker-compose up -d bootstrap orchestrator
	@echo "$(YELLOW)Waiting for orchestrator to be ready$(NC)"
	@until curl -sf http://localhost:$(ORCHESTRATOR_PORT)/docs >/dev/null 2>&1; do \
		printf '$(YELLOW).$(NC)'; sleep 2; \
	done
	@echo ""
	@echo "$(GREEN)Benchmark environment ready.$(NC)"

benchmark-down:
	@$(MAKE) clean

benchmark:
	@set -e; \
	trap 'rc=$$?; $(MAKE) benchmark-down; exit $$rc' EXIT; \
	$(MAKE) benchmark-down; \
	$(MAKE) benchmark-up; \
	echo "$(YELLOW)Running strict benchmark verification suite...$(NC)"; \
	python3 -m benchmarks.runner

benchmark-charts:
	@echo "$(YELLOW)Regenerating charts from existing results...$(NC)"
	python3 -m benchmarks.runner --charts-only

# Local development targets (without Docker)
install:
	@echo "$(YELLOW)Installing in development mode...$(NC)"
	pip install -e ".[dev]"

local-demo:
	@echo "$(YELLOW)Running local demo (single process mode)...$(NC)"
	aether-admin 4 --publish-interval 0.05 --duration 2 --seed 123

distributed-demo:
	@echo "$(YELLOW)Running distributed demo (all-in-one on localhost)...$(NC)"
	aether-distributed 3 2 2 --base-port 8000 --publish-interval 0.5 --duration 10 --seed 42
