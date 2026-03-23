# Phase 2.6 — Orchestrator Docker Setup

## Goal

Move from Phase 1's static docker-compose (9 services hardcoded) to a two-service compose that only runs **bootstrap + orchestrator**. The orchestrator dynamically creates all other containers via the Docker SDK. `make demo` seeds the full topology through the API.

---

## Current State (Phase 1 compose — to be replaced)

`docker-compose.yml` defines 9 static services: bootstrap, broker-1/2/3, subscriber-0/1/2, publisher-0/1.
`Makefile` has hardcoded ports for all 9 and queries their `/status` directly.

---

## Target State

`docker-compose.yml` defines **2 services**: bootstrap + orchestrator.
`make demo` does:
1. `docker-compose up -d` — starts bootstrap + orchestrator
2. Polls orchestrator `/docs` until HTTP 200
3. `POST /api/seed` — orchestrator dynamically creates 3 brokers, 2 publishers, 3 subscribers
4. Prints API docs URL

---

## Files Changed

### `docker-compose.yml`

Strip to 2 services. Add `image: aether:latest` to both so the orchestrator can reference the same image when calling `docker.containers.run()`.

```yaml
# docker-compose.yml — Phase 2.6: bootstrap + orchestrator only
services:
  bootstrap:
    build: .
    image: aether:latest
    container_name: aether-bootstrap
    hostname: bootstrap
    command: >
      aether-bootstrap
        --host bootstrap --port 7000 --status-port 17000 --log-level INFO
    ports:
      - "7100:7000"
      - "17100:17000"
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:17000/status"]
      interval: 5s
      timeout: 3s
      retries: 5
      start_period: 5s
    networks:
      - aether-net
    restart: "no"

  orchestrator:
    build: .
    image: aether:latest
    container_name: aether-orchestrator
    hostname: orchestrator
    command: uvicorn aether.orchestrator.main:app --host 0.0.0.0 --port 9000
    ports:
      - "9000:9000"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    user: root
    environment:
      BOOTSTRAP_HOST: bootstrap
      BOOTSTRAP_PORT: "7000"
      AETHER_IMAGE: aether:latest
      DOCKER_NETWORK: aether-net
    depends_on:
      bootstrap:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:9000/docs"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 15s
    networks:
      - aether-net
    restart: "no"

networks:
  aether-net:
    driver: bridge
```

**Why `user: root`?** The Docker socket at `/var/run/docker.sock` requires root (or the `docker` group). The existing `Dockerfile` creates a non-root `aether` user, so the orchestrator service overrides this. Only the orchestrator needs root — bootstrap still uses the non-root user.

**Why `image: aether:latest` on both?** docker-compose builds the image once and tags it `aether:latest`. The orchestrator's `DockerManager` calls `client.containers.run(settings.aether_image, ...)` — pointing at this tag — to create dynamic containers. Same image, different command.

---

### `aether/orchestrator/settings.py`

Add defaults so the orchestrator doesn't crash when env vars are absent (e.g., in local dev):

```python
class OrchestratorSettings(BaseSettings):
    bootstrap_host: str = "bootstrap"
    bootstrap_port: int = 7000
    aether_image: str = "aether:latest"
    docker_network: str = "aether-net"
```

---

### `Makefile`

Three targeted changes:

**1. `ALL_PORTS` / port variables** — Remove static broker/pub/sub ports, add orchestrator.

```makefile
BOOTSTRAP_STATUS_PORT = 17100
ORCHESTRATOR_PORT     = 9000
ALL_PORTS = $(BOOTSTRAP_STATUS_PORT) $(ORCHESTRATOR_PORT)
```

**2. `demo` target** — Poll orchestrator until ready, then seed.

```makefile
demo: build check-ports
    @echo "Starting bootstrap + orchestrator..."
    @docker-compose up -d
    @echo "Waiting for orchestrator to be ready..."
    @until curl -sf http://localhost:9000/docs >/dev/null 2>&1; do \
        printf '.'; sleep 2; \
    done
    @echo ""
    @echo "Seeding demo topology (3 brokers, 2 publishers, 3 subscribers)..."
    @curl -sf -X POST http://localhost:9000/api/seed | python3 -m json.tool
    @echo ""
    @echo "Demo ready!"
    @echo "  API docs:     http://localhost:9000/docs"
    @echo "  System state: http://localhost:9000/api/state"
    @echo "  Live events:  ws://localhost:9000/ws/events"
```

**3. `status` target** — Query orchestrator `/api/state`.

```makefile
status:
    @echo "System state (via orchestrator):"
    @curl -sf http://localhost:9000/api/state | python3 -m json.tool
```

**4. `check-ports` target** — Only check 2 ports.

```makefile
check-ports:
    @echo "Checking required ports..."
    @failed=0; \
    for port in $(ALL_PORTS); do \
        if lsof -i :$$port >/dev/null 2>&1; then \
            echo "  ✗ Port $$port is in use"; failed=1; \
        else \
            echo "  ✓ Port $$port is available"; \
        fi; \
    done; \
    [ $$failed -eq 0 ] || exit 1
    @echo "All required ports available."
```

**5. `clean` target** — Add label-based cleanup for dynamic containers before `docker-compose down`.

```makefile
clean:
    @echo "Stopping dynamic aether containers..."
    -@docker ps -aq --filter "label=component_type" | xargs -r docker rm -f
    @echo "Stopping compose-managed services..."
    @docker-compose down -v --remove-orphans
```

The `-` prefix on the first command means make won't fail if there are no dynamic containers to remove. The `component_type` label is already set by `DockerManager` on all broker/publisher/subscriber containers.

---

## Build Order

When docker-compose encounters two services with `build: .` and the same `image: aether:latest`, it builds the image once. The tag is available immediately for both services and for dynamic containers the orchestrator spawns.

```
docker-compose build    →  builds Dockerfile, tags as aether:latest
docker-compose up -d    →  runs bootstrap using aether:latest
                        →  runs orchestrator using aether:latest
POST /api/seed          →  orchestrator calls docker.containers.run("aether:latest", ...)
                        →  3 brokers + 2 publishers + 3 subscribers start
```

---

## Verification

```bash
# Full demo flow
make demo

# Verify only 2 compose-managed services
docker-compose ps

# Verify all 10 containers (2 static + 8 dynamic)
docker ps --filter "name=aether"

# API exploration
curl http://localhost:9000/api/state | python3 -m json.tool
curl http://localhost:9000/api/state/topology | python3 -m json.tool
curl http://localhost:9000/api/metrics | python3 -m json.tool

# Dynamic scaling (add a broker on the fly)
curl -X POST http://localhost:9000/api/brokers
curl -X DELETE http://localhost:9000/api/brokers/4

# Full teardown (removes dynamic + compose containers)
make clean
docker ps --filter "name=aether"   # should be empty
```

---

## Known Limitation

The `DockerManager` maintains state in-memory (`self._components` dict). If the orchestrator restarts, it loses track of previously created containers. Those containers keep running but the orchestrator no longer manages them. This is a known limitation documented for Phase 5 (hardening). For now, `make clean` handles it via label-based cleanup.
