# TODO: Network-Enabled Subscribers

## Overview
Make subscribers network-aware so they can communicate with brokers over UDP sockets, similar to how publishers and brokers already communicate.

---

## Phase 1: Define Message Types

Add new dataclasses to `aether.gossip.protocol` for subscriber ↔ broker communication.

- [ ] `SubscribeRequest` - subscriber asks broker to subscribe to a PayloadRange
- [ ] `SubscribeAck` - broker confirms subscription
- [ ] `UnsubscribeRequest` - subscriber asks to unsubscribe
- [ ] `UnsubscribeAck` - broker confirms unsubscription
- [ ] `MessageDelivery` - broker sends a published message to subscriber

---

## Phase 2: Create `NetworkSubscriber`

Create `aether.network.subscriber` - a network wrapper around the existing `Subscriber`.

- [ ] Create `NetworkSubscriber` class with:
  - [ ] Inner `Subscriber` instance (composition)
  - [ ] `NodeAddress` for its identity
  - [ ] `NetworkNode` for socket communication
  - [ ] `broker: Optional[NodeAddress]` - which broker it's connected to
  - [ ] `subscriptions: Set[PayloadRange]` - what it's subscribed to
- [ ] Implement `connect_to_broker(broker: NodeAddress)` method
- [ ] Implement `subscribe(payload_range: PayloadRange)` method
  - Sends `SubscribeRequest` to broker
- [ ] Implement `unsubscribe(payload_range: PayloadRange)` method
  - Sends `UnsubscribeRequest` to broker
- [ ] Implement `_receive_loop()` thread to handle incoming messages:
  - [ ] Handle `MessageDelivery` → delegate to inner `Subscriber.handle_msg()`
  - [ ] Handle `SubscribeAck` → confirm subscription succeeded
  - [ ] Handle `UnsubscribeAck` → confirm unsubscription succeeded
- [ ] Implement `start()` and `stop()` methods for the receive loop thread
- [ ] Expose `counts` property from inner subscriber

---

## Phase 3: Extend `GossipBroker` for Remote Subscribers

Modify `aether.gossip.broker` to track and deliver to remote subscribers.

### New State to Add
- [ ] `_remote_subscribers: Dict[NodeAddress, Set[PayloadRange]]` - what each remote subscriber wants
- [ ] `_payload_to_remote: List[Set[NodeAddress]]` - buckets indexed by payload (0-255), each containing subscriber addresses

### New Methods to Add
- [ ] `_handle_subscribe_request(request: SubscribeRequest, sender: NodeAddress)`
  - Add sender to `_remote_subscribers` and `_payload_to_remote`
  - Send `SubscribeAck` back to sender
- [ ] `_handle_unsubscribe_request(request: UnsubscribeRequest, sender: NodeAddress)`
  - Remove sender from tracking
  - Send `UnsubscribeAck` back to sender
- [ ] `_deliver_to_remote_subscribers(msg: Message)`
  - Look up subscribers in `_payload_to_remote[msg.payload]`
  - Send `MessageDelivery` to each

### Modify Existing Methods
- [ ] Extend `_receive_loop()` to handle new message types:
  - `elif isinstance(msg, SubscribeRequest): ...`
  - `elif isinstance(msg, UnsubscribeRequest): ...`
- [ ] Extend `_handle_gossip_message()` to also call `_deliver_to_remote_subscribers()`

---

## Phase 4: Testing

- [ ] Write unit tests for `NetworkSubscriber`
- [ ] Write integration test: NetworkSubscriber connects to GossipBroker, subscribes, receives message
- [ ] Test multiple subscribers with overlapping PayloadRanges
- [ ] Test unsubscribe flow

---

## Future Considerations (Not in Scope Yet)

- [ ] How subscribers discover brokers (bootstrap?)
- [ ] Subscriber failover if broker dies
- [ ] Thread safety / locking for concurrent access
- [ ] Subscriber heartbeats / liveness detection
- [ ] Delivery acknowledgments from subscriber to broker
- [ ] What happens to messages if subscriber disconnects?

---

## Files to Modify/Create

| Module | Action |
|------|--------|
| `aether.gossip.protocol` | Add new message dataclasses |
| `aether.network.subscriber` | Create new file |
| `aether.gossip.broker` | Extend for remote subscriber tracking |
| `tests/integration/test_network_subscriber.py` | Create new test file |

---
