import { NavLink } from "react-router-dom";
import {
  LayoutDashboard, GitBranch, Users, FlaskConical,
  RotateCcw, Settings, Server,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { Tooltip } from "@/components/ui/Tooltip";

const NAV = [
  { to: "/",         label: "Terminal",  Icon: LayoutDashboard },
  { to: "/pipeline", label: "Pipeline",  Icon: GitBranch },
  { to: "/pms",      label: "PMs",       Icon: Users },
  { to: "/backtest", label: "Backtest",  Icon: FlaskConical },
  { to: "/replay",   label: "Replay",    Icon: RotateCcw },
  { to: "/setup",    label: "Setup",     Icon: Settings },
  { to: "/infra",    label: "Infra",     Icon: Server },
];

export function Sidebar() {
  return (
    <div className="w-14 bg-surface-950 border-r border-surface-700 flex flex-col items-center py-3 gap-1 shrink-0">
      {NAV.map(({ to, label, Icon }) => (
        <Tooltip key={to} content={label} side="right">
          <NavLink
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cn(
                "w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-150",
                isActive
                  ? "bg-accent-primary/20 text-accent-primary shadow-[0_0_12px_rgba(59,130,246,0.2)]"
                  : "text-content-muted hover:text-content-primary hover:bg-surface-700"
              )
            }
          >
            <Icon size={16} strokeWidth={1.75} />
          </NavLink>
        </Tooltip>
      ))}
    </div>
  );
}
