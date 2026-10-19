"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { StatusChip } from "@/components/status-chip";
import { api, ApiError, streams } from "@/lib/api";
import { latestByStage, mergeEvents } from "@/lib/stages";
import type { Candidate, LiveEvent, RunDetail, StageEvent } from "@/lib/types";
import { useEventStream } from "@/lib/use-event-stream";

import { CandidateCards } from "./candidate-cards";
import { DraftPanel } from "./draft-panel";
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

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Link href={`/batches/${run.batch_id}`} className="text-sm text-muted-foreground hover:text-foreground">
          ← Batch #{run.batch_id}
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{name}</h1>
            <p className="text-muted-foreground">
              {[run.title, run.company ?? run.domain].filter(Boolean).join(" at ")}
              {run.message_type === "follow_up" && " · follow-up"}
            </p>
          </div>
          <div className="flex items-center gap-4 text-sm">
            {run.confidence != null && (
              <span className="text-muted-foreground">
                confidence <span className="font-medium text-foreground tabular-nums">{run.confidence.toFixed(2)}</span>
              </span>
            )}
            <span className="text-muted-foreground">
              <span className="font-medium text-foreground tabular-nums">{run.cost_credits}</span> credits
            </span>
            <StatusChip status={run.status} />
          </div>
        </div>
        {run.final_reason && run.status !== "needs_input" && <p className="text-sm text-muted-foreground">{run.final_reason}</p>}
      </div>

      {run.status === "needs_input" && (
        <CandidateCards reason={run.final_reason ?? "Identity is unclear."} candidates={candidates} onPick={pick} />
      )}

      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[17rem_minmax(0,1fr)] xl:grid-cols-[17rem_minmax(0,1fr)_minmax(0,24rem)]">
        <section aria-label="Stages">
          <StageStepper stages={detail.stages} latest={latest} />
        </section>
        <section aria-label="Signals" className="space-y-3">
          <h2 className="text-sm font-medium tracking-wide text-muted-foreground uppercase">Signals</h2>
          <SignalList signals={detail.signals} angle={angle} waiting={running} />
        </section>
        <section aria-label="Draft" className="space-y-3 lg:col-start-2 xl:col-start-auto">
          <h2 className="text-sm font-medium tracking-wide text-muted-foreground uppercase">Draft</h2>
          {detail.drafts.length ? (
            <DraftPanel drafts={detail.drafts} signals={detail.signals} />
          ) : (
            <p className="rounded-xl border border-dashed bg-white px-4 py-10 text-center text-sm text-muted-foreground">
              {running ? "The draft appears here once the hook is chosen." : "No draft for this run."}
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

function lastId(events: StageEvent[]): number {
  return events.reduce((max, e) => Math.max(max, e.id), 0);
}
