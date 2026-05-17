import { NavLink } from "react-router-dom";
import clsx from "clsx";

const NAV = [
  { to: "/", label: "TERMINAL", icon: "⬛" },
  { to: "/pipeline", label: "PIPELINE", icon: "◈" },
  { to: "/pms", label: "PMS", icon: "◎" },
  { to: "/backtest", label: "BACKTEST", icon: "◷" },
  { to: "/replay", label: "REPLAY", icon: "↺" },
  { to: "/setup", label: "SETUP", icon: "⚙" },
  { to: "/infra", label: "INFRA", icon: "⬡" },
];

export function Sidebar() {
  return (
    <div className="w-14 bg-bg-secondary border-r border-border flex flex-col items-center py-2 gap-1 shrink-0">
      <div className="text-blue font-bold text-xs mb-3 tracking-widest" style={{ writingMode: "vertical-rl", transform: "rotate(180deg)" }}>
        KIRO
      </div>
      {NAV.map((n) => (
        <NavLink
          key={n.to}
          to={n.to}
          end={n.to === "/"}
          title={n.label}
          className={({ isActive }) =>
            clsx(
              "w-10 h-10 flex flex-col items-center justify-center rounded text-xs gap-0.5 transition-colors",
              isActive
                ? "bg-blue/20 text-blue border border-blue/40"
                : "text-text-muted hover:text-text-primary hover:bg-bg-tertiary"
            )
          }
        >
          <span className="text-base leading-none">{n.icon}</span>
          <span className="text-[9px] leading-none">{n.label.slice(0, 3)}</span>
        </NavLink>
      ))}
    </div>
  );
}
