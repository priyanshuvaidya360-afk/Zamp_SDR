"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ContentGuide } from "@/app/runs/[id]/content-guide";
import { DraftPanel, latestDraft } from "@/app/runs/[id]/draft-panel";
import { ReviewControls } from "@/components/review-controls";
import { StatusChip } from "@/components/status-chip";
import { buttonVariants } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import type { BatchDetail, ReviewResult, RunDetail } from "@/lib/types";

// Spec: docs/PROJECT_SPEC.md "UI: three screens" > 1b "Review queue" and T15: step through every
// ready_for_review draft in row order. Keys: a approve · e edit · r reject · j next · k previous.

export function ReviewQueue({ batchId }: { batchId: number }) {
  const [batch, setBatch] = useState<BatchDetail | null>(null);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [done, setDone] = useState<{ name: string; outcome: string }[]>([]);
  const [error, setError] = useState<string | null>(null);

  const loadBatch = useCallback(
    () =>
      api.getBatch(batchId).then(
        (b) => {
          setBatch(b);
          return b;
        },
        (e) => {
          setError(e instanceof ApiError ? e.message : "Couldn't load this batch.");
          return null;
        },
      ),
    [batchId],
  );

  useEffect(() => {
    loadBatch();
  }, [loadBatch]);

  const pending = useMemo(
    () => (batch?.runs ?? []).filter((r) => r.status === "ready_for_review").sort((a, b) => a.row_number - b.row_number),
    [batch],
  );

  // The chosen draft while it is still waiting; otherwise the first waiting one.
  const current = pending.some((r) => r.id === currentId) ? currentId : (pending[0]?.id ?? null);

  const loadRun = useCallback((runId: number) => {
    api.getRun(runId).then(setDetail, (e) => setError(e instanceof ApiError ? e.message : "Couldn't load this draft."));
  }, []);

  useEffect(() => {
    if (current !== null) loadRun(current);
  }, [current, loadRun]);

  const index = pending.findIndex((r) => r.id === current);
  const step = useCallback(
    (delta: number) => {
      if (!pending.length) return;
      const next = pending[(Math.max(index, 0) + delta + pending.length) % pending.length];
      setCurrentId(next.id);
    },
    [pending, index],
  );

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (target.closest("input, textarea, select, [contenteditable], [role=dialog]") || event.metaKey || event.ctrlKey) return;
      if (event.key === "j") step(1);
      else if (event.key === "k") step(-1);
      else return;
      event.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  async function reviewed(result: ReviewResult) {
    const name = pending[index]?.name ?? "";
    if (result.outcome === "changed") {
      loadRun(current!); // "Not now": the corrected version stays here, waiting
      return;
    }
    setDone((d) => [{ name, outcome: result.message }, ...d]);
    // Move on to the next waiting draft after this one (in row order).
    const after = pending.slice(index + 1).concat(pending.slice(0, index)).map((r) => r.id);
    const fresh = await loadBatch();
    const stillWaiting = new Set((fresh?.runs ?? []).filter((r) => r.status === "ready_for_review").map((r) => r.id));
    setCurrentId(after.find((id) => stillWaiting.has(id)) ?? null);
  }

  if (error) return <p className="text-red-700">{error}</p>;
  if (!batch) return <p className="text-muted-foreground">Loading…</p>;

  const shown = detail?.run.id === current ? detail : null; // never show the previous draft
  const run = shown?.run;
  const draft = shown ? latestDraft(shown.drafts) : undefined;
  const hook = shown?.signals.find((s) => s.selected);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <Link href={`/batches/${batchId}`} className="text-sm text-muted-foreground hover:text-foreground">
            ← {batch.batch.filename}
          </Link>
          <h1 className="text-2xl font-bold tracking-tight">Review queue</h1>
          <p className="text-muted-foreground">
            {pending.length ? `Draft ${index + 1} of ${pending.length} waiting` : "Nothing waiting for review"}
            {done.length > 0 && ` · ${done.length} reviewed just now`}
          </p>
        </div>
        <a href={api.exportUrl(batchId)} className={buttonVariants({ variant: "outline" })}>
          Export approved CSV
        </a>
      </div>

      {!pending.length ? (
        <div className="rounded-xl border border-dashed bg-white px-4 py-10 text-center text-sm text-muted-foreground">
          <p>Every draft in this batch has been reviewed.</p>
          <p className="mt-1">Approved emails are in the export. Nothing is sent from Signal.</p>
        </div>
      ) : !shown || !run || !draft ? (
        <p className="text-muted-foreground">Loading draft…</p>
      ) : (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <section aria-label="Draft" className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <Link href={`/runs/${run.id}`} className="font-medium hover:underline">
                  {run.full_name || `${run.first_name} ${run.last_name}`}
                </Link>
                <p className="text-sm text-muted-foreground">{[run.title, run.company ?? run.domain].filter(Boolean).join(" at ")}</p>
              </div>
              <StatusChip status={run.status} />
            </div>
            <DraftPanel drafts={shown.drafts} signals={shown.signals} to={[run.full_name || `${run.first_name} ${run.last_name}`, run.title].filter(Boolean).join(" · ")} />
          </section>
          <aside className="space-y-3">
            <ReviewControls
              key={draft.id}
              runId={run.id}
              draft={draft}
              recheckFacts={shown.recheck.facts}
              unverified={shown.run.identity_unverified}
              shortcuts
              onDone={reviewed}
            />
            {hook && (
              <div className="rounded-xl border bg-white p-4 text-sm">
                <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Hook</p>
                <p className="mt-1">{hook.summary}</p>
                {hook.source_url.startsWith("http") && (
                  <a href={hook.source_url} target="_blank" rel="noreferrer" className="mt-1 block text-xs text-primary hover:underline">
                    {new URL(hook.source_url).hostname.replace(/^www\./, "")}
                  </a>
                )}
              </div>
            )}
            <details className="rounded-xl border bg-white p-4">
              <summary className="cursor-pointer text-sm font-medium">Content guide</summary>
              <div className="mt-4">
                <ContentGuide draft={draft} />
              </div>
            </details>
            <p className="text-xs text-muted-foreground">
              Keys: <b>a</b> approve · <b>e</b> edit · <b>r</b> reject · <b>j</b> next · <b>k</b> previous
            </p>
            {done.length > 0 && (
              <ul className="space-y-1 text-xs text-muted-foreground">
                {done.slice(0, 5).map((d, i) => (
                  <li key={i}>
                    {d.name}: {d.outcome}
                  </li>
                ))}
              </ul>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
