import { AlertTriangle, Check, Circle, Loader2, MinusCircle, XCircle } from "lucide-react";

import type { StepState } from "@/lib/stages";
import { cn } from "@/lib/utils";

// Spinner while a stage runs, then a tick, warning or cross (the only animation in the app).
export function StepIcon({ state, className }: { state: StepState; className?: string }) {
  const size = cn("size-4 shrink-0", className);
  switch (state) {
    case "running":
      return <Loader2 aria-label="running" className={cn(size, "animate-spin text-primary")} />;
    case "done":
      return (
        <span aria-label="done" className={cn(size, "flex items-center justify-center rounded-full bg-primary text-white")}>
          <Check className="size-3" strokeWidth={3} />
        </span>
      );
    case "warning":
      return <AlertTriangle aria-label="needs attention" className={cn(size, "text-amber-600")} />;
    case "failed":
      return <XCircle aria-label="failed" className={cn(size, "text-red-600")} />;
    case "skipped":
      return <MinusCircle aria-label="skipped" className={cn(size, "text-zinc-300")} />;
    default:
      return <Circle aria-label="pending" className={cn(size, "text-zinc-300")} />;
  }
}
