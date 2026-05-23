"use client";

// The single live page in the K1 dashboard. Pulls /api/health, /api/mode,
// /api/pms via TanStack Query at a 5s cadence, renders graceful error states
// when the control plane is unreachable.
//
// Static today, real later:
//   - PRs card is hard-coded "PRs (0)" because there's no /api/prs yet (K5).
//   - Kill-switch pill is hard-coded "off" because there's no /api/kill-switch
//     yet (K3). Both have explicit TODO markers in-file.

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Circle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  fetchHealth,
  fetchMode,
  fetchPMs,
  type HealthResponse,
  type HealthStatus,
  type ModeResponse,
  type PMSummary,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const DOT_COLOR: Record<HealthStatus, string> = {
  ok: "text-green-500",
  degraded: "text-yellow-500",
  down: "text-red-500",
};

const MODE_LABEL: Record<string, string> = {
  build: "BUILD",
  trading: "TRADING",
  pre_trade_freeze: "PRE-TRADE FREEZE",
};

export function DashboardOverview() {
  const health = useQuery<HealthResponse>({
    queryKey: ["health"],
    queryFn: ({ signal }) => fetchHealth(signal),
  });
  const mode = useQuery<ModeResponse>({
    queryKey: ["mode"],
    queryFn: ({ signal }) => fetchMode(signal),
  });
  const pms = useQuery<PMSummary[]>({
    queryKey: ["pms"],
    queryFn: ({ signal }) => fetchPMs(signal),
  });

  const pmCount = pms.data?.length ?? null;
  const modeText = mode.data ? (MODE_LABEL[mode.data.mode] ?? mode.data.mode) : "—";
  // TODO: K3 — wire to /api/kill-switch.
  const killSwitch: "off" | "on" = "off";

  const headerSummary =
    pms.isError || mode.isError
      ? "control plane unreachable"
      : `${pmCount ?? "—"} PMs running, mode: ${
          mode.data ? mode.data.mode : "—"
        }, kill switch: ${killSwitch}`;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">AGORA</h1>
          <p className="text-sm text-muted-foreground">{headerSummary}</p>
        </div>
        <Badge
          variant={killSwitch === "off" ? "secondary" : "destructive"}
          className="text-xs uppercase tracking-wide"
        >
          kill switch: {killSwitch}
        </Badge>
      </header>

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
          <div className="space-y-1.5">
            <CardTitle>Mode: {modeText}</CardTitle>
            <CardDescription>
              {pmCount === null
                ? pms.isError
                  ? "PMs (—) — control plane unreachable"
                  : "Loading PMs..."
                : `${pmCount} PMs running`}
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <div className="text-xs uppercase tracking-wide text-muted-foreground">
            Services
          </div>
          <ServicePills health={health.data} error={health.isError} />
        </CardContent>
      </Card>

      <div className="grid gap-6 md:grid-cols-2">
        <PMsCard pms={pms.data} loading={pms.isPending} error={pms.isError} />
        <PRsCard />
      </div>
    </div>
  );
}

function ServicePills({
  health,
  error,
}: {
  health: HealthResponse | undefined;
  error: boolean;
}) {
  if (error) {
    return (
      <div className="mt-2 flex items-center gap-2 text-sm text-destructive">
        <AlertTriangle className="h-4 w-4" />
        Could not reach control plane at /api/health.
      </div>
    );
  }
  if (!health) {
    return (
      <div className="mt-2 text-sm text-muted-foreground">Loading services...</div>
    );
  }
  const entries = Object.entries(health.services);
  if (entries.length === 0) {
    return (
      <div className="mt-2 text-sm text-muted-foreground">
        No services reported.
      </div>
    );
  }
  return (
    <ul className="mt-2 flex flex-wrap gap-2">
      {entries.map(([name, svc]) => (
        <li key={name}>
          <Badge
            variant="outline"
            className="gap-1.5 font-normal"
            title={svc.detail}
          >
            <Circle
              aria-hidden
              className={cn("h-2 w-2 fill-current", DOT_COLOR[svc.status])}
            />
            <span className="lowercase">{name}</span>
            <span className="text-muted-foreground">{svc.status}</span>
          </Badge>
        </li>
      ))}
    </ul>
  );
}

function PMsCard({
  pms,
  loading,
  error,
}: {
  pms: PMSummary[] | undefined;
  loading: boolean;
  error: boolean;
}) {
  const count = error ? "—" : (pms?.length ?? 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>PMs ({count})</CardTitle>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">
        {error
          ? "Control plane unreachable. Will retry every 5s."
          : loading
            ? "Loading..."
            : (pms?.length ?? 0) === 0
              ? "No PMs yet. Spawn one in K2."
              : (
                  <ul className="space-y-1">
                    {pms!.map((pm) => (
                      <li key={pm.id} className="flex justify-between">
                        <span className="font-medium text-foreground">
                          {pm.name}
                        </span>
                        <span>{pm.status}</span>
                      </li>
                    ))}
                  </ul>
                )}
      </CardContent>
    </Card>
  );
}

function PRsCard() {
  // TODO: K5 — wire to /api/prs once the engineer pipeline ships.
  return (
    <Card>
      <CardHeader>
        <CardTitle>PRs (0)</CardTitle>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">
        PR queue is empty. (Lands in K5.)
      </CardContent>
    </Card>
  );
}
