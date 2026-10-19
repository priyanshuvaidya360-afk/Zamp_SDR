import type { StageEvent, StageState } from "./types";

// What one stage looks like in a stepper, from its latest event.
export type StepState = "pending" | "running" | "done" | "warning" | "failed" | "skipped";

export function stepState(event: StageEvent | undefined): StepState {
  if (!event) return "pending";
  if (event.state === "started") return "running";
  if (event.state === "failed") return "failed";
  if (event.state === "skipped") return "skipped";
  const output = event.payload.output ?? {};
  // Done, but the run stopped here (needs input, held...) or the result needs a human look.
  if (event.payload.stopped || output.low_signal === true || output.passed === false) return "warning";
  return "done";
}

export function stateOf(state: StageState | null | undefined): StepState {
  return state === "started" ? "running" : state === "failed" ? "failed" : state ? "done" : "pending";
}

/** The latest event per stage. Later events (a resumed run) replace earlier ones. */
export function latestByStage(events: StageEvent[]): Map<string, StageEvent> {
  const latest = new Map<string, StageEvent>();
  for (const event of [...events].sort((a, b) => a.id - b.id)) latest.set(event.stage, event);
  return latest;
}

/** Merge event lists by id (stored events + live ones), oldest first. */
export function mergeEvents(a: StageEvent[], b: StageEvent[]): StageEvent[] {
  const byId = new Map<number, StageEvent>();
  for (const event of [...a, ...b]) byId.set(event.id, event);
  return [...byId.values()].sort((x, y) => x.id - y.id);
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}
