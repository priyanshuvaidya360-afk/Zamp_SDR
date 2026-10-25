// Status colours, defined once and used everywhere (docs/PROJECT_SPEC.md "UI: three screens", Style):
// green = ready / approved, amber = needs input / low signal (waiting on a human),
// red = failed / suppressed, grey = running / already researched.

import type { IdentityOutcome, RowStatus, RunStatus, SignalLevel } from "./types";

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
  blocked: { label: "Already researched", tone: "grey" },
  suppressed: { label: "Suppressed", tone: "red" },
  failed: { label: "Failed", tone: "red" },
  rejected: { label: "Rejected", tone: "grey" },
  dismissed: { label: "Dismissed", tone: "grey" },
  already_drafted: { label: "Already drafted", tone: "green" },
  invalid: { label: "Invalid", tone: "red" },
  skipped: { label: "Duplicate", tone: "grey" },
};

/** A needs_input run says why identity failed, so the list tells bad data from a private person. */
export const IDENTITY_OUTCOME: Record<IdentityOutcome, { label: string; tone: Tone }> = {
  not_public: { label: "Not found online", tone: "amber" },
  bad_data: { label: "Check data", tone: "red" },
  search_failed: { label: "Search failed", tone: "grey" },
  ambiguous: { label: "Pick the person", tone: "amber" },
  unconfirmed: { label: "Confirm person", tone: "amber" },
};

/** The lists' status column for a researched run: how much the research found. */
export const SIGNAL_LEVEL: Record<SignalLevel, { label: string; tone: Tone }> = {
  high: { label: "High signal", tone: "green" },
  moderate: { label: "Moderate signal", tone: "grey" },
  low: { label: "Low signal", tone: "amber" },
  none: { label: "No signal", tone: "red" },
};

/** Runs whose status column shows the signal level instead: research is done and nothing is
 *  waiting on someone (running, needs input, held, failed... keep their own status). A re-upload
 *  of someone researched recently (blocked, already drafted) shows the earlier run's level. */
export const SHOWS_SIGNAL_LEVEL: RunStatus[] = [
  "ready_for_review", "low_signal", "approved", "rejected", "blocked", "already_drafted",
];

/** The prospect list's status column: was this person researched before? Drafting an already
 *  researched person doesn't search again; it shows the earlier draft. */
export const PROSPECT_STATE: Record<"fresh" | "researched", { label: string; tone: Tone }> = {
  fresh: { label: "Fresh prospect", tone: "green" },
  researched: { label: "Already researched", tone: "grey" },
};

export const ROW_STATUS: Record<RowStatus, { label: string; tone: Tone }> = {
  valid: { label: "Valid", tone: "green" },
  invalid: { label: "Invalid", tone: "red" },
  skipped: { label: "Duplicate", tone: "grey" },
};
