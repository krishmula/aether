# Makefile for aether distributed messaging system
# Targets documented in docs/instructions.md and docs/roadmap.md

.PHONY: demo status logs clean build test lint up down restart ps check-ports

# Colors for better output
GREEN = \033[0;32m
YELLOW = \033[1;33m
RED = \033[0;31m
BLUE = \033[0;34m
NC = \033[0m # No Color

# Component status ports (from docker-compose.yml mapping: host -> container)
# Container ports are status ports, host ports are mapped differently
BOOTSTRAP_PORT = 17100
BROKER_PORTS = 18101 18102 18103
SUBSCRIBER_PORTS = 20100 20101 20102
PUBLISHER_PORTS = 19100 19101
ALL_PORTS = $(BOOTSTRAP_PORT) $(BROKER_PORTS) $(SUBSCRIBER_PORTS) $(PUBLISHER_PORTS)

# Component names for display
BOOTSTRAP_NAME = bootstrap
BROKER_NAMES = broker-1 broker-2 broker-3
SUBSCRIBER_NAMES = subscriber-0 subscriber-1 subscriber-2
PUBLISHER_NAMES = publisher-0 publisher-1
ALL_COMPONENTS = $(BOOTSTRAP_NAME) $(BROKER_NAMES) $(SUBSCRIBER_NAMES) $(PUBLISHER_NAMES)

# Default target
all: help

help:
	@echo "Aether Distributed Messaging System"
	@echo ""
	@echo "Available targets:"
	@echo "  $(YELLOW)make demo$(NC)     - Build, start, wait 20s, query all status endpoints"
	@echo "  $(YELLOW)make status$(NC)   - Query /status from every running component"
	@echo "  $(YELLOW)make logs$(NC)     - Tail all container logs"
	@echo "  $(YELLOW)make clean$(NC)    - Stop and remove all containers, networks, and volumes"
	@echo "  $(YELLOW)make build$(NC)    - Build Docker images"
	@echo "  $(YELLOW)make test$(NC)     - Run unit tests"
	@echo "  $(YELLOW)make lint$(NC)     - Run lint checks"
	@echo "  $(YELLOW)make up$(NC)       - Start containers in background"
	@echo "  $(YELLOW)make down$(NC)     - Stop containers"
	@echo "  $(YELLOW)make restart$(NC)  - Restart containers"
	@echo "  $(YELLOW)make ps$(NC)       - Show container status"
	@echo "  $(YELLOW)make check-ports$(NC) - Check if required ports are available"
	@echo ""
	@echo "See docs/instructions.md for detailed usage"

demo: build check-ports
	@echo "$(YELLOW)Building and starting the full distributed aether system...$(NC)"
	@echo "$(BLUE)This will start:$(NC)"
	@echo "  - 1 bootstrap server"
	@echo "  - 3 brokers"
	@echo "  - 3 subscribers"
	@echo "  - 2 publishers"
	@echo ""
	@docker-compose up --build -d
	@echo "$(YELLOW)Waiting 20 seconds for system stabilization...$(NC)"
	@sleep 20
	@$(MAKE) status

status:
	@echo "$(YELLOW)Checking status of all 9 components:$(NC)"
	@echo ""
	@# Check bootstrap
	@echo "$(BLUE)Bootstrap:$(NC)"
	@if curl -s -f http://localhost:$(BOOTSTRAP_PORT)/status >/dev/null 2>&1; then \
		echo "  $(GREEN)✓$(NC) bootstrap (port $(BOOTSTRAP_PORT)) is online"; \
	else \
		echo "  $(RED)✗$(NC) bootstrap (port $(BOOTSTRAP_PORT)) is offline"; \
	fi
	@echo ""
	
	@# Check brokers
	@echo "$(BLUE)Brokers:$(NC)"
	@i=1; for port in $(BROKER_PORTS); do \
		if curl -s -f http://localhost:$$port/status >/dev/null 2>&1; then \
			echo "  $(GREEN)✓$(NC) broker-$$i (port $$port) is online"; \
		else \
			echo "  $(RED)✗$(NC) broker-$$i (port $$port) is offline"; \
		fi; \
		i=$$((i + 1)); \
	done
	@echo ""
	
	@# Check subscribers
	@echo "$(BLUE)Subscribers:$(NC)"
	@i=0; for port in $(SUBSCRIBER_PORTS); do \
		if curl -s -f http://localhost:$$port/status >/dev/null 2>&1; then \
			echo "  $(GREEN)✓$(NC) subscriber-$$i (port $$port) is online"; \
		else \
			echo "  $(RED)✗$(NC) subscriber-$$i (port $$port) is offline"; \
		fi; \
		i=$$((i + 1)); \
	done
	@echo ""
	
	@# Check publishers
	@echo "$(BLUE)Publishers:$(NC)"
	@i=0; for port in $(PUBLISHER_PORTS); do \
		if curl -s -f http://localhost:$$port/status >/dev/null 2>&1; then \
			echo "  $(GREEN)✓$(NC) publisher-$$i (port $$port) is online"; \
		else \
			echo "  $(RED)✗$(NC) publisher-$$i (port $$port) is offline"; \
		fi; \
		i=$$((i + 1)); \
	done
	@echo ""
	@echo "$(YELLOW)Status check complete.$(NC)"

logs:
	@echo "$(YELLOW)Tailing logs from all containers...$(NC)"
	@echo "$(BLUE)Press Ctrl+C to stop log output$(NC)"
	@docker-compose logs -f

clean:
	@echo "$(YELLOW)Stopping and removing all containers, networks, and volumes...$(NC)"
	@docker-compose down -v --remove-orphans

build:
	@echo "$(YELLOW)Building Docker images...$(NC)"
	@docker-compose build

test:
	@echo "$(YELLOW)Running unit tests...$(NC)"
	pytest tests/unit/ --tb=short

lint:
	@echo "$(YELLOW)Running lint checks...$(NC)"
	ruff check .
	mypy aether/

# Helper targets
up:
	@echo "$(YELLOW)Starting containers in background...$(NC)"
	@docker-compose up -d

down:
	@echo "$(YELLOW)Stopping containers...$(NC)"
	@docker-compose down

restart:
	@echo "$(YELLOW)Restarting containers...$(NC)"
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