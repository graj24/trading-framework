import { cn } from "@/lib/cn";

interface CardProps {
  children: React.ReactNode;
  className?: string;
  glow?: "blue" | "green" | "red";
  onClick?: () => void;
}

export function Card({ children, className, glow, onClick }: CardProps) {
  return (
    <div
      onClick={onClick}
      className={cn(
        "panel",
        glow === "blue"  && "glow-blue",
        glow === "green" && "glow-green",
        glow === "red"   && "glow-red",
        onClick && "cursor-pointer hover:border-surface-600 transition-colors",
        className
      )}
    >
      {children}
    </div>
  );
}

interface CardHeaderProps {
  title: string;
  children?: React.ReactNode;
  className?: string;
}

export function CardHeader({ title, children, className }: CardHeaderProps) {
  return (
    <div className={cn("panel-header", className)}>
      <span className="panel-title">{title}</span>
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
}
