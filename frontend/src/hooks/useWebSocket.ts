import { useEffect } from "react";
import { wsClient } from "@/lib/ws";
import { useMarketStore } from "@/store/useMarketStore";
import { useTradeStore } from "@/store/useTradeStore";
import { useAlertStore } from "@/store/useAlertStore";

export function useWebSocket() {
  const setLtp = useMarketStore((s) => s.setLtp);
  const setRegime = useMarketStore((s) => s.setRegime);
  const setPnl = useTradeStore((s) => s.setPnl);
  const pushAlert = useAlertStore((s) => s.push);

  useEffect(() => {
    wsClient.connect();
    const unsub = wsClient.subscribe((event) => {
      if (event.type === "ltp_update") {
        setLtp(event.symbol, event.price, event.change_pct);
      } else if (event.type === "pnl_update") {
        setPnl(event.total_pnl_inr, event.total_pnl_pct);
      } else if (event.type === "alert") {
        pushAlert(event.message, event.severity);
      } else if (event.type === "regime_change") {
        setRegime(event.regime);
      }
    });
    return () => {
      unsub();
      wsClient.disconnect();
    };
  }, []);
}
