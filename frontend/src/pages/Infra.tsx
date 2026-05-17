import { useQuery } from "@tanstack/react-query";
import { api, InfraStatus } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { cn } from "@/lib/cn";
import { ExternalLink, RefreshCw } from "lucide-react";

function StatusBadge({ status }: { status: string }) {
  const ok = status === "active" || status === "running" || status === "connected";
  return <Badge variant={ok ? "success" : "danger"} dot>{status}</Badge>;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-surface-700/50 last:border-0">
      <span className="text-xs text-content-muted">{label}</span>
      <span className="text-xs text-content-secondary font-mono">{value}</span>
    </div>
  );
}

function UsageBar({ pct, label }: { pct: number; label: string }) {
  const color = pct > 85 ? "bg-accent-danger" : pct > 65 ? "bg-accent-warning" : "bg-accent-success";
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex justify-between text-xs">
        <span className="text-content-muted">{label}</span>
        <span className={cn("num font-medium", pct > 85 ? "text-accent-danger" : pct > 65 ? "text-accent-warning" : "text-accent-success")}>{pct}%</span>
      </div>
      <div className="h-1.5 bg-surface-700 rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Infra() {
  const { data, isLoading, error, dataUpdatedAt, refetch } = useQuery<InfraStatus>({
    queryKey: ["infra"],
    queryFn: api.infra,
    refetchInterval: 30_000,
  });

  if (isLoading) return (
    <div className="flex items-center justify-center h-full">
      <div className="flex items-center gap-2 text-content-muted text-sm">
        <RefreshCw size={14} className="animate-spin" /> Loading infra status…
      </div>
    </div>
  );

  if (error || !data) return (
    <div className="flex items-center justify-center h-full">
      <Badge variant="danger">Error: {error instanceof Error ? error.message : "Failed to load"}</Badge>
    </div>
  );

  const memPct = data.system.memory.total_mb > 0
    ? Math.round((data.system.memory.used_mb / data.system.memory.total_mb) * 100) : 0;
  const diskPct = parseInt(data.system.disk.pct) || 0;

  return (
    <div className="p-4 overflow-y-auto h-full bg-surface-900">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-sm font-bold text-content-primary">Infrastructure</h1>
        <div className="flex items-center gap-3">
          <span className="text-xs text-content-muted">
            Updated {new Date(dataUpdatedAt).toLocaleTimeString()}
          </span>
          <button onClick={() => refetch()} className="btn-sm btn-ghost">
            <RefreshCw size={11} /> Refresh
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {/* Trading EC2 */}
        <Card>
          <CardHeader title="Trading EC2" />
          <div className="p-3 flex flex-col gap-0">
            <Row label="Instance ID" value={data.instance.id} />
            <Row label="Type" value={data.instance.type} />
            <Row label="Public IP" value={data.instance.public_ip} />
            <Row label="Region" value={data.instance.region} />
            <Row label="Uptime" value={data.system.uptime} />
            <Row label="Load (1/5/15m)" value={data.system.load.join(" / ")} />
          </div>
        </Card>

        {/* Services */}
        <Card>
          <CardHeader title="Services" />
          <div className="p-3 flex flex-col gap-2">
            {Object.entries(data.services).map(([name, status]) => (
              <div key={name} className="flex justify-between items-center">
                <span className="text-xs text-content-muted">{name}</span>
                <StatusBadge status={status} />
              </div>
            ))}
            <div className="flex justify-between items-center">
              <span className="text-xs text-content-muted">multica-daemon</span>
              <StatusBadge status={data.multica.status} />
            </div>
          </div>
        </Card>

        {/* Resources */}
        <Card>
          <CardHeader title="Resources" />
          <div className="p-3 flex flex-col gap-4">
            <UsageBar pct={memPct} label={`Memory — ${data.system.memory.used_mb} / ${data.system.memory.total_mb} MB`} />
            <UsageBar pct={diskPct} label={`Disk — ${data.system.disk.used} / ${data.system.disk.total}`} />
          </div>
        </Card>

        {/* Multica */}
        <Card>
          <CardHeader title="Multica Platform" />
          <div className="p-3 flex flex-col gap-0">
            <Row label="Board" value={
              <a href="http://13.232.42.85:3000" target="_blank" rel="noreferrer" className="text-accent-primary hover:underline flex items-center gap-1">
                13.232.42.85:3000 <ExternalLink size={10} />
              </a>
            } />
            <Row label="Backend" value="13.232.42.85:8080" />
            <Row label="Daemon" value={<StatusBadge status={data.multica.status} />} />
            <Row label="Agents" value={data.multica.agents || "—"} />
            <Row label="Workspaces" value={data.multica.workspaces || "—"} />
          </div>
        </Card>

        {/* Deploy */}
        <Card>
          <CardHeader title="Deployment" />
          <div className="p-3 flex flex-col gap-0">
            <Row label="Branch" value={data.deploy.branch} />
            <Row label="Commit" value={data.deploy.commit} />
            <Row label="Repo" value={
              <a href="https://github.com/graj24/trading-framework" target="_blank" rel="noreferrer" className="text-accent-primary hover:underline flex items-center gap-1">
                graj24/trading-framework <ExternalLink size={10} />
              </a>
            } />
            <Row label="Auto-deploy" value="GitHub Actions → main" />
          </div>
        </Card>

        {/* Multica EC2 */}
        <Card>
          <CardHeader title="Multica EC2" />
          <div className="p-3 flex flex-col gap-0">
            <Row label="Instance ID" value="i-011ce82a396c7bbe0" />
            <Row label="Type" value="t3.small" />
            <Row label="Public IP" value="13.232.42.85" />
            <Row label="Region" value="ap-south-1" />
            <Row label="Services" value="frontend · backend · postgres" />
          </div>
        </Card>
      </div>
    </div>
  );
}
