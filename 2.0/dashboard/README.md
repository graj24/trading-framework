# AGORA Dashboard

Tiny Next.js 15 app that watches the control plane. One page (`/`) shows the
current mode, the service-health pill list, and placeholder cards for PMs and
PRs. It's the operator's "is the platform alive" view, nothing more — real
features land alongside their backend keystones (K3 trading positions, K5 PR
queue, etc.).

Stack: Next 15 (App Router) + React 19 + Tailwind v4 + TanStack Query 5 +
shadcn/ui (`card` + `badge` only, no Radix dependency yet).

## Run it

From `2.0/`:

```bash
make dashboard-install   # one-time: pnpm install --frozen-lockfile
make dashboard           # pnpm dev on http://localhost:3000
```

The control plane must be up at `http://localhost:8000` (`make api` in another
terminal). To point at a different control plane, set
`NEXT_PUBLIC_API_BASE=http://host:port` before `pnpm dev`.

If the control plane is down the page still renders, with explicit error states
on each card. It will recover within ~5s once the API is back (refetch
interval).

## Add a shadcn component

The shadcn registry is at <https://ui.shadcn.com/docs/components>. Pull a new
primitive with:

```bash
pnpm dlx shadcn@latest add <component>
```

Add only what's actually used — the K1 budget for the dashboard is "tool, not
product". `components.json` already pins style=new-york, baseColor=neutral,
icon-library=lucide, so the CLI will drop files into `components/ui/` with the
right shape.
