# Plan: Replace pickle with orjson for network serialization

## Motivation

`pickle.dumps`/`pickle.loads` is the dominant CPU cost in the broker hot path.
Each outbound message is serialized once per destination (subscriber + fanout peers).
At 8,000 msg/s entering the mesh, pickle runs O(10,000) times/second.
Replacing it with `orjson` (a fast C-based JSON library) is estimated to yield 3-5x
throughput improvement, potentially closing the 10B SLO gap.

## Surface area

### File to modify: `aether/network/node.py`

**6 lines** across 3 contexts:

| Line | Current code | Role | Hot path? |
|------|-------------|------|-----------|
| 131 | `pickle.loads(data)` | Deserialize inbound handshake | No (once/conn) |
| 159 | `pickle.loads(data)` | Deserialize inbound msg | **Yes** (every msg) |
| 284 | `pickle.dumps(msg)` | Serialize outbound msg | **Yes** (every msg) |
| 306 | `pickle.dumps(id_msg)` | Serialize outbound handshake | No (once/conn) |
| 371 | `pickle.loads(data)` | Deserialize outbound msg | **Yes** (every msg) |

### File to create: `aether/network/serialize.py`

Thin type-registry layer:

```
serialize(msg)      -> identify type -> to_dict -> orjson -> bytes
deserialize(bytes)  -> orjson -> dict -> read type_id -> from_dict -> typed object
```

### All 16 message types (3 source files, all simple dataclasses)

**`aether/gossip/protocol.py`** (8 types):
- GossipMessage, Heartbeat, MembershipUpdate, SubscribeRequest, SubscribeAck,
  UnsubscribeRequest, UnsubscribeAck, PayloadMessageDelivery

**`aether/snapshot.py`** (8 types):
- SnapshotMarker, SnapshotReplica, SnapshotRequest, SnapshotResponse, Ping,
  Pong, BrokerRecoveryNotification, BrokerSnapshot

**`aether/network/node.py`** (1 type, internal):
- _IdentificationMessage (convert to dataclass for uniform handling)

### Non-dataclass types needing custom coders (3)

| Type | File | Fields | Coder |
|------|------|--------|-------|
| `NodeAddress` | `node.py:14` | host: str, port: int | `{"host": ..., "port": ...}` |
| `PayloadRange` | `core/payload_range.py` | low, high: int | `{"low": ..., "high": ...}` |
| `Message` | `core/message.py` | _payload: UInt8 (__slots__) | `{"payload": ...}` |

## Implementation

### Step 1: Add orjson to `pyproject.toml`

```toml
dependencies = [
    ...
    "orjson>=3.10.0",
]
```

### Step 2: Create `aether/network/serialize.py`

Three sections:

**2a. Type registry**

```python
_TYPE_REGISTRY: dict[int, type] = {}
_REVERSE_REGISTRY: dict[type, int] = {}

def register(cls: type, type_id: int) -> None:
    assert type_id not in _TYPE_REGISTRY
    _TYPE_REGISTRY[type_id] = cls
    _REVERSE_REGISTRY[cls] = type_id
```

**2b. Custom coders for non-dataclass types**

```python
_CUSTOM_ENCODERS: dict[type, Callable] = {}
_CUSTOM_DECODERS: dict[type, Callable] = {}

def register_coder(cls: type, encoder: Callable, decoder: Callable) -> None: ...

# NodeAddress -> {"host": str, "port": int}
# PayloadRange -> {"low": int, "high": int}
# Message -> {"payload": int}
```

**2c. Generic to_dict / from_dict**

Uses `dataclasses.fields()` for recursive conversion:

```python
def _to_dict(obj: Any) -> Any:
    cls = type(obj)
    if cls in _CUSTOM_ENCODERS:
        return _CUSTOM_ENCODERS[cls](obj)
    if dataclasses.is_dataclass(cls):
        return {f.name: _to_dict(getattr(obj, f.name)) for f in dataclasses.fields(cls)}
    if isinstance(obj, set):
        return [_to_dict(x) for x in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    raise TypeError(f"cannot serialize {cls}")

def _from_dict(cls: type, data: Any) -> Any:
    if cls in _CUSTOM_DECODERS:
        return _CUSTOM_DECODERS[cls](data)
    if dataclasses.is_dataclass(cls):
        kwargs = {}
        for f in dataclasses.fields(cls):
            kwargs[f.name] = _from_dict(f.type, data[f.name])
        return cls(**kwargs)
    # Handle generic types: Set[X], Optional[X], List[X], Dict[K,V]
    return data
```

**2d. Public API**

```python
def serialize(msg: Any) -> bytes:
    type_id = _REVERSE_REGISTRY[type(msg)]
    return orjson.dumps({"t": type_id, "d": _to_dict(msg)})

def deserialize(data: bytes) -> Any:
    obj = orjson.loads(data)
    cls = _TYPE_REGISTRY[obj["t"]]
    return _from_dict(cls, obj["d"])
```

**2e. Registration calls** (bottom of file)

Register all 16 types with sequential IDs 1-16.

### Step 3: Modify `aether/network/node.py`

**Changes:**

1. `import pickle` -> remove
2. Add `from aether.network.serialize import serialize, deserialize`

Replace the 6 pickle calls:

| Before | After |
|--------|-------|
| `msg = pickle.loads(data)` | `msg = deserialize(data)` |
| `data = pickle.dumps(msg)` | `data = serialize(msg)` |
| `pickle.dumps(id_msg)` | `serialize(id_msg)` |

3. Convert `_IdentificationMessage` from manual class to `@dataclass`:

```python
@dataclass
class _IdentificationMessage:
    sender: NodeAddress
```

4. Register `_IdentificationMessage` in serialize.py (or inline in node.py).

### Step 4: Add unit tests in `tests/unit/test_serialize.py`

```
test_every_type_roundtrips()
  -> Creates each of the 16 types with realistic data
  -> serialize -> deserialize
  -> assert result == original

test_all_fields_covered()
  -> Introspects every registered type's field types
  -> Asserts all field types have a codec path (custom coder or primitive)

test_registry_no_collisions()
  -> Asserts no duplicate type IDs

test_node_address_roundtrip()
  -> Tests NodeAddress specifically (non-dataclass)

test_set_roundtrip()
  -> Tests Set[NodeAddress] (MembershipUpdate.brokers)
```

### Step 5: Verify

```bash
make test          # pytest tests/unit/ --tb=short
make lint           # mypy aether/

# Optional quick benchmark smoke test:
# Run 3B-5P throughput config before and after
```

### Step 6: Commands

```
pip install orjson
make test && make lint  # fix any issues
```

## Regression prevention

| Risk | Mitigation |
|------|-----------|
| Missing type registration | `test_all_fields_covered()` - introspect every field of every registered type |
| Type ID collision | `test_registry_no_collisions()` - assert all 16 IDs are unique |
| Silently wrong deserialization | `test_every_type_roundtrips()` - assert equality after serialize/deserialize |
| NodeAddress hash changes | `NodeAddress.__hash__` depends on host + port - unchanged by serialization format |
| Mixed-version cluster | Not a concern - benchmark rebuilds all containers; no rolling upgrade needed |
| Message size bloat | orjson is compact for scalar fields. Slightly larger than pickle for sets of NodeAddress, but negligible vs TCP overhead |
