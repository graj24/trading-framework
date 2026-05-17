import { Handle, Position } from "reactflow";
import clsx from "clsx";

export interface AgentNodeData {
  label: string;
  icon: string;
  status: "active" | "idle" | "error";
  score?: number;
  color?: string;
}

export function AgentNode({ data }: { data: AgentNodeData }) {
  const borderColor = data.color ?? "#3b82f6";
  const statusColor = data.status === "active" ? "#00d4aa" : data.status === "error" ? "#ff4d4d" : "#4b5563";

  return (
    <div
      className="bg-bg-secondary rounded px-3 py-2 min-w-[110px] text-xs"
      style={{ border: `1px solid ${borderColor}33` }}
    >
      <Handle type="target" position={Position.Left} style={{ background: borderColor, width: 6, height: 6 }} />
      <div className="flex items-center gap-1.5 mb-1">
        <span
          className={clsx("status-dot", data.status === "active" && "active")}
          style={{ background: statusColor }}
        />
        <span className="text-base">{data.icon}</span>
        <span className="font-semibold text-text-primary">{data.label}</span>
      </div>
      {data.score != null && (
        <div className="mono text-text-muted text-[10px]">score: {data.score.toFixed(2)}</div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: borderColor, width: 6, height: 6 }} />
    </div>
  );
}
