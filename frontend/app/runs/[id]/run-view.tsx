"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { FlowSteps } from "@/components/flow-steps";
import { RetryButton } from "@/components/retry-button";
import { ReviewControls } from "@/components/review-controls";
import { Chip, StatusChip } from "@/components/status-chip";
import { api, ApiError, streams } from "@/lib/api";
import { latestByStage, mergeEvents } from "@/lib/stages";
import { SIGNAL_LEVEL } from "@/lib/status";
import type { Candidate, IdentityCheck, IdentityOutcome, LiveEvent, ResumeInput, ReviewResult, RunDetail, StageEvent } from "@/lib/types";
import { useEventStream } from "@/lib/use-event-stream";
import { cn } from "@/lib/utils";

import { ContentGuide } from "./content-guide";
import { DraftPanel, latestDraft } from "./draft-panel";
import { IdentityPanel } from "./identity-panel";
import { SignalList } from "./signal-list";
import { SourceList } from "./source-list";
import { StageStepper } from "./stage-stepper";

// Spec: docs/PROJECT_SPEC.md "UI: three screens" > 2 Live run view, and T11.
// Stored state loads first (so a reload mid-run shows exactly where it is), then the live
// stream continues from the last stored event. Stages that write signals or drafts trigger a
// refetch of those records.

const WRITES_RECORDS = new Set(["resolve", "extract", "score", "draft", "critic"]);

export function RunView({ runId }: { runId: number }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [streamFrom, setStreamFrom] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // null = not chosen yet: Signals when there are any, Sources when there are none (see below).
  const [side, setSide] = useState<"sources" | "signals" | "guide" | null>(null);
  const [sender, setSender] = useState<string | undefined>(undefined);
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

  useEffect(() => {
    api.getSeller().then((seller) => setSender(seller.rep.name), () => {});
  }, []);

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

  // Already drafted: this person was researched in another batch recently. Show that run's
  // draft, signals and sources here; it is reviewed there, so one email is never queued twice.
  const earlierId = detail?.run.status === "already_drafted" ? Number(latest.get("intake")?.payload.earlier_run_id) || null : null;
  const [earlier, setEarlier] = useState<RunDetail | null>(null);
  useEffect(() => {
    if (!earlierId) return;
    api.getRun(earlierId).then(setEarlier, () => setEarlier(null));
  }, [earlierId]);
  const earlierLatest = useMemo(() => (earlier ? latestByStage(earlier.events) : null), [earlier]);

  // Retried in place: follow the new attempt live from the events we already have.
  function retried() {
    setDetail((d) =>
      d
        ? { ...d, run: { ...d.run, status: "running", final_reason: null,
                         retry_count: d.run.retry_count + 1, retries_left: d.run.retries_left - 1 } }
        : d,
    );
    setStreamFrom(lastId(events));
  }

  async function resume(input: ResumeInput) {
    await api.resumeRun(runId, input);
    setDetail((d) => (d ? { ...d, run: { ...d.run, status: "running", final_reason: null } } : d));
    setStreamFrom(lastId(events));
  }

  if (error) return <p className="text-red-700">{error}</p>;
  if (!detail) return <p className="text-muted-foreground">Loading…</p>;

  const { run } = detail;
  const name = run.full_name || `${run.first_name} ${run.last_name}`;
  const resolve = latest.get("resolve");
  const candidates = (resolve?.payload.candidates ?? []) as Candidate[];
  const outcome = (resolve?.payload.outcome ?? run.identity_outcome ?? null) as IdentityOutcome | null;
  const diagnosis = (resolve?.payload.diagnosis ?? []) as IdentityCheck[];
  // What the email and side columns show: this run's work, or the earlier run's (already drafted).
  const shown = earlierId && earlier ? earlier : detail;
  const shownLatest = earlierId && earlierLatest ? earlierLatest : latest;
  const angle = ((shownLatest.get("score")?.payload.output ?? {}) as { angle?: string | null }).angle ?? null;
  const running = run.status === "running";
  const reviewable = run.status === "ready_for_review" || run.status === "low_signal";
  const draft = latestDraft(shown.drafts);
  // Open on what's useful: the signals, or, when none were found, the sources that explain why.
  const tab = side ?? (shown.signals.length || running ? "signals" : "sources");
  function reviewed(result: ReviewResult) {
    if (result.outcome === "gone") {
      // Back through scoring with the runner-up hook: follow it live.
      setDetail((d) => (d ? { ...d, run: { ...d.run, status: "running", final_reason: null } } : d));
      setStreamFrom(lastId(events));
    }
    load(false);
  }

  const to = [name, run.title].filter(Boolean).join(" · ");
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("");

  return (
    // One screen on desktop: three columns that scroll on their own. Stacked on small screens.
    <div className="space-y-4 lg:relative lg:left-1/2 lg:w-[min(96rem,calc(100vw-3rem))] lg:-translate-x-1/2">
      <FlowSteps current={3} compact />
      <header className="flex flex-wrap items-center gap-x-5 gap-y-3 rounded-[14px] border bg-white px-5 py-4">
        <Link
          href={`/batches/${run.batch_id}`}
          className="flex items-center gap-1.5 rounded-lg bg-secondary px-3 py-1.5 text-[13px] font-semibold text-primary hover:bg-[#dce8fc]"
        >
          ← All prospects
        </Link>
        <div className="flex size-11 items-center justify-center rounded-full bg-primary text-base font-bold text-white">{initials}</div>
        <div className="flex min-w-0 flex-col gap-0.5">
          <h1 className="text-[22px] leading-tight font-bold tracking-tight">{name}</h1>
          <p className="truncate text-sm text-muted-foreground">
            {[run.title, run.company ?? run.domain].filter(Boolean).join(" at ")}
            {run.message_type === "follow_up" && " · follow-up"}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-3">
          {run.final_reason && run.status !== "needs_input" && (
            <p className="hidden max-w-md truncate text-sm text-muted-foreground xl:block" title={run.final_reason}>
              {run.final_reason}
            </p>
          )}
          {run.signal_level && <Chip {...SIGNAL_LEVEL[run.signal_level]} title="How much the research found for this prospect" />}
          <StatusChip status={run.status} outcome={run.identity_outcome} />
        </div>
      </header>

      {run.status === "failed" && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-[14px] border border-red-200 bg-red-50 px-5 py-3.5">
          <div className="min-w-0 space-y-0.5">
            <p className="text-sm font-semibold text-red-700">This run failed</p>
            <p className="text-sm text-red-700">{run.final_reason ?? "Something went wrong while researching this prospect."}</p>
            <p className="text-xs text-muted-foreground">
              Retrying restarts at the failed stage and reuses research that is still fresh.
              {run.retry_count > 0 && ` Retried ${run.retry_count} time${run.retry_count === 1 ? "" : "s"} so far.`}
            </p>
          </div>
          <RetryButton runId={runId} retriesLeft={run.retries_left} onRetried={retried} />
        </div>
      )}

      {earlierId && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-[14px] border border-[#bfe6cf] bg-[#e7f6ee] px-5 py-3.5">
          <div className="min-w-0 space-y-0.5">
            <p className="text-sm font-semibold text-[#137a43]">Already drafted</p>
            <p className="text-sm text-zinc-700">
              {name} was researched in another batch
              {earlier && ` on ${new Date(earlier.run.started_at).toLocaleDateString()}`}, so nothing was searched again.
              The draft below is from that run
              {earlier && ` (${earlier.run.status.replace(/_/g, " ")})`}: review or edit it there.
            </p>
          </div>
          <Link href={`/runs/${earlierId}`} className="rounded-lg bg-primary px-3.5 py-2 text-[13px] font-semibold text-white hover:bg-primary/90">
            Open run #{earlierId} to review
          </Link>
        </div>
      )}

      {run.status === "needs_input" && (
        <IdentityPanel
          reason={run.final_reason ?? "Identity is unclear."}
          outcome={outcome}
          checks={diagnosis}
          candidates={candidates}
          prospect={run}
          onResume={resume}
        />
      )}

      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:h-[calc(100vh-18rem)] lg:min-h-[30rem] lg:grid-cols-[19rem_minmax(0,1fr)_minmax(0,24rem)]">
        <Column
          title="Agent activity"
          chips={[`${run.cost_credits} credit${run.cost_credits === 1 ? "" : "s"}`, ...(run.confidence != null ? [`confidence ${run.confidence.toFixed(2)}`] : [])]}
        >
          <StageStepper stages={detail.stages} latest={latest} retry={latest.get("retry")} />
        </Column>

        <section aria-label="Email" className="flex min-h-0 flex-col gap-3">
          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            {draft ? (
              <DraftPanel drafts={shown.drafts} signals={shown.signals} to={to} from={sender} />
            ) : (
              <p className="rounded-[14px] border border-dashed bg-white px-4 py-16 text-center text-sm text-muted-foreground">
                {running ? "The email appears here once the hook is chosen." : "No draft for this run."}
              </p>
            )}
          </div>
          {reviewable && draft && (
            <ReviewControls
              key={draft.id}
              runId={runId}
              draft={draft}
              recheckFacts={detail.recheck.facts}
              unverified={run.identity_unverified}
              onDone={reviewed}
            />
          )}
        </section>

        <section aria-label="Sources, signals and content guide" className="flex min-h-0 flex-col gap-3">
          <div className="grid shrink-0 grid-cols-3 gap-1 rounded-xl border bg-white p-1" role="tablist">
            {(
              [
                ["sources", "Sources"],
                ["signals", `Signals · ${shown.signals.length}`],
                ["guide", "Content guide"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                role="tab"
                aria-selected={tab === id}
                disabled={id === "guide" && !draft}
                onClick={() => setSide(id)}
                className={cn(
                  "rounded-[9px] px-3 py-2 text-[13px] font-semibold transition-colors disabled:opacity-40",
                  tab === id ? "bg-primary text-white" : "text-muted-foreground hover:bg-secondary hover:text-navy",
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            {tab === "guide" && draft ? (
              <ContentGuide draft={draft} />
            ) : tab === "sources" ? (
              <SourceList run={run} latest={shownLatest} signals={shown.signals} waiting={running} />
            ) : (
              <SignalList signals={shown.signals} angle={angle} waiting={running} />
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function Column({ title, chips, children }: { title: string; chips?: string[]; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="flex min-h-0 flex-col gap-3">
      <div className="flex shrink-0 flex-col gap-2 rounded-[14px] border bg-white px-5 py-4">
        <h2 className="text-xs font-bold tracking-[0.08em] text-primary uppercase">{title}</h2>
        {chips && chips.length > 0 && (
          <div className="flex flex-wrap gap-2 text-[13px]">
            {chips.map((c) => (
              <span key={c} className="rounded-md bg-secondary px-2.5 py-0.5 font-semibold text-navy">
                {c}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">{children}</div>
    </section>
  );
}

function lastId(events: StageEvent[]): number {
  return events.reduce((max, e) => Math.max(max, e.id), 0);
}
