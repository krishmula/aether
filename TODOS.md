# TODOS

## Broker Failover (Phase 1)

### RecoveryManager: retry-with-backoff for POST /recover

**What:** Add retry-with-backoff when POST /recover to a replacement broker fails on the first attempt.

**Why:** The replacement container starts but the broker process takes 2-5 seconds to initialize. The first POST /recover gets connection refused. Without retry, Path A (replacement from fresh snapshot) silently fails and subscribers never reconnect.

**Pros:** Fixes a silent failure mode in the critical recovery path. Path A becomes reliable, not probabilistic.

**Cons:** Adds ~10 lines to RecoveryManager. Needs a test for the retry behavior.

**Context:** RecoveryManager._replacement_path() calls POST /recover on the newly-started replacement broker container. The broker is started via DockerManager.create_broker(), but the broker process (aether-broker entrypoint) takes a few seconds to initialize its TCP + HTTP servers. The first HTTP call will get connection refused. Solution: poll the broker's GET /status endpoint first (already done by HealthMonitor), wait until it returns 200, THEN send POST /recover. This reuses the same httpx pattern and avoids a separate sleep.

**Depends on:** HealthMonitor (B in parallelization plan) must be wired first.

**Priority:** P1 (blocks Path A reliability, demo looks broken without it)

## Completed

