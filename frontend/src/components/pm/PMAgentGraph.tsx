/**
 * PMAgentGraph — scoped ReactFlow DAG for a single PM's agent pipeline.
 * Nodes pulse when they receive a recent event.
 */
import { useEffect, useState, useCallback } from "react";
import ReactFlow, { Background, type Node, type Edge } from "reactflow";
import "reactflow/dist/style.css";
import type { StreamEvent } from "./ThoughtStream";

// ── Node renderer ─────────────────────────────────────────────────────────────

function AgentNodeInner({ data }: { data: { label: string; icon: string; color: string; active: boolean; lastOutput?: string } }) {
  return (
    <div
      className="flex flex-col items-center gap-1 px-3 py-2 rounded-xl border text-center transition-all duration-300"
      style={{
        background: data.active ? `${data.color}22` : "#111827",
        borderColor: data.active ? data.color : "#1f2937",
        boxShadow: data.active ? `0 0 12px ${data.color}55` : "none",
        minWidth: 80,
      }}
    >
      <span className="text-lg">{data.icon}</span>
      <span className="text-[10px] font-semibold text-content-secondary">{data.label}</span>
      {data.lastOutput && (
        <span className="text-[9px] text-content-muted max-w-[80px] truncate">{data.lastOutput}</span>
      )}
    </div>
  );
}

const nodeTypes = { pm_agent: AgentNodeInner };

// ── Static graph definition ───────────────────────────────────────────────────

const BASE_NODES: Node[] = [
  { id: "triage",  type: "pm_agent", position: { x: 0,   y: 80  }, data: { label: "Triage",  icon: "🔍", color: "#f59e0b", active: false } },
  { id: "master",  type: "pm_agent", position: { x: 160, y: 80  }, data: { label: "Master",  icon: "👑", color: "#f59e0b", active: false } },
  { id: "llm",     type: "pm_agent", position: { x: 320, y: 0   }, data: { label: "LLM",     icon: "🧠", color: "#8b5cf6", active: false } },
  { id: "risk",    type: "pm_agent", position: { x: 320, y: 160 }, data: { label: "Risk",    icon: "🛡", color: "#ef4444", active: false } },
  { id: "trader",  type: "pm_agent", position: { x: 480, y: 80  }, data: { label: "Trader",  icon: "⚙", color: "#10b981", active: false } },
];

const EDGES: Edge[] = [
  { id: "t-m",  source: "triage",  target: "master",  style: { stroke: "#374151" }, animated: false },
  { id: "m-l",  source: "master",  target: "llm",     style: { stroke: "#374151" }, animated: false },
  { id: "m-r",  source: "master",  target: "risk",    style: { stroke: "#374151" }, animated: false },
  { id: "l-tr", source: "llm",     target: "trader",  style: { stroke: "#374151" }, animated: false },
  { id: "r-tr", source: "risk",    target: "trader",  style: { stroke: "#374151" }, animated: false },
];

// Map event topics → which node to pulse
function topicToNode(topic: string): string | null {
  if (topic.includes("triage") || topic.startsWith("price.spike") || topic.startsWith("news")) return "triage";
  if (topic.includes("master") || topic.startsWith("pm.wakeup")) return "master";
  if (topic.includes("llm") || (topic.startsWith("agent.thinking") && (topic.includes("master")))) return "llm";
  if (topic.startsWith("risk")) return "risk";
  if (topic.startsWith("exec_order") || topic.startsWith("fill")) return "trader";
  if (topic.startsWith("agent.thinking")) {
    // Use payload.agent field
    return null; // handled by caller
  }
  return null;
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  lastEvent: StreamEvent | null;
}

export function PMAgentGraph({ lastEvent }: Props) {
  const [nodes, setNodes] = useState(BASE_NODES);

  const pulseNode = useCallback((nodeId: string, output?: string) => {
    setNodes((prev) => prev.map((n) =>
      n.id === nodeId ? { ...n, data: { ...n.data, active: true, lastOutput: output ?? n.data.lastOutput } } : n
    ));
    setTimeout(() => {
      setNodes((prev) => prev.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, active: false } } : n
      ));
    }, 3000);
  }, []);

  useEffect(() => {
    if (!lastEvent) return;
    const { topic, payload } = lastEvent;

    // thinking events carry agent name
    if (topic.startsWith("agent.thinking")) {
      const agent = payload.agent as string;
      const nodeId = agent === "triage" ? "triage" : agent === "master" ? "master" : null;
      if (nodeId) {
        const output = payload.status === "done" ? (payload.output as string ?? payload.decision as string) : undefined;
        pulseNode(nodeId, output);
      }
      return;
    }

    const nodeId = topicToNode(topic);
    if (nodeId) pulseNode(nodeId);
  }, [lastEvent, pulseNode]);

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={EDGES}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        panOnDrag={false}
      >
        <Background color="#1f2937" gap={20} />
      </ReactFlow>
    </div>
  );
}
