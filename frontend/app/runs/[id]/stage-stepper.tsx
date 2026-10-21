"use client";

import { StepIcon } from "@/components/step-icon";
import { formatDuration, stepState } from "@/lib/stages";
import type { StageEvent, StageInfo } from "@/lib/types";

// Agent activity tab: one row per stage. Spinner, then tick / warning / cross, duration, one-line
// result; open a stage for its queries, sources, model output and credits.
export function StageStepper({ stages, latest }: { stages: StageInfo[]; latest: Map<string, StageEvent> }) {
  return (
    <ol className="rounded-[14px] border bg-white px-4 py-2">
      {stages.map((stage, index) => {
        const event = latest.get(stage.name);
        const state = stepState(event);
        return (
          <li key={stage.name} className="relative">
            {index < stages.length - 1 && <span className="absolute top-7 bottom-0 left-[11px] w-px bg-zinc-200" aria-hidden />}
            <details className="group rounded-lg px-1 py-2.5 open:bg-zinc-50">
              <summary className="flex cursor-pointer list-none items-start gap-3">
                <StepIcon state={state} className="mt-0.5" />
                <div className="min-w-0 flex-1">
                  <p className="flex items-baseline justify-between gap-2 text-sm font-medium">
                    {stage.label}
                    {event?.duration_ms != null && (
                      <span className="font-mono text-xs font-normal text-primary tabular-nums">{formatDuration(event.duration_ms)}</span>
                    )}
                  </p>
                  <p className="text-xs text-muted-foreground">{summaryLine(state, event)}</p>
                </div>
              </summary>
              {event && <StageDetails event={event} />}
            </details>
          </li>
        );
      })}
    </ol>
  );
}

function summaryLine(state: ReturnType<typeof stepState>, event?: StageEvent): string {
  if (state === "pending") return "Waiting";
  if (state === "running") return "Working…";
  if (state === "skipped") return "Not run";
  return event?.message || "Done";
}

type Output = Record<string, unknown>;

function StageDetails({ event }: { event: StageEvent }) {
  const output = (event.payload.output ?? {}) as Output;
  const tokens = event.payload.tokens ?? 0;
  const credits = event.payload.credits ?? 0;
  return (
    <div className="mt-2 ml-7 space-y-3 text-xs">
      <p className="text-muted-foreground">
        {tokens.toLocaleString()} tokens · {credits} search credits
      </p>
      <Queries output={output} payload={event.payload} />
      <Sources output={output} />
      <Hits output={output} />
      <details>
        <summary className="cursor-pointer text-muted-foreground">Raw output</summary>
        <pre className="mt-1 max-h-72 overflow-auto rounded-md bg-zinc-950 p-2 text-[11px] leading-relaxed text-zinc-100">
          {JSON.stringify(event.payload.output ?? event.payload, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function Queries({ output, payload }: { output: Output; payload: StageEvent["payload"] }) {
  const queries = (output.queries ?? payload.queries) as unknown[] | undefined;
  if (!queries?.length) return null;
  return (
    <div>
      <p className="font-medium">Searches</p>
      <ul className="mt-1 space-y-1">
        {queries.map((q, i) =>
          typeof q === "string" ? (
            <li key={i} className="font-mono text-[11px] text-muted-foreground">{q}</li>
          ) : (
            <li key={i} className="text-muted-foreground">
              <span className="font-mono text-[11px]">{String((q as Output).query)}</span>
              <span className="block">
                {String((q as Output).provider)} · {String((q as Output).results)} results, {String((q as Output).relevant ?? "—")} relevant
              </span>
            </li>
          ),
        )}
      </ul>
    </div>
  );
}

function Sources({ output }: { output: Output }) {
  const sources = output.sources as { url: string; title: string; category: string; text: string | null }[] | undefined;
  if (!sources?.length) return null;
  return (
    <div>
      <p className="font-medium">Pages read</p>
      <ul className="mt-1 space-y-1">
        {sources.map((s) => (
          <li key={s.url} className="truncate">
            <a href={s.url} target="_blank" rel="noreferrer" className="text-primary hover:underline">
              {s.title || s.url}
            </a>
            <span className="text-muted-foreground"> · {s.category.replace(/_/g, " ")}{s.text ? "" : " · couldn't read"}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Hits({ output }: { output: Output }) {
  const hits = output.instruction_hits as { text: string; label: string; origin: string }[] | undefined;
  if (!hits?.length) return null;
  return (
    <div>
      <p className="font-medium">Instructions aimed at AI tools</p>
      <ul className="mt-1 space-y-1.5">
        {hits.map((h, i) => (
          <li key={i}>
            <mark className="rounded bg-amber-100 px-1 text-amber-900">{h.text}</mark>
            <span className="block text-muted-foreground">
              {h.label.replace(/_/g, " ")} · in {h.origin === "public_bio" ? "their bio" : "a page"} · removed, not followed
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
