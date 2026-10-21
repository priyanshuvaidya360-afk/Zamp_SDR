"use client";

import { BookOpenCheck, Loader2 } from "lucide-react";
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

type Tab = "email" | "signals" | "activity";

const WRITES_RECORDS = new Set(["resolve", "extract", "score", "draft", "critic"]);

export function RunView({ runId }: { runId: number }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [streamFrom, setStreamFrom] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab | null>(null); // null: Email once there is a draft, else Agent activity
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
  const active = tab ?? (draft ? "email" : "activity");

  function reviewed(result: ReviewResult) {
    if (result.outcome === "gone") {
      // Back through scoring with the runner-up hook: follow it live.
      setDetail((d) => (d ? { ...d, run: { ...d.run, status: "running", final_reason: null } } : d));
      setStreamFrom(lastId(events));
    }
    load(false);
  }

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "email", label: "Email" },
    { id: "signals", label: "Signals", count: detail.signals.length },
    { id: "activity", label: "Agent activity" },
  ];

  return (
    <div className="space-y-6">
      <header className="space-y-3">
        <Link href={`/batches/${run.batch_id}`} className="text-sm text-muted-foreground hover:text-foreground">
          ← Batch #{run.batch_id}
        </Link>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{name}</h1>
            <p className="text-muted-foreground">
              {[run.title, run.company ?? run.domain].filter(Boolean).join(" at ")}
              {run.message_type === "follow_up" && " · follow-up"}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <StatusChip status={run.status} />
          </div>
        </div>
        {run.final_reason && run.status !== "needs_input" && <p className="text-sm text-muted-foreground">{run.final_reason}</p>}
      </header>

      {run.status === "needs_input" && (
        <CandidateCards reason={run.final_reason ?? "Identity is unclear."} candidates={candidates} onPick={pick} />
      )}

      <div className="flex items-end justify-between gap-4 border-b">
        <div role="tablist" className="-mb-px flex gap-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={active === t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                "flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
                active === t.id ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {t.label}
              {t.count != null && t.count > 0 && (
                <span className="rounded-full bg-zinc-100 px-1.5 text-xs text-zinc-600 tabular-nums">{t.count}</span>
              )}
              {t.id === "activity" && running && <Loader2 className="size-3.5 animate-spin text-zinc-400" aria-label="running" />}
            </button>
          ))}
        </div>
        {draft && active === "email" && (
          <button
            onClick={() => setGuide((g) => !g)}
            aria-pressed={guide}
            className={cn(
              "mb-1.5 flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
              guide ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-zinc-100 hover:text-foreground",
            )}
          >
            <BookOpenCheck className="size-4" /> Content guide
          </button>
        )}
      </div>

      <div className={cn("grid gap-6 grid-cols-[minmax(0,1fr)]", guide && draft && active === "email" && "lg:grid-cols-[minmax(0,1fr)_20rem]")}>
        <div className="min-w-0">
          {active === "email" &&
            (draft ? (
              <div className="mx-auto max-w-3xl space-y-4">
                <DraftPanel drafts={detail.drafts} signals={detail.signals} to={[name, run.title].filter(Boolean).join(" · ")} />
                {reviewable && (
                  <div className="sticky bottom-4 z-10">
                    <ReviewControls key={draft.id} runId={runId} draft={draft} recheckFacts={detail.recheck.facts} onDone={reviewed} />
                  </div>
                )}
              </div>
            ) : (
              <p className="rounded-xl border border-dashed bg-white px-4 py-16 text-center text-sm text-muted-foreground">
                {running ? "The email appears here once the hook is chosen. Watch progress under Agent activity." : "No draft for this run."}
              </p>
            ))}
          {active === "signals" && (
            <div className="mx-auto max-w-3xl">
              <SignalList signals={detail.signals} angle={angle} waiting={running} />
            </div>
          )}
          {active === "activity" && (
            <div className="mx-auto max-w-3xl space-y-3">
              <p className="text-sm text-muted-foreground">
                {run.cost_credits} search credits
                {run.confidence != null && <> · confidence {run.confidence.toFixed(2)}</>}. Open a step to see what it searched, read and decided.
              </p>
              <StageStepper stages={detail.stages} latest={latest} />
            </div>
          )}
        </div>
        {guide && draft && active === "email" && (
          <aside aria-label="Content guide" className="h-fit rounded-xl border bg-white p-5 lg:sticky lg:top-6">
            <h2 className="mb-4 text-sm font-semibold">Content guide</h2>
            <ContentGuide draft={draft} />
          </aside>
        )}
      </div>
    </div>
  );
}

function lastId(events: StageEvent[]): number {
  return events.reduce((max, e) => Math.max(max, e.id), 0);
}
