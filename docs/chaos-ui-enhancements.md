# Chaos Demo UI Enhancements — Implementation Plan

Generated 2026-04-02
Status: PLAN — awaiting review

This document describes three high-impact UI enhancements for the "Create Chaos" workflow. Each enhancement is designed to make the demo visually communicate the system's resilience story — no explanation needed, the screen tells the story.

---

## Design Philosophy

The chaos demo should answer three questions in under 5 seconds:

1. **Something broke** — the viewer immediately sees a failure
2. **The system is handling it** — the viewer sees intelligent decision-making in progress
3. **It recovered** — the viewer sees the recovery time number and the system is green again

Every pixel added should serve one of these three signals. No decorative chrome.

---

## Enhancement 1: Chaos Recovery Timeline

### What

A dedicated horizontal timeline component that appears in the ControlPanel when chaos is active. It replaces the current simple status text with a visual step-by-step progression showing each phase of the recovery, its duration, and the final recovery time.

### Why

Right now the EventLog shows events as flat text lines. During chaos, the viewer sees a burst of events but has to mentally reconstruct the story. A dedicated timeline tells the story at a glance and — critically — surfaces the **recovery time number**, which is the single most impressive metric in the entire demo.

### Design

```
┌─ Chaos ───────────────────────────────────────────┐
│                                    [Create Chaos] │
│                                                   │
│  ──●──────────●──────────●──────────●─────────   │
│   DEAD      CHOOSING    PATH A    RECOVERED       │
│   0.3s       0.1s        3.8s      ✓              │
│                                                   │
│  Total recovery: 4.2s                             │
│  ↳ Path A — Snapshot Restore                      │
└───────────────────────────────────────────────────┘
```

**States:**
- **Inactive** → nothing shown, just the "Create Chaos" button
- **Active** → timeline appears, current phase pulses, completed phases are solid
- **Recovered** → all phases green, recovery time displayed, fades after 8s
- **Failed** → last phase turns red with "Recovery failed" message

### Implementation

**New file:** `dashboard/src/components/ChaosTimeline.tsx`

**State tracking additions** to `ChaosState` (in `useAetherStore.ts`):
```typescript
interface ChaosState {
  active: boolean;
  targetBrokerId: number | null;
  phase: "triggered" | "declared_dead" | "recovering" | "recovered" | "failed";
  recoveryPath: "replacement" | "redistribution" | null;
  phaseTimestamps: Record<string, number>;   // phase name → epoch seconds
  recoveryTime: number | null;                // total seconds from dead to recovered
  reason: string | null;                      // why this recovery path was chosen
  deadBrokerEdges: string[];                  // edge IDs that should show as broken
}
```

**Phase tracking logic** in `useWebSocket.ts`:
- On `broker_declared_dead`: record `phaseTimestamps.declared_dead = event.timestamp`
- On `broker_recovery_started`: record `phaseTimestamps.recovering = event.timestamp`, extract `reason`
- On `broker_recovered`/`broker_recovery_failed`: record `phaseTimestamps.recovered`, compute `recoveryTime = recovered - declared_dead`

**Timeline rendering:**
- Each phase is a dot + label + duration
- Dots connected by a horizontal line
- Current phase: pulsing animation
- Completed phases: solid color
- Duration computed live: `now - phaseTimestamps[phase]`
- After recovery: show total time prominently

**Color mapping:**
- `declared_dead` → red (`#ef4444`)
- `recovering` → amber (`#f59e0b`)
- `recovered` → green (`#22c55e`)
- `failed` → red, with error indicator

**Integration:**
- Replace the current chaos status text in `ChaosControls.tsx` with `<ChaosTimeline />`
- The existing `pathLabel` ("Path A — Snapshot Restore") moves below the timeline

---

## Enhancement 2: Edge Breakage & Rebuild on Topology Graph

### What

When a broker dies during chaos, all edges connected to that broker visually break — particles stop flowing, edges fade to red and dissolve. During recovery, edges rebuild with a "growing" animation from the recovered broker outward.

### Why

The topology graph is the centerpiece of the dashboard. Making the failure and recovery **visually obvious** on the graph means someone can understand what's happening without reading any text. This is the "show don't tell" moment.

### Design

**Phase: declared_dead**
- Edges connected to the dead broker turn from their normal color to red (`#ef4444`)
- Edge opacity drops from 0.35 → 0 over 0.5s
- Particles on those edges stop spawning (existing particles finish their current animation)
- The dead broker node dims (already partially implemented — just needs edge treatment)

**Phase: recovering**
- If Path A (replacement): edges rebuild from the replacement broker outward with a green "growing" animation
  - New edges appear as a green line that grows from the broker node to its neighbor
  - Particles resume flowing on rebuilt edges
- If Path B (redistribution): subscriber edges animate from the dead broker position to their new broker
  - The edge line morphs from old broker → new broker
  - Subscriber node visually slides to new position (or at least its edge re-routes)

**Phase: recovered**
- All edges fully restored to normal color
- Brief green flash on the recovered node (already implemented)

### Implementation

**Modified file:** `dashboard/src/components/TopologyGraph.tsx`

**New store state** — track the dead broker's edges:
```typescript
// Add to ChaosState:
deadBrokerEdges: string[];  // edge IDs that should show as broken
```

**Edge state management** in `useWebSocket.ts`:
- On `broker_declared_dead`: find all edges connected to that broker in the topology, store in `chaosState.deadBrokerEdges`
- On `broker_recovered`: clear `deadBrokerEdges`

**AnimatedEdge component changes:**
- Accept a new `broken` prop (boolean)
- When `broken`:
  - Base line color transitions to red
  - Opacity animates to 0
  - Particles stop spawning (don't create new `<animateMotion>` elements)
  - Add a brief "snap" animation (line contracts toward the dead node)

**Rebuild animation:**
- Add a `rebuilding` prop to AnimatedEdge
- When `rebuilding`:
  - Line draws from source to target using `stroke-dashoffset` animation
  - Color is green during rebuild, transitions to normal when complete
  - Particles resume with a staggered delay

**Subscriber migration (Path B):**
- This is trickier because the topology nodes themselves change
- The topology refreshes via `refreshAll()` on `STRUCTURAL_EVENTS`
- The D3 force simulation already handles node repositioning
- The key is to make the **edge transition smooth** rather than instant
- Add `previousEdges` tracking: when edges change, animate from old positions to new

---

## Enhancement 3: Recovery Path Decision Display

### What

When the system chooses between Path A and Path B, show **why** — the decision rationale is displayed in the ChaosControls panel.

### Why

The intelligent decision-making between Path A (snapshot restore) and Path B (redistribution) is invisible to the viewer. Showing the rationale makes the system feel alive and intelligent, not just reactive.

### Design

```
┌─ Chaos ───────────────────────────────────────────┐
│                                    [Create Chaos] │
│                                                   │
│  ──●──────────●──────────●──────────●─────────   │
│   DEAD      CHOOSING    PATH A    RECOVERED       │
│   0.3s       0.1s        3.8s      ✓              │
│                                                   │
│  Fresh snapshot found (2.1s old) → Path A         │
│  Total recovery: 4.2s                             │
└───────────────────────────────────────────────────┘
```

Or for Path B:
```
│  No snapshot available → Path B (Redistribution)  │
│  3 subscribers reassigned                         │
```

### Implementation

**Backend change** — add decision rationale to the `broker_recovery_started` event:

**Modified file:** `aether/orchestrator/recovery.py`

Current emit for Path A:
```python
await self._broadcaster.emit(
    EventType.BROKER_RECOVERY_STARTED,
    {"broker_id": broker_id, "recovery_path": "replacement"},
)
```

Change to:
```python
await self._broadcaster.emit(
    EventType.BROKER_RECOVERY_STARTED,
    {
        "broker_id": broker_id,
        "recovery_path": "replacement",
        "reason": f"Fresh snapshot found ({age:.1f}s old)",
    },
)
```

For Path B:
```python
await self._broadcaster.emit(
    EventType.BROKER_RECOVERY_STARTED,
    {
        "broker_id": broker_id,
        "recovery_path": "redistribution",
        "reason": "No fresh snapshot available",
    },
)
```

**Frontend changes:**

1. **`ChaosState`** — add `reason: string | null`
2. **`useWebSocket.ts`** — extract `reason` from `broker_recovery_started` event data
3. **`ChaosTimeline.tsx`** — display reason below the timeline, styled in muted text
4. **`EventLog.tsx`** — update `summarize()` to include reason for `broker_recovery_started`

---

## Implementation Order

These must be done in sequence because each builds on the previous:

```
Step 1: ChaosState expansion (store changes)
Step 2: Phase timestamp tracking (WebSocket hook)
Step 3: ChaosTimeline component (new file)
Step 4: Integrate ChaosTimeline into ChaosControls
Step 5: Edge breakage animation (TopologyGraph)
Step 6: Recovery path decision display (backend + frontend)
Step 7: Subscriber migration animation (TopologyGraph, Path B only)
```

Steps 1-4 are the highest-impact and can ship independently.
Steps 5-7 are visual polish that make the demo truly jaw-dropping.

---

## Detailed Step Breakdown

### Step 1: ChaosState Expansion

**File:** `dashboard/src/store/useAetherStore.ts`

Add to `ChaosState` interface:
```typescript
phaseTimestamps: Record<string, number>;
recoveryTime: number | null;
reason: string | null;
deadBrokerEdges: string[];
```

Update initial state in `setChaosState`:
```typescript
setChaosState({
  active: true,
  targetBrokerId: res.chaos_target,
  phase: "triggered",
  recoveryPath: null,
  phaseTimestamps: { triggered: Date.now() / 1000 },
  recoveryTime: null,
  reason: null,
  deadBrokerEdges: [],
});
```

### Step 2: Phase Timestamp Tracking

**File:** `dashboard/src/hooks/useWebSocket.ts`

Add timestamp recording to each chaos event handler:
```typescript
if (event.type === "broker_declared_dead") {
  setChaosPhase("declared_dead", {
    phaseTimestamps: { ...chaosState.phaseTimestamps, declared_dead: event.timestamp },
  });
} else if (event.type === "broker_recovery_started") {
  const path = event.data.recovery_path as "replacement" | "redistribution" | undefined;
  const reason = event.data.reason as string | undefined;
  setChaosPhase("recovering", {
    recoveryPath: path ?? null,
    reason: reason ?? null,
    phaseTimestamps: { ...chaosState.phaseTimestamps, recovering: event.timestamp },
  });
} else if (event.type === "broker_recovered") {
  const deadAt = chaosState.phaseTimestamps.declared_dead;
  const recoveryTime = deadAt ? event.timestamp - deadAt : null;
  setChaosPhase("recovered", {
    recoveryTime,
    phaseTimestamps: { ...chaosState.phaseTimestamps, recovered: event.timestamp },
  });
  setTimeout(clearChaosState, 8000);
} else if (event.type === "broker_recovery_failed") {
  setChaosPhase("failed", {
    phaseTimestamps: { ...chaosState.phaseTimestamps, failed: event.timestamp },
  });
  setTimeout(clearChaosState, 8000);
}
```

### Step 3: ChaosTimeline Component

**New file:** `dashboard/src/components/ChaosTimeline.tsx`

Renders the horizontal timeline with:
- Phase dots connected by lines
- Labels under each dot
- Duration under each label (live-updating for current phase)
- Total recovery time at the bottom after completion
- Recovery reason text
- Pulsing animation on current phase

### Step 4: Integrate into ChaosControls

**File:** `dashboard/src/components/ChaosControls.tsx`

Replace the current status text block with `<ChaosTimeline />`.
Keep the "Create Chaos" button and API error display.

### Step 5: Edge Breakage Animation

**File:** `dashboard/src/components/TopologyGraph.tsx`

Modify `AnimatedEdge` to accept `broken` and `rebuilding` props.
Add CSS transitions for edge color/opacity changes.
Stop particle spawning on broken edges.
Add rebuild animation with green growing line.

### Step 6: Recovery Path Decision Display

**Backend:** `aether/orchestrator/recovery.py` — add `reason` field to `BROKER_RECOVERY_STARTED` event
**Frontend:** `ChaosTimeline.tsx` — display reason text below timeline

### Step 7: Subscriber Migration Animation

**File:** `dashboard/src/components/TopologyGraph.tsx`

This is the most complex step. When Path B redistributes subscribers:
- Track `previousEdges` in the store
- When topology refreshes, compare old edges to new edges
- For changed subscriber edges: animate the line from old position to new
- Use D3 transitions for smooth movement

---

## Files Changed Summary

| File | Change |
|------|--------|
| `dashboard/src/store/useAetherStore.ts` | Expand ChaosState interface |
| `dashboard/src/hooks/useWebSocket.ts` | Add timestamp tracking, reason extraction |
| `dashboard/src/components/ChaosTimeline.tsx` | **NEW** — timeline component |
| `dashboard/src/components/ChaosControls.tsx` | Integrate ChaosTimeline |
| `dashboard/src/components/TopologyGraph.tsx` | Edge breakage + rebuild animations |
| `dashboard/src/components/EventLog.tsx` | Add reason to broker_recovery_started summary |
| `aether/orchestrator/recovery.py` | Add `reason` field to recovery started event |

---

## Tradeoffs & Decisions

### Why not a separate chaos panel?
The ChaosControls sidebar is already the entry point. Keeping the timeline there means the viewer's eye stays in one place. The topology graph shows the visual story (edges breaking/rebuilding), the sidebar shows the narrative story (timeline + recovery time).

### Why track phaseTimestamps in the store (not local state)?
Phase timestamps need to survive component re-renders and be accessible from multiple components (ChaosTimeline, TopologyGraph). Zustand is the right place.

### Why 8s auto-clear (not 3s)?
Recovery time is the hero number. 3 seconds is too short for the viewer to read and process "Recovered in 4.2s". 8 seconds gives enough time without being permanent.

### Edge breakage: CSS vs SVG animation
SVG `stroke-dashoffset` animation is more precise and works with the existing `<animateMotion>` particle system. CSS transitions on SVG elements are less reliable across browsers. Use SVG native animations.

### Subscriber migration: full reposition vs edge-only
Full node repositioning during a D3 force simulation is jarring. Better to just animate the edges — the subscriber node will naturally drift to its new position via the force simulation, and the edge animation bridges the gap.

---

## Risk Assessment

| Step | Risk | Mitigation |
|------|------|------------|
| 1-2 (store + hook) | Low — additive changes, no breaking | Easy to revert |
| 3 (timeline) | Low — new component, isolated | No impact if broken |
| 4 (integration) | Low — replaces existing text | Fallback to text if timeline fails |
| 5 (edge breakage) | Medium — touches core graph rendering | Feature flag via store state, defaults to off |
| 6 (decision display) | Low — backend adds field, frontend reads | Backward compatible (reason is optional) |
| 7 (subscriber migration) | High — D3 force sim interaction | Ship last, can defer to future pass |
