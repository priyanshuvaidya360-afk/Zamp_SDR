// Status colours, defined once and used everywhere (docs/GUIDE.md "UI: three screens", Style):
// green = ready / approved, amber = needs input / low signal (waiting on a human),
// red = blocked / failed, grey = running.

import type { RowStatus, RunStatus } from "./types";

export type Tone = "green" | "amber" | "red" | "grey";

export const TONE_CLASSES: Record<Tone, string> = {
  green: "bg-[#e7f6ee] text-[#137a43] ring-[#bfe6cf]",
  amber: "bg-[#fff4e8] text-[#a14a00] ring-[#f7d6b2]",
  red: "bg-red-50 text-red-700 ring-red-200",
  grey: "bg-[#eaf1fd] text-[#1a56db] ring-[#c9d8f5]",
};

/** The dot colour that goes with each tone (status chips). */
export const TONE_DOT: Record<Tone, string> = {
  green: "bg-[#1a9a56]",
  amber: "bg-[#e28a1d]",
  red: "bg-red-500",
  grey: "bg-[#3b82f6]",
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
