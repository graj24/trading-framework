export const fmt = {
  inr: (v?: number | null) =>
    v == null ? "—" : "₹" + v.toLocaleString("en-IN", { maximumFractionDigits: 2 }),
  pct: (v?: number | null) =>
    v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%",
  num: (v?: number | null, d = 2) =>
    v == null ? "—" : v.toLocaleString("en-IN", { maximumFractionDigits: d }),
  date: (s?: string | null) =>
    s ? new Date(s).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "2-digit" }) : "—",
  time: () =>
    new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }),
};

export const colorPnl = (v?: number | null) =>
  v == null ? "text-text-secondary" : v >= 0 ? "text-green" : "text-red";
