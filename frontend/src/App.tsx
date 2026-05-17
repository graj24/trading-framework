import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TopBar } from "@/components/layout/TopBar";
import { Sidebar } from "@/components/layout/Sidebar";
import { AlertBanner } from "@/components/layout/AlertBanner";
import { Terminal } from "@/pages/Terminal";
import { Pipeline } from "@/pages/Pipeline";
import { Backtest } from "@/pages/Backtest";
import { Setup } from "@/pages/Setup";
import { Replay } from "@/pages/Replay";
import { Infra } from "@/pages/Infra";
import { PMs } from "@/pages/PMs";
import { useWebSocket } from "@/hooks/useWebSocket";

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 30000 } } });

function AppShell() {
  useWebSocket();
  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <TopBar />
      <AlertBanner />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-hidden bg-bg-primary">
          <Routes>
            <Route path="/" element={<Terminal />} />
            <Route path="/pipeline" element={<Pipeline />} />
            <Route path="/backtest" element={<Backtest />} />
            <Route path="/replay" element={<Replay />} />
            <Route path="/setup" element={<Setup />} />
            <Route path="/infra" element={<Infra />} />
            <Route path="/pms" element={<PMs />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <AppShell />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
