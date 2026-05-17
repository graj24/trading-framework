import { create } from "zustand";

export interface Alert {
  id: string;
  message: string;
  severity: "info" | "warn" | "error";
  ts: number;
}

interface AlertState {
  alerts: Alert[];
  push: (message: string, severity?: Alert["severity"]) => void;
  dismiss: (id: string) => void;
}

export const useAlertStore = create<AlertState>((set) => ({
  alerts: [],
  push: (message, severity = "info") =>
    set((s) => ({
      alerts: [
        { id: crypto.randomUUID(), message, severity, ts: Date.now() },
        ...s.alerts.slice(0, 9),
      ],
    })),
  dismiss: (id) => set((s) => ({ alerts: s.alerts.filter((a) => a.id !== id) })),
}));
