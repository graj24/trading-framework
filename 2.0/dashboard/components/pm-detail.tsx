"use client";

// PM detail view (Step 2.4). Three TanStack queries (PM record, journal,
// mode) auto-refresh at the providers' 5s default. The mutations call
// the existing /stop /pause /resume endpoints from Step 2.3 and
// invalidate the PM record on success so the status badge updates
// without waiting for the next poll tick.
//
// Disabled-state rules per the plan:
//   - Stop:   enabled if status != "stopped"
//   - Pause:  enabled if status == "running"
//   - Resume: enabled if status == "paused"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Circle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  fetchJournal,
  fetchMode,
  fetchPM,
  pausePM,
  resumePM,
  stopPM,
  type JournalResponse,
  type ModeResponse,
  type PMRecord,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_DOT: Record<string, string> = {
  running: "text-green-500",
  paused: "text-yellow-500",
  stopped: "text-zinc-400",
  error: "text-red-500",
  spawned: "text-blue-500",
  provisioning: "text-blue-300",
};

const MODE_LABEL: Record<string, string> = {
  build: "build",
  trading: "trading",
  pre_trade_freeze: "pre-trade freeze",
};

export function PMDetail({ id }: { id: string }) {
  const qc = useQueryClient();

  const pm = useQuery<PMRecord>({
    queryKey: ["pm", id],
    queryFn: ({ signal }) => fetchPM(id, signal),
  });
  const journal = useQuery<JournalResponse>({
    queryKey: ["pm", id, "journal"],
    queryFn: ({ signal }) => fetchJournal(id, 50, signal),
  });
  const mode = useQuery<ModeResponse>({
    queryKey: ["mode"],
    queryFn: ({ signal }) => fetchMode(signal),
  });

  // Each mutation invalidates the PM record so the displayed status
  // refreshes immediately. The journal and mode keep their poll
  // cadence; nothing in those changes synchronously with a control
  // signal.
  const stopMut = useMutation({
    mutationFn: () => stopPM(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pm", id] }),
  });
  const pauseMut = useMutation({
    mutationFn: () => pausePM(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pm", id] }),
  });
  const resumeMut = useMutation({
    mutationFn: () => resumePM(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pm", id] }),
  });

  if (pm.isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>PM {id}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-sm text-destructive">
            <AlertTriangle className="h-4 w-4" />
            Could not load PM. Will retry every 5s.
          </div>
        </CardContent>
      </Card>
    );
  }
  if (!pm.data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>PM {id}</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Loading...
        </CardContent>
      </Card>
    );
  }

  const status = pm.data.status;
  const modeText = mode.data
    ? (MODE_LABEL[mode.data.mode] ?? mode.data.mode)
    : "—";
  // Buttons reflect the most recent known status. After a successful
  // mutation, TanStack invalidates the PM query and the badges and
  // disabled states snap to the new value on the next render.
  const canStop = status !== "stopped" && !stopMut.isPending;
  const canPause = status === "running" && !pauseMut.isPending;
  const canResume = status === "paused" && !resumeMut.isPending;

  // Most recent error from any mutation — show inline so the user
  // doesn't have to inspect the network tab.
  const mutationError =
    stopMut.error ?? pauseMut.error ?? resumeMut.error ?? null;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
          <div className="space-y-1.5">
            <CardTitle className="text-2xl">{pm.data.name}</CardTitle>
            <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
              <span>
                Spawned: {new Date(pm.data.spawned_at).toLocaleString()}
              </span>
              <span>•</span>
              <span>
                Capital: ₹
                {pm.data.starting_capital_inr.toLocaleString("en-IN")}
              </span>
              {pm.data.workflow_id && (
                <>
                  <span>•</span>
                  <span>Workflow: {pm.data.workflow_id}</span>
                </>
              )}
            </div>
          </div>
          <div className="flex flex-col items-end gap-1">
            <Badge variant="outline" className="gap-1.5 font-normal">
              <Circle
                aria-hidden
                className={cn(
                  "h-2 w-2 fill-current",
                  STATUS_DOT[status] ?? "text-zinc-400",
                )}
              />
              {status}
            </Badge>
            <Badge variant="secondary" className="font-normal">
              ● {modeText}
            </Badge>
          </div>
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Controls</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="destructive"
              disabled={!canStop}
              onClick={() => stopMut.mutate()}
            >
              Stop
            </Button>
            <Button
              variant="secondary"
              disabled={!canPause}
              onClick={() => pauseMut.mutate()}
            >
              Pause
            </Button>
            <Button
              variant="default"
              disabled={!canResume}
              onClick={() => resumeMut.mutate()}
            >
              Resume
            </Button>
          </div>
          {mutationError && (
            <div className="mt-3 flex items-center gap-2 text-sm text-destructive">
              <AlertTriangle className="h-4 w-4" />
              {(mutationError as Error).message}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Journal — last 50 entries</CardTitle>
        </CardHeader>
        <CardContent>
          {journal.isError ? (
            <div className="flex items-center gap-2 text-sm text-destructive">
              <AlertTriangle className="h-4 w-4" />
              Could not load journal.
            </div>
          ) : !journal.data ? (
            <div className="text-sm text-muted-foreground">
              Loading journal...
            </div>
          ) : journal.data.lines.length === 0 ? (
            <div className="text-sm text-muted-foreground">
              No journal entries yet today.
            </div>
          ) : (
            <pre className="max-h-96 overflow-auto rounded-md border bg-muted/40 p-3 text-xs leading-relaxed font-mono">
              {journal.data.lines.join("\n")}
            </pre>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
