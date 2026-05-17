import { create } from "zustand";

interface MarketState {
  regime: string;
  vix: number | null;
  sectors: Record<string, number | null>;
  ltpMap: Record<string, { price: number; change_pct: number }>;
  setRegime: (r: string) => void;
  setLtp: (symbol: string, price: number, change_pct: number) => void;
  setSectors: (s: Record<string, number | null>) => void;
}

export const useMarketStore = create<MarketState>((set) => ({
  regime: "unknown",
  vix: null,
  sectors: {},
  ltpMap: {},
  setRegime: (regime) => set({ regime }),
  setLtp: (symbol, price, change_pct) =>
    set((s) => ({ ltpMap: { ...s.ltpMap, [symbol]: { price, change_pct } } })),
  setSectors: (sectors) => set({ sectors }),
}));
