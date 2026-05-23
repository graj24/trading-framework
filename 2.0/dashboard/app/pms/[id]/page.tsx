import Link from "next/link";

import { PMDetail } from "@/components/pm-detail";

// Server shell — Next.js 15 app router types params as a Promise. The
// live data fetching (PM record, journal, mode + mutations) lives in
// PMDetail so TanStack Query polling stays scoped to one subtree.
export default async function PMDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-6 p-6">
      <Link
        href="/"
        className="text-sm text-muted-foreground hover:text-foreground"
      >
        ← Overview
      </Link>
      <PMDetail id={id} />
    </main>
  );
}
