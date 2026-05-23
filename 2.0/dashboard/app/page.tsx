import { DashboardOverview } from "@/components/dashboard-overview";

// Server component — only renders the chrome. Live data lives in the client
// child so TanStack Query polling stays scoped to one subtree.
export default function HomePage() {
  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-6 p-6">
      <DashboardOverview />
    </main>
  );
}
