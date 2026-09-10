import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const OUTCOME_STYLE: Record<string, string> = {
  success: "bg-success/12 text-success border-success/30",
  recoverable_then_success: "bg-success/12 text-success border-success/30",
  business_outcome: "bg-warning/12 text-warning border-warning/30",
  hard_failure: "bg-destructive/12 text-destructive border-destructive/30",
};

const RUN_STYLE: Record<string, string> = {
  completed: "bg-success/12 text-success border-success/30",
  running: "bg-primary/12 text-primary border-primary/30",
  pending: "bg-muted text-muted-foreground",
  stuck: "bg-warning/12 text-warning border-warning/30",
  business_outcome: "bg-warning/12 text-warning border-warning/30",
  failed: "bg-destructive/12 text-destructive border-destructive/30",
  dead_end: "bg-destructive/12 text-destructive border-destructive/30",
};

export function OutcomeBadge({ outcome }: { outcome?: string | null }) {
  if (!outcome) return null;
  return (
    <Badge
      variant="outline"
      className={cn("font-medium", OUTCOME_STYLE[outcome] ?? "")}
    >
      {outcome.replaceAll("_", " ")}
    </Badge>
  );
}

export function StatusBadge({ status }: { status?: string | null }) {
  if (!status) return null;
  return (
    <Badge
      variant="outline"
      className={cn("font-medium", RUN_STYLE[status] ?? "")}
    >
      {status.replaceAll("_", " ")}
    </Badge>
  );
}

export function RiskBadge({ risk }: { risk?: string | null }) {
  if (!risk) return null;
  const risky = risk === "risky_irreversible";
  return (
    <Badge
      variant="outline"
      className={cn(
        "font-medium",
        risky
          ? "bg-warning/12 text-warning border-warning/30"
          : "bg-muted text-muted-foreground",
      )}
    >
      {risk.replaceAll("_", " ")}
    </Badge>
  );
}
