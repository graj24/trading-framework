import { create } from "zustand";
import type { Trade } from "@/lib/api";

interface TradeState {
  trades: Trade[];
  totalPnlInr: number;
  totalPnlPct: number;
  setTrades: (t: Trade[]) => void;
  setPnl: (inr: number, pct: number) => void;
}

export const useTradeStore = create<TradeState>((set) => ({
  trades: [],
  totalPnlInr: 0,
  totalPnlPct: 0,
  setTrades: (trades) => set({ trades }),
  setPnl: (totalPnlInr, totalPnlPct) => set({ totalPnlInr, totalPnlPct }),
}));
