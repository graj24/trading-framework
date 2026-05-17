import { useState, useCallback } from "react";
import ReactFlow, {
  Background, Controls,
  type Node, type Edge,
} from "reactflow";
import "reactflow/dist/style.css";
import { AgentNode } from "@/components/pipeline/AgentNode";
import { AnimatedEdge } from "@/components/pipeline/AnimatedEdge";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import { Play, RefreshCw } from "lucide-react";

const nodeTypes = { agent: AgentNode };
const edgeTypes = { animated: AnimatedEdge };

const NODES: Node[] = [
  { id: "data",     type: "agent", position: { x: 0,   y: 0   }, data: { label: "DataAgent",     icon: "📊", status: "idle", color: "#3b82f6" } },
  { id: "news",     type: "agent", position: { x: 0,   y: 80  }, data: { label: "NewsAgent",      icon: "📰", status: "idle", color: "#3b82f6" } },
  { id: "pattern",  type: "agent", position: { x: 0,   y: 160 }, data: { label: "PatternAgent",   icon: "📐", status: "idle", color: "#3b82f6" } },
  { id: "regime",   type: "agent", position: { x: 0,   y: 240 }, data: { label: "RegimeAgent",    icon: "🌡", status: "idle", color: "#3b82f6" } },
  { id: "ml_daily", type: "agent", position: { x: 0,   y: 320 }, data: { label: "ML Daily",       icon: "🤖", status: "idle", color: "#8b5cf6" } },
  { id: "ml_intra", type: "agent", position: { x: 0,   y: 400 }, data: { label: "ML Intraday",    icon: "⚡", status: "idle", color: "#8b5cf6" } },
  { id: "earnings", type: "agent", position: { x: 0,   y: 480 }, data: { label: "EarningsAgent",  icon: "📅", status: "idle", color: "#3b82f6" } },
  { id: "master",   type: "agent", position: { x: 260, y: 220 }, data: { label: "MasterAgent",    icon: "👑", status: "idle", color: "#f59e0b" } },
  { id: "llm",      type: "agent", position: { x: 480, y: 160 }, data: { label: "LLM",            icon: "🧠", status: "idle", color: "#8b5cf6" } },
  { id: "risk",     type: "agent", position: { x: 480, y: 280 }, data: { label: "RiskManager",    icon: "🛡", status: "idle", color: "#f97316" } },
  { id: "exec",     type: "agent", position: { x: 700, y: 220 }, data: { label: "ExecutionAgent", icon: "⚙", status: "idle", color: "#10b981" } },
  { id: "db",       type: "agent", position: { x: 900, y: 160 }, data: { label: "SQLite DB",      icon: "🗄", status: "idle", color: "#6b7280" } },
  { id: "learning", type: "agent", position: { x: 900, y: 280 }, data: { label: "LearningAgent",  icon: "📈", status: "idle", color: "#10b981" } },
];

const BASE_EDGES: Edge[] = [
  { id: "e1",  source: "data",     target: "master",   type: "animated", data: { active: false } },
  { id: "e2",  source: "news",     target: "master",   type: "animated", data: { active: false } },
  { id: "e3",  source: "pattern",  target: "master",   type: "animated", data: { active: false } },
  { id: "e4",  source: "regime",   target: "master",   type: "animated", data: { active: false } },
  { id: "e5",  source: "ml_daily", target: "master",   type: "animated", data: { active: false } },
  { id: "e6",  source: "ml_intra", target: "master",   type: "animated", data: { active: false } },
  { id: "e7",  source: "earnings", target: "master",   type: "animated", data: { active: false } },
  { id: "e8",  source: "master",   target: "llm",      type: "animated", data: { active: false } },
  { id: "e9",  source: "master",   target: "risk",     type: "animated", data: { active: false } },
  { id: "e10", source: "llm",      target: "exec",     type: "animated", data: { active: false } },
  { id: "e11", source: "risk",     target: "exec",     type: "animated", data: { active: false } },
  { id: "e12", source: "exec",     target: "db",       type: "animated", data: { active: false } },
  { id: "e13", source: "exec",     target: "learning", type: "animated", data: { active: false } },
  { id: "e14", source: "learning", target: "master",   type: "animated", data: { active: false } },
];

export function Pipeline() {
  const [edges, setEdges] = useState(BASE_EDGES);
  const [symbol, setSymbol] = useState("RELIANCE");
  const [running, setRunning] = useState(false);
  const [selected, setSelected] = useState<Node | null>(null);

  const animatePipeline = useCallback(() => {
    const order = ["e1","e2","e3","e4","e5","e6","e7","e8","e9","e10","e11","e12","e13","e14"];
    order.forEach((id, i) => {
      setTimeout(() => {
        setEdges((es) => es.map((e) => e.id === id ? { ...e, data: { active: true } } : e));
        setTimeout(() => setEdges((es) => es.map((e) => e.id === id ? { ...e, data: { active: false } } : e)), 1500);
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
    <div className="flex h-full overflow-hidden bg-surface-900">
      {/* Canvas */}
      <div className="flex-1 relative">
        {/* Floating controls */}
        <div className="absolute top-3 left-3 z-10 flex items-center gap-2">
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="bg-surface-800 border border-surface-700 rounded-lg px-3 py-1.5 text-xs text-content-primary w-28 font-mono focus:outline-none focus:border-accent-primary"
            placeholder="SYMBOL"
          />
          <button
            onClick={handleRun}
            disabled={running}
            className={cn("btn-sm flex items-center gap-1.5", running ? "btn-ghost" : "btn-primary")}
          >
            {running ? <RefreshCw size={11} className="animate-spin" /> : <Play size={11} />}
            {running ? "Running…" : "Run Pipeline"}
          </button>
          {running && <Badge variant="warning" dot>Live</Badge>}
        </div>

        <ReactFlow
          nodes={NODES}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodeClick={(_, node) => setSelected(node)}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#1f2937" gap={20} />
          <Controls />
        </ReactFlow>
      </div>

      {/* Detail panel */}
      <div className="w-60 border-l border-surface-700 bg-surface-900 flex flex-col shrink-0">
        <div className="panel-header border-b border-surface-700">
          <span className="panel-title">Node Detail</span>
        </div>
        {selected ? (
          <div className="p-4 flex flex-col gap-3">
            <div className="text-3xl">{(selected.data as any).icon}</div>
            <div>
              <div className="text-sm font-bold text-content-primary">{(selected.data as any).label}</div>
              <Badge variant="default" className="mt-1">{(selected.data as any).status}</Badge>
            </div>
            {(selected.data as any).score != null && (
              <div className="num text-xs text-content-secondary">Score: {(selected.data as any).score}</div>
            )}
            <p className="text-xs text-content-muted leading-relaxed">
              Click "Run Pipeline" to trigger a live analysis cycle and watch signals flow through this node.
            </p>
          </div>
        ) : (
          <div className="p-4 text-xs text-content-muted">Click any node to inspect it</div>
        )}
      </div>
    </div>
  );
}
