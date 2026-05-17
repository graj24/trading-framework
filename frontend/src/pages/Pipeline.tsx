import { useState, useCallback } from "react";
import ReactFlow, {
  Background, Controls, MiniMap,
  type Node, type Edge,
} from "reactflow";
import "reactflow/dist/style.css";
import { AgentNode } from "@/components/pipeline/AgentNode";
import { AnimatedEdge } from "@/components/pipeline/AnimatedEdge";
import { api } from "@/lib/api";

const nodeTypes = { agent: AgentNode };
const edgeTypes = { animated: AnimatedEdge };

const NODES: Node[] = [
  // Data sources
  { id: "data",     type: "agent", position: { x: 0,   y: 0   }, data: { label: "DataAgent",     icon: "📊", status: "idle", color: "#3b82f6" } },
  { id: "news",     type: "agent", position: { x: 0,   y: 80  }, data: { label: "NewsAgent",      icon: "📰", status: "idle", color: "#3b82f6" } },
  { id: "pattern",  type: "agent", position: { x: 0,   y: 160 }, data: { label: "PatternAgent",   icon: "📐", status: "idle", color: "#3b82f6" } },
  { id: "regime",   type: "agent", position: { x: 0,   y: 240 }, data: { label: "RegimeAgent",    icon: "🌡", status: "idle", color: "#3b82f6" } },
  { id: "ml_daily", type: "agent", position: { x: 0,   y: 320 }, data: { label: "ML Daily",       icon: "🤖", status: "idle", color: "#8b5cf6" } },
  { id: "ml_intra", type: "agent", position: { x: 0,   y: 400 }, data: { label: "ML Intraday",    icon: "⚡", status: "idle", color: "#8b5cf6" } },
  { id: "earnings", type: "agent", position: { x: 0,   y: 480 }, data: { label: "EarningsAgent",  icon: "📅", status: "idle", color: "#3b82f6" } },
  // Master
  { id: "master",   type: "agent", position: { x: 260, y: 220 }, data: { label: "MasterAgent",    icon: "👑", status: "idle", color: "#f59e0b" } },
  // LLM + Rules
  { id: "llm",      type: "agent", position: { x: 480, y: 160 }, data: { label: "LLM",            icon: "🧠", status: "idle", color: "#8b5cf6" } },
  { id: "risk",     type: "agent", position: { x: 480, y: 280 }, data: { label: "RiskManager",    icon: "🛡", status: "idle", color: "#f97316" } },
  // Execution
  { id: "exec",     type: "agent", position: { x: 700, y: 220 }, data: { label: "ExecutionAgent", icon: "⚙", status: "idle", color: "#00d4aa" } },
  // Post-trade
  { id: "db",       type: "agent", position: { x: 900, y: 160 }, data: { label: "SQLite DB",      icon: "🗄", status: "idle", color: "#4b5563" } },
  { id: "learning", type: "agent", position: { x: 900, y: 280 }, data: { label: "LearningAgent",  icon: "📈", status: "idle", color: "#00d4aa" } },
];

const EDGES: Edge[] = [
  { id: "e1",  source: "data",     target: "master",  type: "animated", data: { active: false } },
  { id: "e2",  source: "news",     target: "master",  type: "animated", data: { active: false } },
  { id: "e3",  source: "pattern",  target: "master",  type: "animated", data: { active: false } },
  { id: "e4",  source: "regime",   target: "master",  type: "animated", data: { active: false } },
  { id: "e5",  source: "ml_daily", target: "master",  type: "animated", data: { active: false } },
  { id: "e6",  source: "ml_intra", target: "master",  type: "animated", data: { active: false } },
  { id: "e7",  source: "earnings", target: "master",  type: "animated", data: { active: false } },
  { id: "e8",  source: "master",   target: "llm",     type: "animated", data: { active: false } },
  { id: "e9",  source: "master",   target: "risk",    type: "animated", data: { active: false } },
  { id: "e10", source: "llm",      target: "exec",    type: "animated", data: { active: false } },
  { id: "e11", source: "risk",     target: "exec",    type: "animated", data: { active: false } },
  { id: "e12", source: "exec",     target: "db",      type: "animated", data: { active: false } },
  { id: "e13", source: "exec",     target: "learning",type: "animated", data: { active: false } },
  { id: "e14", source: "learning", target: "master",  type: "animated", data: { active: false } },
];

export function Pipeline() {
  const [nodes, setNodes] = useState(NODES);
  const [edges, setEdges] = useState(EDGES);
  const [symbol, setSymbol] = useState("RELIANCE");
  const [running, setRunning] = useState(false);
  const [selected, setSelected] = useState<Node | null>(null);

  const animatePipeline = useCallback(() => {
    // Animate edges sequentially
    const order = ["e1","e2","e3","e4","e5","e6","e7","e8","e9","e10","e11","e12","e13","e14"];
    order.forEach((id, i) => {
      setTimeout(() => {
        setEdges((es) => es.map((e) => e.id === id ? { ...e, data: { active: true } } : e));
        setTimeout(() => {
          setEdges((es) => es.map((e) => e.id === id ? { ...e, data: { active: false } } : e));
        }, 1500);
      }, i * 400);
    });
  }, []);

  const handleRun = useCallback(async () => {
    setRunning(true);
    animatePipeline();
    await api.runSignal(symbol);
    setTimeout(() => setRunning(false), 8000);
  }, [symbol, animatePipeline]);

  return (
    <div className="flex h-full overflow-hidden">
      {/* Flow canvas */}
      <div className="flex-1 relative">
        {/* Controls bar */}
        <div className="absolute top-3 left-3 z-10 flex gap-2 items-center">
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="bg-bg-secondary border border-border rounded px-2 py-1 text-xs text-text-primary w-28 mono"
            placeholder="SYMBOL"
          />
          <button
            onClick={handleRun}
            disabled={running}
            className="bg-blue/20 border border-blue/40 text-blue text-xs px-3 py-1 rounded hover:bg-blue/30 disabled:opacity-40"
          >
            {running ? "Running…" : "▶ Run for symbol"}
          </button>
        </div>

        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodeClick={(_, node) => setSelected(node)}
          fitView
          style={{ background: "#0a0e17" }}
        >
          <Background color="#1f2937" gap={20} />
          <Controls style={{ background: "#111827", border: "1px solid #1f2937" }} />
          <MiniMap style={{ background: "#111827" }} nodeColor="#1f2937" />
        </ReactFlow>
      </div>

      {/* Detail panel */}
      <div className="w-64 border-l border-border bg-bg-secondary flex flex-col shrink-0">
        <div className="px-3 py-1.5 border-b border-border text-xs text-text-secondary font-semibold tracking-wider">
          NODE DETAIL
        </div>
        {selected ? (
          <div className="p-3 flex flex-col gap-2 text-xs">
            <div className="text-2xl">{(selected.data as any).icon}</div>
            <div className="font-bold text-text-primary text-sm">{(selected.data as any).label}</div>
            <div className="flex items-center gap-1.5">
              <span
                className={`status-dot ${(selected.data as any).status === "active" ? "active" : ""}`}
                style={{ background: (selected.data as any).status === "active" ? "#00d4aa" : "#4b5563" }}
              />
              <span className="text-text-muted capitalize">{(selected.data as any).status}</span>
            </div>
            {(selected.data as any).score != null && (
              <div className="mono text-text-secondary">Score: {(selected.data as any).score}</div>
            )}
            <div className="text-text-muted mt-2 leading-relaxed">
              Click "Run for symbol" to trigger a live analysis cycle and watch data flow through this node.
            </div>
          </div>
        ) : (
          <div className="p-3 text-xs text-text-muted">Click any node to see details</div>
        )}
      </div>
    </div>
  );
}
