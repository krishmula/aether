import { useCallback, useEffect, useRef, useState } from "react";
import { drag, select, zoom, type D3DragEvent, type ZoomTransform } from "d3";
import { useAetherStore } from "../store/useAetherStore";
import { NODE_COLORS, EDGE_COLORS } from "../lib/colors";
import { useForceSimulation, type PositionedNode, type PositionedEdge } from "./useForceSimulation";
import type { ComponentType } from "../api/types";

const NODE_RADIUS: Record<ComponentType, number> = {
  bootstrap: 16,
  broker: 18,
  publisher: 14,
  subscriber: 14,
};

const NODE_LABEL: Record<ComponentType, string> = {
  bootstrap: "BS",
  broker: "B",
  publisher: "P",
  subscriber: "S",
};

// Particle config per edge type
const PARTICLE_CONFIG: Record<string, { count: number; speed: number; radius: number }> = {
  publish:   { count: 3, speed: 2.2, radius: 2.5 },
  subscribe: { count: 2, speed: 2.8, radius: 2.0 },
  peer:      { count: 2, speed: 4.0, radius: 1.5 },
};

function edgePath(src: PositionedNode, tgt: PositionedNode): string {
  return `M ${src.x} ${src.y} L ${tgt.x} ${tgt.y}`;
}

function freshnessColor(ageSec: number | null): string {
  if (ageSec === null) return "#e05252";
  if (ageSec < 30) return "#5ec269";
  if (ageSec < 90) return "#e0a643";
  return "#e05252";
}

function freshnessDasharray(ageSec: number | null, arcR: number): string {
  const circ = 2 * Math.PI * arcR;
  if (ageSec === null) return "3 5";
  const filled = Math.max(0, (1 - ageSec / 90)) * circ;
  return `${filled} ${circ - filled}`;
}

/** Renders a single animated edge — the line, flowing dash, and traveling particles. */
function AnimatedEdge({
  edge,
  index,
}: {
  edge: PositionedEdge;
  index: number;
}) {
  const src = edge.source as PositionedNode;
  const tgt = edge.target as PositionedNode;
  if (typeof src === "string" || typeof tgt === "string") return null;
  if (src.x == null || tgt.x == null) return null;

  const color = EDGE_COLORS[edge.edge_type as keyof typeof EDGE_COLORS] ?? "#555";
  const cfg = PARTICLE_CONFIG[edge.edge_type] ?? PARTICLE_CONFIG.peer;
  const path = edgePath(src, tgt);
  const pathId = `edge-${edge.sourceId}-${edge.targetId}-${index}`;
  const isPeer = edge.edge_type === "peer";

  return (
    <g>
      {/* Invisible path for animateMotion to follow */}
      <path id={pathId} d={path} fill="none" stroke="none" />
      {isPeer && (
        <path
          id={`${pathId}-rev`}
          d={edgePath(tgt, src)}
          fill="none"
          stroke="none"
        />
      )}

      {/* Base edge — faint, wide, blurred for underglow */}
      <line
        x1={src.x} y1={src.y} x2={tgt.x} y2={tgt.y}
        stroke={color}
        strokeWidth={4}
        opacity={0.08}
        filter="url(#edge-glow)"
      />

      {/* Flowing dash line */}
      <line
        x1={src.x} y1={src.y} x2={tgt.x} y2={tgt.y}
        stroke={color}
        strokeWidth={1.2}
        strokeDasharray="8 6"
        opacity={0.35}
        className="flowing-dash"
      />

      {/* Solid subtle core */}
      <line
        x1={src.x} y1={src.y} x2={tgt.x} y2={tgt.y}
        stroke={color}
        strokeWidth={0.5}
        opacity={0.2}
      />

      {/* Traveling particles — forward direction */}
      {Array.from({ length: cfg.count }).map((_, pi) => {
        const dur = `${cfg.speed + pi * 0.7}s`;
        const begin = `${pi * (cfg.speed / cfg.count)}s`;
        return (
          <g key={`p-${pi}`}>
            {/* Particle glow */}
            <circle r={cfg.radius * 2.5} fill={color} opacity={0.2}>
              <animateMotion
                dur={dur}
                begin={begin}
                repeatCount="indefinite"
              >
                <mpath href={`#${pathId}`} />
              </animateMotion>
            </circle>
            {/* Particle core */}
            <circle r={cfg.radius} fill={color} opacity={0.9}>
              <animateMotion
                dur={dur}
                begin={begin}
                repeatCount="indefinite"
              >
                <mpath href={`#${pathId}`} />
              </animateMotion>
              <animate
                attributeName="opacity"
                values="0.5;1;0.5"
                dur={dur}
                begin={begin}
                repeatCount="indefinite"
              />
            </circle>
          </g>
        );
      })}

      {/* Peer edges: particles going the other direction too */}
      {isPeer &&
        Array.from({ length: cfg.count }).map((_, pi) => {
          const dur = `${cfg.speed + 0.4 + pi * 0.7}s`;
          const begin = `${pi * (cfg.speed / cfg.count) + 0.5}s`;
          return (
            <g key={`pr-${pi}`}>
              <circle r={cfg.radius * 2.5} fill={color} opacity={0.15}>
                <animateMotion
                  dur={dur}
                  begin={begin}
                  repeatCount="indefinite"
                >
                  <mpath href={`#${pathId}-rev`} />
                </animateMotion>
              </circle>
              <circle r={cfg.radius} fill={color} opacity={0.7}>
                <animateMotion
                  dur={dur}
                  begin={begin}
                  repeatCount="indefinite"
                >
                  <mpath href={`#${pathId}-rev`} />
                </animateMotion>
              </circle>
            </g>
          );
        })}
    </g>
  );
}

export default function TopologyGraph() {
  const topology = useAetherStore((s) => s.topology);
  const chaosState = useAetherStore((s) => s.chaosState);
  const snapshots = useAetherStore((s) => s.snapshots);
  const snapshotWave = useAetherStore((s) => s.snapshotWave);
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNowSec(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const gRef = useRef<SVGGElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [transform, setTransform] = useState<ZoomTransform | null>(null);

  // Observe container size
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ width, height });
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const rawNodes = topology?.nodes ?? [];
  const rawEdges = topology?.edges ?? [];

  const { nodes, edges, simulation } = useForceSimulation(
    rawNodes,
    rawEdges,
    size.width,
    size.height,
  );

  // Zoom behavior
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;

    const z = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 3])
      .on("zoom", (event) => {
        setTransform(event.transform);
      });

    select(svg).call(z);

    return () => {
      select(svg).on(".zoom", null);
    };
  }, []);

  // Drag behavior
  const handleDragStart = useCallback(
    (_event: D3DragEvent<SVGGElement, PositionedNode, PositionedNode>, d: PositionedNode) => {
      const sim = simulation.current;
      if (sim) sim.alphaTarget(0.3).restart();
      d.fx = d.x;
      d.fy = d.y;
    },
    [simulation],
  );

  const handleDrag = useCallback(
    (_event: D3DragEvent<SVGGElement, PositionedNode, PositionedNode>, d: PositionedNode) => {
      d.fx = _event.x;
      d.fy = _event.y;
    },
    [],
  );

  const handleDragEnd = useCallback(
    (_event: D3DragEvent<SVGGElement, PositionedNode, PositionedNode>, d: PositionedNode) => {
      const sim = simulation.current;
      if (sim) sim.alphaTarget(0);
      d.fx = null;
      d.fy = null;
    },
    [simulation],
  );

  // Attach drag to nodes
  const nodeRefCallback = useCallback(
    (el: SVGGElement | null, node: PositionedNode) => {
      if (!el) return;
      const d = drag<SVGGElement, PositionedNode>()
        .on("start", handleDragStart)
        .on("drag", handleDrag)
        .on("end", handleDragEnd);

      select(el).datum(node).call(d);
    },
    [handleDragStart, handleDrag, handleDragEnd],
  );

  const transformStr = transform
    ? `translate(${transform.x},${transform.y}) scale(${transform.k})`
    : "";

  return (
    <div ref={containerRef} className="bg-bg border-b border-border relative overflow-hidden">
      {nodes.length === 0 ? (
        <div className="absolute inset-0 flex items-center justify-center flex-col gap-2">
          <p className="text-muted text-[13px] font-light">
            No topology yet
          </p>
          <p className="text-muted text-[11px] font-mono">
            seed the demo to visualize
          </p>
        </div>
      ) : null}
      <svg
        ref={svgRef}
        width={size.width}
        height={size.height}
        className="block"
      >
        <defs>
          {/* Soft glow filter */}
          <filter id="edge-glow" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          {/* Node glow filter */}
          <filter id="node-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="4" />
          </filter>
        </defs>

        <g ref={gRef} transform={transformStr}>
          {/* Animated edges with particles */}
          {edges.map((e, i) => (
            <AnimatedEdge
              key={`${e.sourceId}-${e.targetId}-${i}`}
              edge={e}
              index={i}
            />
          ))}

          {/* Nodes */}
          {nodes.map((node) => {
            const r = NODE_RADIUS[node.component_type] ?? 14;
            const color = NODE_COLORS[node.component_type] ?? "#888";
            const label = `${NODE_LABEL[node.component_type] ?? "?"}${node.component_id}`;
            const isAlive = node.status === "running" || node.status === "starting";
            return (
              <g
                key={node.id}
                ref={(el) => nodeRefCallback(el, node)}
                transform={`translate(${node.x},${node.y})`}
                className="cursor-grab active:cursor-grabbing"
              >
                {/* Outer ambient glow — blurred circle behind everything */}
                {isAlive && (
                  <circle
                    r={r + 10}
                    fill={color}
                    opacity={0.12}
                    filter="url(#node-glow)"
                  >
                    <animate
                      attributeName="opacity"
                      values="0.06;0.15;0.06"
                      dur="3s"
                      repeatCount="indefinite"
                    />
                  </circle>
                )}

                {/* Breathing ring */}
                {isAlive && (
                  <circle r={r + 8} fill="none" stroke={color} strokeWidth={0.5} opacity={0.2}>
                    <animate
                      attributeName="r"
                      values={`${r + 4};${r + 14};${r + 4}`}
                      dur="3s"
                      repeatCount="indefinite"
                    />
                    <animate
                      attributeName="opacity"
                      values="0.25;0.05;0.25"
                      dur="3s"
                      repeatCount="indefinite"
                    />
                  </circle>
                )}

                {/* Static dim glow for dead nodes */}
                {!isAlive && (
                  <circle r={r + 4} fill={color} opacity={0.05} />
                )}

                {/* Main circle with subtle inner glow */}
                <circle
                  r={r}
                  fill="#050505"
                  stroke={color}
                  strokeWidth={isAlive ? 2 : 1}
                  opacity={isAlive ? 1 : 0.4}
                />

                {/* Error pulsing ring */}
                {node.status === "error" && (
                  <circle
                    r={r + 2}
                    fill="none"
                    stroke="#ef4444"
                    strokeWidth={2}
                    opacity={0.8}
                  >
                    <animate
                      attributeName="opacity"
                      values="0.4;0.9;0.4"
                      dur="1.5s"
                      repeatCount="indefinite"
                    />
                    <animate
                      attributeName="r"
                      values={`${r + 1};${r + 5};${r + 1}`}
                      dur="1.5s"
                      repeatCount="indefinite"
                    />
                  </circle>
                )}

                {/* Chaos rings — only on the targeted broker */}
                {chaosState?.targetBrokerId === node.component_id && (
                  <>
                    {/* triggered / declared_dead: fast orange-red pulse */}
                    {(chaosState.phase === "triggered" || chaosState.phase === "declared_dead") && (
                      <circle r={r + 3} fill="none" stroke="#ff5500" strokeWidth={2.5} opacity={0.9}>
                        <animate attributeName="opacity" values="0.3;1.0;0.3" dur="0.8s" repeatCount="indefinite" />
                        <animate attributeName="r" values={`${r + 2};${r + 8};${r + 2}`} dur="0.8s" repeatCount="indefinite" />
                      </circle>
                    )}
                    {/* recovering: calm amber pulse */}
                    {chaosState.phase === "recovering" && (
                      <circle r={r + 3} fill="none" stroke="#f59e0b" strokeWidth={2} opacity={0.7}>
                        <animate attributeName="opacity" values="0.3;0.8;0.3" dur="1.2s" repeatCount="indefinite" />
                        <animate attributeName="r" values={`${r + 2};${r + 7};${r + 2}`} dur="1.2s" repeatCount="indefinite" />
                      </circle>
                    )}
                    {/* recovered: green ring that fades out */}
                    {chaosState.phase === "recovered" && (
                      <circle r={r + 3} fill="none" stroke="#22c55e" strokeWidth={2} opacity={0.8}>
                        <animate attributeName="opacity" values="0.8;0.0" dur="3s" fill="freeze" />
                        <animate attributeName="r" values={`${r + 3};${r + 12}`} dur="3s" fill="freeze" />
                      </circle>
                    )}
                  </>
                )}

                {/* Snapshot freshness arc — broker nodes only */}
                {node.component_type === "broker" && (() => {
                  const info = snapshots?.brokers.find(s => s.broker_id === node.component_id);
                  const ageSec = info?.timestamp != null ? nowSec - info.timestamp : null;
                  const arcR = r + 16;
                  const circ = 2 * Math.PI * arcR;
                  return (
                    <circle
                      r={arcR}
                      fill="none"
                      stroke={freshnessColor(ageSec)}
                      strokeWidth={1.5}
                      strokeDasharray={freshnessDasharray(ageSec, arcR)}
                      strokeDashoffset={circ * 0.25}
                      opacity={0.55}
                      style={{ transform: "rotate(-90deg)" }}
                    />
                  );
                })()}

                {/* Snapshot wave ring — pulses on all brokers when a snapshot completes */}
                {node.component_type === "broker" && snapshotWave && (
                  <circle r={r + 2} fill="none" stroke="#a78bfa" strokeWidth={2} opacity={0}>
                    <animate attributeName="r" values={`${r + 2};${r + 28}`} dur="1.4s" begin="0s" fill="freeze" />
                    <animate attributeName="opacity" values="0;0.75;0" dur="1.4s" begin="0s" fill="freeze" />
                    <animate attributeName="stroke-width" values="2;0.5" dur="1.4s" begin="0s" fill="freeze" />
                  </circle>
                )}

                {/* Label */}
                <text
                  textAnchor="middle"
                  dy="0.35em"
                  fill={color}
                  fontSize={10}
                  fontFamily="var(--font-mono)"
                  fontWeight={600}
                  opacity={isAlive ? 1 : 0.5}
                >
                  {label}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      {/* Legend — minimal, bottom-left */}
      {nodes.length > 0 && (
        <div className="absolute bottom-4 left-4 flex gap-5 text-[10px] font-mono text-muted">
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-broker" /> broker
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-publisher" /> pub
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-subscriber" /> sub
          </span>
        </div>
      )}
    </div>
  );
}
