# Plan: Broker Deletion & Snapshot Recovery Fixes

## Current Issues Identified

### 1. Manual Broker Deletion Bypasses Recovery
**Problem**: When broker deleted via UI/orchestrator:
- Container stopped/removed ✅
- Orchestrator state updated ✅
- **NO** snapshot recovery triggered ❌
- **NO** subscriber notification ❌
- Subscribers remain connected to dead broker address

**Root Cause**: `docker_manager.remove_broker()` only handles Docker lifecycle, not system recovery coordination.

### 2. UI Crash on Broker Deletion
**Problem**: UI crashes when deleting broker
**Need to**: Debug exact error in `BrokerControls.tsx:handleRemove()`

### 3. Snapshot System Not Triggered for Manual Deletions
**Problem**: Snapshot recovery relies on heartbeat timeouts (crash detection), not manual deletions.

## What's Already Implemented

✅ **NetworkSubscriber** - Complete implementation  
✅ **Broker remote tracking** - `_remote_subscribers`, `_payload_to_remotes`  
✅ **Snapshot system** - Chandy-Lamport with k=2 replication  
✅ **Recovery protocol** - `request_snapshot_from_peers()`, `recover_from_snapshot()`  
✅ **Subscriber recovery** - `_handle_broker_recovery()`  
✅ **Broker reconnection** - `_reconnect_subscribers()`

## Fixes Required

### Phase 1: Debug UI Crash
1. Add error logging to `BrokerControls.tsx:handleRemove()`
2. Check browser console for errors
3. Verify API endpoint `/api/brokers/{id}` works
4. Check for CORS or network issues

### Phase 2: Extend Orchestrator for Recovery Coordination
Modify `docker_manager.py:remove_broker()` to:
1. Before deleting broker, identify affected subscribers
2. Select surviving broker for recovery
3. Trigger snapshot recovery on surviving broker
4. Optionally delete/recreate affected subscribers

**New methods needed**:
- `_find_affected_subscribers(broker_id)` - Find subscribers connected to this broker
- `_select_recovery_broker()` - Choose surviving broker
- `_trigger_recovery(dead_broker, surviving_broker)` - Coordinate recovery

### Phase 3: Unified Broker Removal Protocol
Create `BrokerRemovalCoordinator` that handles:
1. Both manual deletions AND crash scenarios
2. Snapshot recovery coordination
3. Subscriber reconnection
4. UI feedback on recovery progress

### Phase 4: Testing
1. Test broker deletion with active subscribers
2. Verify snapshot recovery triggers
3. Confirm subscribers reconnect to new broker
4. Test UI stability

## Files to Modify

1. **`/Users/krishna/dev/aether/dashboard/src/components/BrokerControls.tsx`**
   - Add error handling/logging
   - Show recovery status in UI

2. **`/Users/krishna/dev/aether/aether/orchestrator/docker_manager.py`**
   - Extend `remove_broker()` for recovery
   - Add recovery coordination methods

3. **`/Users/krishna/dev/aether/aether/orchestrator/main.py`**
   - Update `remove_broker()` endpoint
   - Add recovery status endpoints

4. **`/Users/krishna/dev/aether/aether/gossip/broker.py`**
   - Ensure `_reconnect_subscribers()` works correctly
   - Add API for external recovery triggering

## Implementation Priority

**HIGH**: Fix UI crash (immediate user impact)  
**HIGH**: Add basic recovery to `remove_broker()`  
**MEDIUM**: Create `BrokerRemovalCoordinator`  
**LOW**: Enhanced UI feedback

## Success Criteria
1. UI doesn't crash when deleting broker
2. Subscribers automatically reconnect when broker deleted
3. Snapshot recovery triggers for manual deletions
4. System remains stable during broker removal