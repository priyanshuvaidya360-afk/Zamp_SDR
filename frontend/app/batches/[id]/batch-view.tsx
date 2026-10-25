"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Chip, StatusChip } from "@/components/status-chip";
import { StepIcon } from "@/components/step-icon";
import { FlowSteps } from "@/components/flow-steps";
import { MappingDialog } from "@/components/mapping-dialog";
import { RetryButton } from "@/components/retry-button";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError, streams } from "@/lib/api";
import { stateOf } from "@/lib/stages";
import { PROSPECT_STATE, RUN_STATUS } from "@/lib/status";
import type { AccountPlan, BatchDetail, BatchPreview, LiveEvent, Prospect, RunSummary } from "@/lib/types";
import { useEventStream } from "@/lib/use-event-stream";
import { cn } from "@/lib/utils";

// Spec: docs/PROJECT_SPEC.md "UI: three screens" > 1b Batch view, and T11.

export function BatchView({ batchId }: { batchId: number }) {
  const router = useRouter();
  const [detail, setDetail] = useState<BatchDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drafting, setDrafting] = useState<number | "all" | null>(null);
  const [mapping, setMapping] = useState<BatchPreview | null>(null);
  const refetch = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const load = useCallback(() => {
    api.getBatch(batchId).then(setDetail, (e) =>
      setError(e instanceof ApiError ? e.message : "Couldn't load this batch."),
    );
  }, [batchId]);

  useEffect(load, [load]);

  // Hook types are written to the database as runs finish; refetch, at most twice a second.
  const scheduleRefetch = useCallback(() => {
    clearTimeout(refetch.current);
    refetch.current = setTimeout(load, 500);
  }, [load]);

  const live = detail !== null && detail.runs.some((r) => r.status === "running");
  useEventStream(live ? streams.batch(batchId) : null, (event: LiveEvent) => {
    setDetail((current) => (current ? applyEvent(current, event) : current));
    if (event.type === "run_status" && event.status !== "running") scheduleRefetch();
    if (event.type === "batch_status" || event.type === "end") scheduleRefetch();
  });

  const labels = useMemo(
    () => Object.fromEntries((detail?.stages ?? []).map((s) => [s.name, s.label])),
    [detail?.stages],
  );
  const accounts = useMemo(() => groupAccounts(detail?.runs ?? []), [detail?.runs]);

  async function draft(rows?: number[]) {
    setDrafting(rows?.length === 1 ? rows[0] : "all");
    setError(null);
    try {
      const result = await api.draftRows(batchId, rows);
      if (rows?.length === 1) {
        router.push(`/runs/${result.runs.find((r) => r.row_number === rows[0])!.run_id}`);
        return;
      }
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't start the draft.");
    }
    setDrafting(null);
  }

  // A retried run is live again: show it running so the stream reopens and the row follows it.
  function retried(runId: number) {
    setDetail((d) =>
      d
        ? {
            ...d,
            runs: d.runs.map((r) =>
              r.id === runId
                ? { ...r, status: "running", final_reason: null, current_stage: null, current_state: null,
                    retry_count: r.retry_count + 1, retries_left: r.retries_left - 1 }
                : r,
            ),
          }
        : d,
    );
  }

  async function editMapping() {
    try {
      setMapping(await api.getPreview(batchId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't open the mapping.");
    }
  }

  if (error && !detail) return <p className="text-red-700">{error}</p>;
  if (!detail) return <p className="text-muted-foreground">Loading…</p>;

  // Live run state wins over the snapshot the prospects list was loaded with.
  const runByRow = new Map(detail.runs.map((r) => [r.row_number, r]));
  const prospects = detail.prospects.map((p) => ({ ...p, run: runByRow.get(p.row_number) ?? null }));
  const draftable = prospects.filter((p) => p.status === "valid");
  const remaining = draftable.filter((p) => !p.run).length;
  const drafted = draftable.length - remaining;
  const waiting = detail.runs.filter((r) => r.status === "ready_for_review").length;
  const canRemap = detail.batch.status === "validating" && detail.runs.length === 0;

  return (
    <div className="space-y-6">
      <FlowSteps current={2} />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[28px] leading-tight font-bold tracking-tight">Your prospects</h1>
          <p className="mt-1 text-muted-foreground">
            {detail.batch.filename} · {draftable.length} ready to draft
            {drafted > 0 && ` · ${drafted} started`}
            {detail.not_run.length > 0 && ` · ${detail.not_run.length} need a fix`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canRemap && (
            <Button variant="outline" size="lg" onClick={editMapping}>
              Edit column mapping
            </Button>
          )}
          {waiting > 0 && (
            <Link href={`/batches/${batchId}/review`} className={buttonVariants({ variant: "outline", size: "lg" })}>
              Review queue ({waiting})
            </Link>
          )}
          <a href={api.exportUrl(batchId)} className={buttonVariants({ variant: "outline", size: "lg" })}>
            Export approved CSV
          </a>
          {remaining > 1 && (
            <Button size="lg" onClick={() => draft()} disabled={drafting !== null}>
              {drafting === "all" ? "Starting…" : `Draft all ${remaining}`}
            </Button>
          )}
        </div>
      </div>

      {error && <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">{error}</p>}
      <SimilarityLine batch={detail.batch} />

      <div className="overflow-hidden rounded-[14px] border bg-white">
        <table className="w-full text-sm">
          <thead className="bg-zinc-50 text-left text-xs text-muted-foreground">
            <tr>
              <th className="w-14 px-5 py-3 font-semibold">Row</th>
              <th className="px-3 py-3 font-semibold">Prospect</th>
              <th className="px-3 py-3 font-semibold">Company</th>
              <th className="hidden px-3 py-3 font-semibold lg:table-cell">Domain / LinkedIn</th>
              <th className="px-3 py-3 font-semibold">Status</th>
              <th className="px-5 py-3 text-right font-semibold">Action</th>
            </tr>
          </thead>
          <tbody>
            {prospects.map((p) => (
              <ProspectRow key={p.row_number} p={p} labels={labels} busy={drafting !== null} onDraft={() => draft([p.row_number])} drafting={drafting === p.row_number} onRetried={retried} />
            ))}
          </tbody>
        </table>
      </div>

      {accounts.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-xs font-bold tracking-[0.08em] text-primary uppercase">Accounts with several people</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {accounts.map(([key, runs]) => {
              const plan = runs.find((r) => r.account_plan)?.account_plan;
              if (plan) return <AccountPlanCard key={key} plan={plan} runs={runs} />;
              return (
                <Card key={key}>
                  <CardHeader>
                    <CardTitle>{runs[0].company ?? key}</CardTitle>
                    <CardDescription>{runs.length} people, one set of company research</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {runs.map((run) => (
                      <Link key={run.id} href={`/runs/${run.id}`} className="flex items-center justify-between gap-3 text-sm hover:underline">
                        <span>
                          {run.name}
                          {run.title && <span className="text-muted-foreground"> · {run.title}</span>}
                        </span>
                        <span className="flex items-center gap-2">
                          {run.hook_type && <HookChip type={run.hook_type} />}
                          <StatusChip status={run.status} outcome={run.identity_outcome}/>
                        </span>
                      </Link>
                    ))}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {mapping && (
        <MappingDialog
          initial={mapping}
          confirmLabel="Save mapping"
          onClose={() => setMapping(null)}
          onConfirm={() => {
            setMapping(null);
            load();
          }}
        />
      )}
    </div>
  );
}

function ProspectRow({
  p,
  labels,
  busy,
  drafting,
  onDraft,
  onRetried,
}: {
  p: Prospect;
  labels: Record<string, string>;
  busy: boolean;
  drafting: boolean;
  onDraft: () => void;
  onRetried: (runId: number) => void;
}) {
  const run = p.run;
  const draftable = p.status === "valid";
  return (
    <tr className={cn("border-t", !draftable && "bg-zinc-50/70")}>
      <td className="px-5 py-3.5 text-muted-foreground tabular-nums">{p.row_number}</td>
      <td className="px-3 py-3.5">
        <p className="font-semibold">{p.name || "—"}</p>
        {p.title && <p className="text-xs text-muted-foreground">{p.title}</p>}
      </td>
      <td className="px-3 py-3.5">{p.company || <span className="text-muted-foreground">—</span>}</td>
      <td className="hidden max-w-48 truncate px-3 py-3.5 text-muted-foreground lg:table-cell">{p.domain || p.linkedin_url || "—"}</td>
      <td className="max-w-72 px-3 py-3.5">
        {!draftable ? (
          <span className={cn("text-xs", p.status === "invalid" ? "text-red-700" : "text-muted-foreground")}>{p.reason}</span>
        ) : (
          // One question only: was this person researched before? Signal strength is on the run page.
          <span className="flex flex-wrap items-center gap-2">
            <ProspectState p={p} />
            {run?.status === "running" && (
              <span className="basis-full text-xs text-muted-foreground">
                <StageCell run={run} labels={labels} />
              </span>
            )}
            {run?.status === "failed" && run.final_reason && (
              <span className="line-clamp-2 basis-full text-xs text-red-700" title={run.final_reason}>
                {run.final_reason}
              </span>
            )}
          </span>
        )}
      </td>
      <td className="px-5 py-3.5 text-right whitespace-nowrap">
        {!draftable ? null : !run ? (
          <Button onClick={onDraft} disabled={busy}>
            {drafting ? "Starting…" : "Draft email →"}
          </Button>
        ) : run.status === "failed" ? (
          <span className="inline-flex items-start justify-end gap-2">
            <RetryButton runId={run.id} retriesLeft={run.retries_left} onRetried={() => onRetried(run.id)} />
            <Link href={`/runs/${run.id}`} className={buttonVariants({ variant: "ghost" })}>
              Details
            </Link>
          </span>
        ) : (
          <Link href={`/runs/${run.id}`} className={buttonVariants({ variant: run.status === "running" ? "default" : "outline" })}>
            {run.status === "running" ? "Watch live" : run.status === "needs_input" ? "Resolve" : "View draft"}
          </Link>
        )}
      </td>
    </tr>
  );
}

function ProspectState({ p }: { p: Prospect }) {
  const earlier = p.researched_before;
  if (!earlier) return <Chip {...PROSPECT_STATE.fresh} title="Not researched in the last 30 days: drafting runs a full search." />;
  return (
    <Link href={`/runs/${earlier.run_id}`} className="rounded-full hover:opacity-80">
      <Chip
        {...PROSPECT_STATE.researched}
        title={`Researched on ${earlier.on} (run #${earlier.run_id}). Drafting won't search again; it shows that draft.`}
      />
    </Link>
  );
}

function StageCell({ run, labels }: { run: RunSummary; labels: Record<string, string> }) {
  if (run.status === "running") {
    if (!run.current_stage) return <span className="text-muted-foreground">Queued</span>;
    return (
      <span className="flex items-center gap-2">
        <StepIcon state={stateOf(run.current_state)} />
        {run.current_stage === "retry" ? "Retrying" : (labels[run.current_stage] ?? run.current_stage)}
      </span>
    );
  }
  const tone = RUN_STATUS[run.status].tone;
  return (
    <span className="flex items-center gap-2 text-sm text-muted-foreground">
      <StepIcon state={tone === "green" ? "done" : tone === "amber" ? "warning" : tone === "red" ? "failed" : "skipped"} />
      <span className="line-clamp-2">{run.final_reason ?? "Draft ready"}</span>
    </span>
  );
}

function AccountPlanCard({ plan, runs }: { plan: AccountPlan; runs: RunSummary[] }) {
  const statusOf = new Map(runs.map((r) => [r.id, r.status]));
  const people = [...plan.assignments].sort((a, b) => (a.send_day ?? 99) - (b.send_day ?? 99));
  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>{plan.company ?? plan.account_key} · account plan</CardTitle>
        <CardDescription>
          {people.length} people, one set of company research, a different opener each
          {plan.fallback && " (assigned by code after the model's plan broke a rule)"}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.min(people.length, 3)}, minmax(0, 1fr))` }}>
          {people.map((p) => (
            <Link key={p.run_id} href={`/runs/${p.run_id}`} className="space-y-2 rounded-lg border p-3 text-sm hover:bg-zinc-50">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-medium">{p.name}</p>
                  <p className="text-xs text-muted-foreground">{p.title}</p>
                </div>
                {p.send_day != null && (
                  <span className="shrink-0 rounded-md bg-zinc-100 px-1.5 py-0.5 text-xs font-medium">Day {p.send_day}</span>
                )}
              </div>
              {p.opener_type ? <HookChip type={p.opener_type} /> : <span className="text-xs text-muted-foreground">role-based email</span>}
              {p.opener_summary && <p className="text-xs">{p.opener_summary}</p>}
              {p.context_summary && <p className="text-xs text-muted-foreground">Context: {p.context_summary}</p>}
              {statusOf.get(p.run_id) && <StatusChip status={statusOf.get(p.run_id)!} />}
            </Link>
          ))}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {plan.checks.filter((c) => c.name !== "model_plan").map((c) => (
            <span key={c.name} className={c.passed ? "text-emerald-700" : "text-red-700"}>
              {c.passed ? "✓" : "✗"} {CHECK_LABELS[c.name] ?? c.name}
            </span>
          ))}
        </div>
        <p className="text-sm text-muted-foreground">{plan.counterfactual}</p>
      </CardContent>
    </Card>
  );
}

const CHECK_LABELS: Record<string, string> = {
  no_reused_openers: "No reused openers",
  company_context_once: "Company event used as context at most once",
};

function SimilarityLine({ batch }: { batch: BatchDetail["batch"] }) {
  const s = batch.similarity ?? {};
  if (batch.diversity_score == null || !s.drafts || s.drafts < 2) return null;
  const pairs = s.similar_pairs?.length ?? 0;
  const rehooked = s.rebalanced?.length ?? 0;
  return (
    <p className="text-sm text-muted-foreground">
      Draft diversity <span className="font-medium text-foreground tabular-nums">{batch.diversity_score.toFixed(2)}</span>
      {" · "}
      {pairs ? `${pairs} pair${pairs === 1 ? "" : "s"} read alike` : "no drafts read alike"}
      {rehooked > 0 && ` · ${rehooked} re-hooked (one source opened more than ${s.source_cap} drafts)`}
    </p>
  );
}

function HookChip({ type }: { type: string }) {
  return (
    <span className="rounded-md bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">{type.replace(/_/g, " ")}</span>
  );
}

function groupAccounts(runs: RunSummary[]): [string, RunSummary[]][] {
  const groups = new Map<string, RunSummary[]>();
  for (const run of runs) {
    if (!run.account_key) continue;
    groups.set(run.account_key, [...(groups.get(run.account_key) ?? []), run]);
  }
  return [...groups.entries()].filter(([, members]) => members.length > 1);
}

function applyEvent(detail: BatchDetail, event: LiveEvent): BatchDetail {
  switch (event.type) {
    case "batch_progress":
      return { ...detail, progress: { done: event.done, total: event.total, counts: event.counts } };
    case "stage":
      return {
        ...detail,
        runs: detail.runs.map((r) =>
          r.id === event.run_id ? { ...r, current_stage: event.stage, current_state: event.state } : r,
        ),
      };
    case "run_status":
      return {
        ...detail,
        runs: detail.runs.map((r) =>
          r.id === event.run_id
            ? { ...r, status: event.status, final_reason: event.final_reason, confidence: event.confidence }
            : r,
        ),
      };
    case "batch_status":
      return { ...detail, batch: { ...detail.batch, status: event.status } };
    default:
      return detail;
  }
}
