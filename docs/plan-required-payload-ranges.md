# Plan: Make Subscriber Payload Ranges Required

## Context

`CreateSubscriberRequest` has optional `range_low`/`range_high` fields. When omitted:
1. The orchestrator stores `null` in `ComponentInfo` → `GET /api/state` shows `range_low: null, range_high: null`
2. The subscriber container silently auto-computes its range via `partition_payload_space(total_subscribers)` in `run_subscribers.py`
3. The orchestrator has no visibility into what range was actually assigned

Beyond the visibility gap, `partition_payload_space(total_subscribers)` is broken in a dynamic system — `total_subscribers` is a moving target, so adding subscribers one at a time produces overlapping or gapped payload coverage.

**Decision:** Make `range_low` and `range_high` required. The orchestrator always passes them explicitly. Range assignment is the API consumer's responsibility. The auto-partition fallback stays in `run_subscribers.py` for standalone CLI use only.

---

## Step 1: `aether/orchestrator/models.py` — Make ranges required

**Lines 88–92** — Remove `Optional` and `None` defaults:

```python
# Before
class CreateSubscriberRequest(BaseModel):
    subscriber_id: int | None = None
    broker_id: int  # required — subscriber needs exactly one broker
    range_low: int | None = Field(default=None, ge=0, le=255)
    range_high: int | None = Field(default=None, ge=0, le=255)

# After
class CreateSubscriberRequest(BaseModel):
    subscriber_id: int | None = None
    broker_id: int  # required — subscriber needs exactly one broker
    range_low: int = Field(ge=0, le=255)   # required — caller owns range assignment
    range_high: int = Field(ge=0, le=255)  # required — caller owns range assignment
```

`ComponentInfo.range_low`/`range_high` (lines 71–72) stay as `int | None = None` since they're shared across all component types and only apply to subscribers.

---

## Step 2: `aether/orchestrator/docker_manager.py` — Always pass ranges, remove debug prints

**Lines 192–200** — Remove conditional `range_args` and the 3 debug `print()` statements:

```python
# Before (lines 192-200)
range_args = (
    f"--range-low {req.range_low} --range-high {req.range_high}"
    if req.range_low is not None and req.range_high is not None
    else ""
)

print("req.broker_id", req.broker_id)
print("req.broker's range_low", req.range_low)
print("req.broker's range_high", req.range_high)

# After
range_args = f"--range-low {req.range_low} --range-high {req.range_high}"
```

No other changes needed in this file — `ComponentInfo` construction (lines 232–234) already assigns `req.range_low` and `req.range_high` directly.

---

## Step 3: `aether/orchestrator/main.py` — Fix `/api/seed` to pass explicit ranges

**Lines 211–218** — The seed creates 3 subscribers without ranges. Import `partition_payload_space` and compute ranges for the 3 demo subscribers:

Add import at top of file:
```python
from aether.core.payload_range import partition_payload_space
from aether.core.uint8 import UInt8
```

Update seed subscriber creation (lines 211–218):
```python
# Before
subscribers_needed = 3 - len(state.subscribers)
for broker_id in broker_ids[: max(0, subscribers_needed)]:
    info = docker_mgr.create_subscriber(
        CreateSubscriberRequest(broker_id=broker_id)
    )

# After
subscribers_needed = 3 - len(state.subscribers)
seed_ranges = partition_payload_space(UInt8(3))
for i, broker_id in enumerate(broker_ids[: max(0, subscribers_needed)]):
    r = seed_ranges[i]
    info = docker_mgr.create_subscriber(
        CreateSubscriberRequest(
            broker_id=broker_id,
            range_low=int(r.low),
            range_high=int(r.high),
        )
    )
```

This gives the demo subscribers `[0-84]`, `[85-169]`, `[170-255]`.

---

## Files Modified

| File | Change |
|------|--------|
| `aether/orchestrator/models.py:88-92` | `range_low`/`range_high` → required fields |
| `aether/orchestrator/docker_manager.py:192-200` | Remove conditional range_args + remove 3 print statements |
| `aether/orchestrator/main.py:1-24,211-218` | Add imports, pass explicit ranges in seed |

---

## Verification

1. **API validation:** `POST /api/subscribers` with body `{"broker_id": 1}` (no ranges) → should return **422 Validation Error**
2. **API happy path:** `POST /api/subscribers` with `{"broker_id": 1, "range_low": 0, "range_high": 127}` → should succeed, `ComponentInfo` has non-null ranges
3. **Demo seed:** `make demo` → `GET /api/state` → all 3 subscribers show `range_low`/`range_high` values (0-84, 85-169, 170-255)
4. **Standalone CLI:** `aether-subscriber --subscriber-id 0 --host localhost --port 9100 --broker-host localhost --broker-port 8000` (no range args) → still works via auto-partition fallback in `run_subscribers.py` (untouched)
