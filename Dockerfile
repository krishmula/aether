# Dockerfile for pub-sub distributed messaging system
# Uses Python 3.13 as required by the project

FROM python:3.13-slim

LABEL maintainer="pubsub-system"
LABEL description="Distributed publish-subscribe system with gossip protocol and Chandy-Lamport snapshots"

# Set working directory
WORKDIR /app

# Install system dependencies (curl for health checks)
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy application code first
COPY pyproject.toml /app/
COPY pubsub/ /app/pubsub/
COPY config.docker.yaml /app/

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Create non-root user for security
RUN useradd -m -u 1000 pubsub && chown -R pubsub:pubsub /app
USER pubsub

# Default command (can be overridden by docker-compose)
CMD ["python", "-c", "print('Pub-Sub container ready. Use specific entry point like pubsub-broker, pubsub-bootstrap, etc.')"]

# Health check (uses the status port defined in each component)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${STATUS_PORT:-18000}/status || exit 1

# Expose ports (will be overridden by individual services)
EXPOSE 7000 8000 9000 10000 17000 18000 19000 20000