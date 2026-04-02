import { useEffect, useRef, useState } from "react";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceY,
  type Simulation,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from "d3";
import type { ComponentType, TopologyEdge, TopologyNode } from "../api/types";

// Soft vertical layering: publishers top, brokers middle, subscribers bottom.
// Values are proportions of canvas height (0 = top, 1 = bottom).
const LAYER_Y: Record<ComponentType, number> = {
  publisher: 0.2,
  bootstrap: 0.5,
  broker: 0.5,
  subscriber: 0.8,
};

export interface PositionedNode extends TopologyNode, SimulationNodeDatum {
  x: number;
  y: number;
}

export interface PositionedEdge extends SimulationLinkDatum<PositionedNode> {
  edge_type: string;
  sourceId: string;
  targetId: string;
}

export function useForceSimulation(
  nodes: TopologyNode[],
  edges: TopologyEdge[],
  width: number,
  height: number,
) {
  const simRef = useRef<Simulation<PositionedNode, PositionedEdge> | null>(null);
  const prevNodesRef = useRef<Map<string, { x: number; y: number; fx?: number | null; fy?: number | null }>>(new Map());
  const [positionedNodes, setPositionedNodes] = useState<PositionedNode[]>([]);
  const [positionedEdges, setPositionedEdges] = useState<PositionedEdge[]>([]);

  useEffect(() => {
    if (width === 0 || height === 0) return;

    // Preserve existing positions
    const prev = prevNodesRef.current;

    const simNodes: PositionedNode[] = nodes.map((n) => {
      const existing = prev.get(n.id);
      return {
        ...n,
        x: existing?.x ?? width / 2 + (Math.random() - 0.5) * 100,
        y: existing?.y ?? height / 2 + (Math.random() - 0.5) * 100,
        fx: existing?.fx ?? undefined,
        fy: existing?.fy ?? undefined,
      };
    });

    // Filter out edges referencing deleted nodes — D3 forceLink throws on missing IDs.
    const nodeIds = new Set(nodes.map((n) => n.id));
    const validEdges = edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));

    const simEdges: PositionedEdge[] = validEdges.map((e) => ({
      source: e.source,
      target: e.target,
      edge_type: e.edge_type,
      sourceId: e.source,
      targetId: e.target,
    }));

    // Stop previous simulation
    simRef.current?.stop();

    const sim = forceSimulation<PositionedNode>(simNodes)
      .force(
        "link",
        forceLink<PositionedNode, PositionedEdge>(simEdges)
          .id((d) => d.id)
          .distance(100),
      )
      .force("charge", forceManyBody().strength(-300))
      .force("center", forceCenter(width / 2, height / 2))
      .force("collide", forceCollide(40))
      .force(
        "layerY",
        forceY<PositionedNode>((d) => height * (LAYER_Y[d.component_type] ?? 0.5)).strength(0.12),
      )
      .alphaDecay(0.02);

    sim.on("tick", () => {
      setPositionedNodes([...simNodes]);
      setPositionedEdges([...simEdges]);

      // Update prev positions
      const map = new Map<string, { x: number; y: number; fx?: number | null; fy?: number | null }>();
      for (const n of simNodes) {
        map.set(n.id, { x: n.x, y: n.y, fx: n.fx, fy: n.fy });
      }
      prevNodesRef.current = map;
    });

    simRef.current = sim;

    return () => {
      sim.stop();
    };
  }, [nodes, edges, width, height]);

  return { nodes: positionedNodes, edges: positionedEdges, simulation: simRef };
}
