"use client";

import { BookOpenCheck } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ReviewControls } from "@/components/review-controls";
import { StatusChip } from "@/components/status-chip";
import { api, ApiError, streams } from "@/lib/api";
import { latestByStage, mergeEvents } from "@/lib/stages";
import type { Candidate, LiveEvent, ReviewResult, RunDetail, StageEvent } from "@/lib/types";
import { useEventStream } from "@/lib/use-event-stream";
import { cn } from "@/lib/utils";

import { CandidateCards } from "./candidate-cards";
import { ContentGuide } from "./content-guide";
import { DraftPanel, latestDraft } from "./draft-panel";
import { SignalList } from "./signal-list";
import { StageStepper } from "./stage-stepper";

// Spec: docs/GUIDE.md "UI: three screens" > 2 Live run view, and T11.
// Stored state loads first (so a reload mid-run shows exactly where it is), then the live
// stream continues from the last stored event. Stages that write signals or drafts trigger a
// refetch of those records.

const WRITES_RECORDS = new Set(["resolve", "extract", "score", "draft", "critic"]);

export function RunView({ runId }: { runId: number }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [streamFrom, setStreamFrom] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [guide, setGuide] = useState(false);
  const refetch = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const load = useCallback(
    (startStreaming: boolean) =>
      api.getRun(runId).then(
        (next) => {
          setDetail(next);
          setEvents((current) => mergeEvents(current, next.events));
          if (startStreaming && next.run.status === "running") setStreamFrom(lastId(next.events));
        },
        (e) => setError(e instanceof ApiError ? e.message : "Couldn't load this run."),
      ),
    [runId],
  );

  useEffect(() => {
    load(true);
  }, [load]);

  const scheduleRefetch = useCallback(() => {
    clearTimeout(refetch.current);
    refetch.current = setTimeout(() => load(false), 300);
  }, [load]);

  useEventStream(streamFrom !== null ? streams.run(runId, streamFrom) : null, (event: LiveEvent) => {
    if (event.type === "stage") {
      setEvents((current) => mergeEvents(current, [event]));
      if (event.state !== "started" && WRITES_RECORDS.has(event.stage)) scheduleRefetch();
    } else if (event.type === "run_status") {
      setDetail((d) =>
        d ? { ...d, run: { ...d.run, status: event.status, final_reason: event.final_reason, confidence: event.confidence, cost_credits: event.cost_credits } } : d,
      );
    } else if (event.type === "end") {
      setStreamFrom(null);
      scheduleRefetch();
    }
  });

  const latest = useMemo(() => latestByStage(events), [events]);

  async function pick(candidate: Candidate) {
    await api.resumeRun(runId, candidate);
    setDetail((d) => (d ? { ...d, run: { ...d.run, status: "running", final_reason: null } } : d));
    setStreamFrom(lastId(events));
  }

  if (error) return <p className="text-red-700">{error}</p>;
  if (!detail) return <p className="text-muted-foreground">Loading…</p>;

  const { run } = detail;
  const name = run.full_name || `${run.first_name} ${run.last_name}`;
  const resolve = latest.get("resolve");
  const candidates = (resolve?.payload.candidates ?? []) as Candidate[];
  const angle = ((latest.get("score")?.payload.output ?? {}) as { angle?: string | null }).angle ?? null;
  const running = run.status === "running";
  const reviewable = run.status === "ready_for_review" || run.status === "low_signal";
  const draft = latestDraft(detail.drafts);
  function reviewed(result: ReviewResult) {
    if (result.outcome === "gone") {
      // Back through scoring with the runner-up hook: follow it live.
      setDetail((d) => (d ? { ...d, run: { ...d.run, status: "running", final_reason: null } } : d));
      setStreamFrom(lastId(events));
    }
    load(false);
  }

  const to = [name, run.title].filter(Boolean).join(" · ");

  return (
    // One screen on desktop: three columns that scroll on their own. Stacked on small screens.
    <div className="space-y-4 lg:relative lg:left-1/2 lg:w-[min(96rem,calc(100vw-3rem))] lg:-translate-x-1/2">
      <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3">
          <Link href={`/batches/${run.batch_id}`} className="text-sm text-muted-foreground hover:text-foreground">
            ← Batch #{run.batch_id}
          </Link>
          <h1 className="text-xl font-semibold tracking-tight">{name}</h1>
          <p className="truncate text-sm text-muted-foreground">
            {[run.title, run.company ?? run.domain].filter(Boolean).join(" at ")}
            {run.message_type === "follow_up" && " · follow-up"}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {run.final_reason && run.status !== "needs_input" && (
            <p className="max-w-md truncate text-sm text-muted-foreground" title={run.final_reason}>
              {run.final_reason}
            </p>
          )}
          <StatusChip status={run.status} />
        </div>
      </header>

      {run.status === "needs_input" && (
        <CandidateCards reason={run.final_reason ?? "Identity is unclear."} candidates={candidates} onPick={pick} />
      )}

      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:h-[calc(100vh-10.5rem)] lg:min-h-[30rem] lg:grid-cols-[17rem_minmax(0,1fr)_minmax(0,23rem)]">
        <Column title="Agent activity" note={`${run.cost_credits} credits${run.confidence != null ? ` · confidence ${run.confidence.toFixed(2)}` : ""}`}>
          <StageStepper stages={detail.stages} latest={latest} />
        </Column>

        <section aria-label="Email" className="flex min-h-0 flex-col gap-3">
          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            {draft ? (
              <DraftPanel drafts={detail.drafts} signals={detail.signals} to={to} />
            ) : (
              <p className="rounded-xl border border-dashed bg-white px-4 py-16 text-center text-sm text-muted-foreground">
                {running ? "The email appears here once the hook is chosen." : "No draft for this run."}
              </p>
            )}
          </div>
          {reviewable && draft && (
            <ReviewControls key={draft.id} runId={runId} draft={draft} recheckFacts={detail.recheck.facts} onDone={reviewed} />
          )}
        </section>

        <Column
          title={guide && draft ? "Content guide" : "Signals"}
          note={guide && draft ? undefined : `${detail.signals.length} found`}
          action={
            draft && (
              <button
                onClick={() => setGuide((g) => !g)}
                aria-pressed={guide}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors",
                  guide ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-zinc-100 hover:text-foreground",
                )}
              >
                <BookOpenCheck className="size-3.5" /> Content guide
              </button>
            )
          }
        >
          {guide && draft ? (
            <div className="rounded-xl border bg-white p-5">
              <ContentGuide draft={draft} />
            </div>
          ) : (
            <SignalList signals={detail.signals} angle={angle} waiting={running} />
          )}
        </Column>
      </div>
    </div>
  );
}

function Column({ title, note, action, children }: { title: string; note?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="flex min-h-0 flex-col gap-2">
      <div className="flex h-7 shrink-0 items-center justify-between gap-2">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          {title}
          {note && <span className="ml-2 font-normal tracking-normal normal-case">{note}</span>}
        </h2>
        {action}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">{children}</div>
    </section>
  );
}

function lastId(events: StageEvent[]): number {
  return events.reduce((max, e) => Math.max(max, e.id), 0);
}
