// Status colours, defined once and used everywhere (docs/GUIDE.md "UI: three screens", Style):
// green = ready / approved, amber = needs input / low signal (waiting on a human),
// red = blocked / failed, grey = running.

import type { RowStatus, RunStatus } from "./types";

export type Tone = "green" | "amber" | "red" | "grey";

export const TONE_CLASSES: Record<Tone, string> = {
  green: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  amber: "bg-amber-50 text-amber-800 ring-amber-600/25",
  red: "bg-red-50 text-red-700 ring-red-600/20",
  grey: "bg-zinc-100 text-zinc-600 ring-zinc-500/20",
};

export const RUN_STATUS: Record<RunStatus, { label: string; tone: Tone }> = {
  running: { label: "Running", tone: "grey" },
  ready_for_review: { label: "Ready for review", tone: "green" },
  approved: { label: "Approved", tone: "green" },
  needs_input: { label: "Needs input", tone: "amber" },
  low_signal: { label: "Low signal", tone: "amber" },
  held: { label: "Held", tone: "amber" },
  blocked: { label: "Blocked", tone: "red" },
  suppressed: { label: "Suppressed", tone: "red" },
  failed: { label: "Failed", tone: "red" },
  rejected: { label: "Rejected", tone: "grey" },
  invalid: { label: "Invalid", tone: "red" },
  skipped: { label: "Duplicate", tone: "grey" },
};

export const ROW_STATUS: Record<RowStatus, { label: string; tone: Tone }> = {
  valid: { label: "Valid", tone: "green" },
  invalid: { label: "Invalid", tone: "red" },
  skipped: { label: "Duplicate", tone: "grey" },
};
