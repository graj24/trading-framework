import { useQuery } from "@tanstack/react-query";
import { api, InfraStatus } from "@/lib/api";

function StatusDot({ status }: { status: string }) {
  const ok = status === "active" || status === "running";
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full mr-2 ${ok ? "bg-green-400" : "bg-red-400"}`}
    />
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-bg-secondary border border-border rounded p-4">
      <div className="text-[10px] text-text-muted tracking-widest mb-3 uppercase">{title}</div>
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between items-center py-1 border-b border-border/40 last:border-0">
      <span className="text-xs text-text-muted">{label}</span>
      <span className="text-xs text-text-primary font-mono">{value}</span>
    </div>
  );
}

function MemBar({ used, total }: { used: number; total: number }) {
  const pct = total > 0 ? Math.round((used / total) * 100) : 0;
  const color = pct > 85 ? "bg-red-400" : pct > 65 ? "bg-yellow-400" : "bg-green-400";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-bg-tertiary rounded overflow-hidden">
        <div className={`h-full ${color} rounded`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono text-text-primary w-10 text-right">{pct}%</span>
    </div>
  );
}

export function Infra() {
  const { data, isLoading, error, dataUpdatedAt } = useQuery<InfraStatus>({
    queryKey: ["infra"],
    queryFn: api.infra,
    refetchInterval: 30_000,
  });

  if (isLoading)
    return (
      <div className="p-6 text-text-muted text-sm animate-pulse">Loading infra status…</div>
    );

  if (error || !data)
    return (
      <div className="p-6 text-red-400 text-sm">
        Failed to load infra status. Is the API running?
      </div>
    );

  const memPct =
    data.system.memory.total_mb > 0
      ? Math.round((data.system.memory.used_mb / data.system.memory.total_mb) * 100)
      : 0;

  return (
    <div className="p-4 overflow-y-auto h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-sm font-bold tracking-widest text-text-primary uppercase">
          Infrastructure
        </h1>
        <span className="text-[10px] text-text-muted font-mono">
          Updated {new Date(dataUpdatedAt).toLocaleTimeString()} · auto-refresh 30s
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {/* Trading EC2 */}
        <Card title="Trading EC2">
          <Row label="Instance ID" value={data.instance.id} />
          <Row label="Type" value={data.instance.type} />
          <Row label="Public IP" value={data.instance.public_ip} />
          <Row label="Region" value={data.instance.region} />
          <Row label="Uptime" value={data.system.uptime} />
          <Row
            label="Load (1/5/15m)"
            value={data.system.load.join(" / ")}
          />
        </Card>

        {/* Services */}
        <Card title="Services">
          {Object.entries(data.services).map(([name, status]) => (
            <div
              key={name}
              className="flex justify-between items-center py-1 border-b border-border/40 last:border-0"
            >
              <span className="text-xs text-text-muted">{name}</span>
              <span className="text-xs font-mono flex items-center">
                <StatusDot status={status} />
                {status}
              </span>
            </div>
          ))}
          <div className="flex justify-between items-center py-1 border-b border-border/40 last:border-0">
            <span className="text-xs text-text-muted">multica-daemon</span>
            <span className="text-xs font-mono flex items-center">
              <StatusDot status={data.multica.status} />
              {data.multica.status}
            </span>
          </div>
        </Card>

        {/* Resources */}
        <Card title="Resources">
          <div className="mb-3">
            <div className="flex justify-between mb-1">
              <span className="text-xs text-text-muted">Memory</span>
              <span className="text-xs font-mono text-text-primary">
                {data.system.memory.used_mb} / {data.system.memory.total_mb} MB
              </span>
            </div>
            <MemBar used={data.system.memory.used_mb} total={data.system.memory.total_mb} />
          </div>
          <div>
            <div className="flex justify-between mb-1">
              <span className="text-xs text-text-muted">Disk (/)</span>
              <span className="text-xs font-mono text-text-primary">
                {data.system.disk.used} / {data.system.disk.total} ({data.system.disk.pct})
              </span>
            </div>
            <div className="flex-1 h-1.5 bg-bg-tertiary rounded overflow-hidden">
              <div
                className={`h-full rounded ${
                  parseInt(data.system.disk.pct) > 85
                    ? "bg-red-400"
                    : parseInt(data.system.disk.pct) > 65
                    ? "bg-yellow-400"
                    : "bg-green-400"
                }`}
                style={{ width: data.system.disk.pct }}
              />
            </div>
          </div>
        </Card>

        {/* Multica */}
        <Card title="Multica">
          <Row
            label="Server"
            value={
              <a
                href="http://13.232.42.85:3000"
                target="_blank"
                rel="noreferrer"
                className="text-blue-400 hover:underline"
              >
                13.232.42.85:3000 ↗
              </a>
            }
          />
          <Row label="Backend" value="13.232.42.85:8080" />
          <Row label="Daemon" value={
            <span className="flex items-center">
              <StatusDot status={data.multica.status} />
              {data.multica.status}
            </span>
          } />
          <Row label="Connected agents" value={data.multica.agents || "—"} />
          <Row label="Workspaces" value={data.multica.workspaces || "—"} />
        </Card>

        {/* Deploy */}
        <Card title="Deployment">
          <Row label="Branch" value={data.deploy.branch} />
          <Row label="Commit" value={data.deploy.commit} />
          <Row
            label="GitHub"
            value={
              <a
                href="https://github.com/graj24/trading-framework"
                target="_blank"
                rel="noreferrer"
                className="text-blue-400 hover:underline"
              >
                graj24/trading-framework ↗
              </a>
            }
          />
          <Row label="Auto-deploy" value="GitHub Actions → main" />
        </Card>

        {/* Static infra reference */}
        <Card title="Multica EC2">
          <Row label="Instance ID" value="i-011ce82a396c7bbe0" />
          <Row label="Type" value="t3.small" />
          <Row label="Public IP" value="13.232.42.85" />
          <Row label="Region" value="ap-south-1" />
          <Row
            label="Services"
            value="frontend · backend · postgres"
          />
        </Card>
      </div>
    </div>
  );
}
