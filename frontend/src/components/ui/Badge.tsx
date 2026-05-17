import { cn } from "@/lib/cn";

type BadgeVariant = "default" | "success" | "danger" | "warning" | "info" | "purple";

const variants: Record<BadgeVariant, string> = {
  default: "bg-surface-700 text-content-secondary border-surface-600",
  success: "bg-accent-success/15 text-accent-success border-accent-success/30",
  danger:  "bg-accent-danger/15 text-accent-danger border-accent-danger/30",
  warning: "bg-accent-warning/15 text-accent-warning border-accent-warning/30",
  info:    "bg-accent-info/15 text-accent-info border-accent-info/30",
  purple:  "bg-purple-500/15 text-purple-400 border-purple-500/30",
};

interface BadgeProps {
  children: React.ReactNode;
  variant?: BadgeVariant;
  dot?: boolean;
  className?: string;
}

export function Badge({ children, variant = "default", dot, className }: BadgeProps) {
  return (
    <span className={cn("pill border", variants[variant], className)}>
      {dot && (
        <span className={cn(
          "w-1.5 h-1.5 rounded-full",
          variant === "success" ? "bg-accent-success" :
          variant === "danger"  ? "bg-accent-danger" :
          variant === "warning" ? "bg-accent-warning" :
          variant === "info"    ? "bg-accent-info" :
          variant === "purple"  ? "bg-purple-400" :
          "bg-content-muted"
        )} />
      )}
      {children}
    </span>
  );
}
