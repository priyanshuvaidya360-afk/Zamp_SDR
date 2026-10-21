import { cn } from "@/lib/utils";
import { ROW_STATUS, RUN_STATUS, TONE_CLASSES, TONE_DOT } from "@/lib/status";
import type { RowStatus, RunStatus } from "@/lib/types";

export function StatusChip({ status, kind = "run" }: { status: RunStatus | RowStatus; kind?: "run" | "row" }) {
  const meta = kind === "row" ? ROW_STATUS[status as RowStatus] : RUN_STATUS[status as RunStatus];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold whitespace-nowrap ring-1 ring-inset",
        TONE_CLASSES[meta.tone],
      )}
    >
      <span className={cn("size-1.5 rounded-full", TONE_DOT[meta.tone])} />
      {meta.label}
    </span>
  );
}
