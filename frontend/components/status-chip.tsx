import { cn } from "@/lib/utils";
import { ROW_STATUS, RUN_STATUS, TONE_CLASSES } from "@/lib/status";
import type { RowStatus, RunStatus } from "@/lib/types";

export function StatusChip({ status, kind = "run" }: { status: RunStatus | RowStatus; kind?: "run" | "row" }) {
  const meta = kind === "row" ? ROW_STATUS[status as RowStatus] : RUN_STATUS[status as RunStatus];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset",
        TONE_CLASSES[meta.tone],
      )}
    >
      {meta.label}
    </span>
  );
}
