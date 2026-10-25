import { cn } from "@/lib/utils";
import { IDENTITY_OUTCOME, ROW_STATUS, RUN_STATUS, SHOWS_SIGNAL_LEVEL, SIGNAL_LEVEL, TONE_CLASSES, TONE_DOT, type Tone } from "@/lib/status";
import type { IdentityOutcome, RowStatus, RunStatus, SignalLevel } from "@/lib/types";

export function StatusChip({
  status,
  kind = "run",
  outcome,
  level,
}: {
  status: RunStatus | RowStatus;
  kind?: "run" | "row";
  /** needs_input only: why identity failed, shown instead of "Needs input". */
  outcome?: IdentityOutcome | null;
  /** Lists: how much the research found, shown instead of the status once research is done. */
  level?: SignalLevel | null;
}) {
  const meta =
    kind === "row"
      ? ROW_STATUS[status as RowStatus]
      : level && SHOWS_SIGNAL_LEVEL.includes(status as RunStatus)
        ? SIGNAL_LEVEL[level]
        : status === "needs_input" && outcome
          ? IDENTITY_OUTCOME[outcome]
          : RUN_STATUS[status as RunStatus];
  return <Chip label={meta.label} tone={meta.tone} />;
}

/** A coloured pill with a dot: every status-like label in the app looks the same. */
export function Chip({ label, tone, title }: { label: string; tone: Tone; title?: string }) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold whitespace-nowrap ring-1 ring-inset",
        TONE_CLASSES[tone],
      )}
    >
      <span className={cn("size-1.5 rounded-full", TONE_DOT[tone])} />
      {label}
    </span>
  );
}
