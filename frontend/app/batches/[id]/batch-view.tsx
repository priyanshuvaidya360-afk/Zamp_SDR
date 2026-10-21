"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { StatusChip } from "@/components/status-chip";
import { StepIcon } from "@/components/step-icon";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError, streams } from "@/lib/api";
import { stateOf } from "@/lib/stages";
import { RUN_STATUS } from "@/lib/status";
import type { AccountPlan, BatchDetail, LiveEvent, RunStatus, RunSummary } from "@/lib/types";
import { useEventStream } from "@/lib/use-event-stream";

// Spec: docs/GUIDE.md "UI: three screens" > 1b Batch view, and T11.

export function BatchView({ batchId }: { batchId: number }) {
  const router = useRouter();
  const [detail, setDetail] = useState<BatchDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
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

  const live = detail !== null && detail.batch.status !== "done";
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

  if (error) return <p className="text-red-700">{error}</p>;
  if (!detail) return <p className="text-muted-foreground">Loading…</p>;

  const { progress } = detail;
  const percent = progress.total ? Math.round((progress.done / progress.total) * 100) : 0;
  const counts = Object.entries(progress.counts).filter(([, n]) => n) as [RunStatus, number][];
  const waiting = detail.runs.filter((r) => r.status === "ready_for_review").length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{detail.batch.filename}</h1>
          <p className="text-muted-foreground">
            Batch #{detail.batch.id} · {progress.total} prospect{progress.total === 1 ? "" : "s"}
            {detail.not_run.length > 0 && ` · ${detail.not_run.length} rows not run`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href={`/batches/${batchId}/review`}
            className={buttonVariants({ variant: waiting ? "default" : "outline" })}
            aria-disabled={!waiting}
          >
            Review queue{waiting ? ` (${waiting})` : ""}
          </Link>
          <a href={api.exportUrl(batchId)} className={buttonVariants({ variant: "outline" })}>
            Export CSV
          </a>
          <a href={api.exportUrl(batchId, true)} className="text-xs text-muted-foreground hover:text-foreground hover:underline">
            all rows
          </a>
        </div>
      </div>

      <Card>
        <CardContent className="space-y-3">
          <div className="flex items-baseline justify-between text-sm">
            <span className="font-medium">
              {progress.done} of {progress.total} done
            </span>
            <span className="text-muted-foreground">{live ? "Running…" : "Finished"}</span>
          </div>
          <div
            className="h-2 overflow-hidden rounded-full bg-zinc-100"
            role="progressbar"
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${percent}%` }} />
          </div>
          <SimilarityLine batch={detail.batch} />
          <div className="flex flex-wrap gap-2">
            {counts.map(([status, n]) => (
              <span key={status} className="inline-flex items-center gap-1.5 text-sm">
                <StatusChip status={status} />
                <span className="tabular-nums text-muted-foreground">{n}</span>
              </span>
            ))}
          </div>
        </CardContent>
      </Card>

      {accounts.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-medium tracking-wide text-muted-foreground uppercase">
            Accounts with several people
          </h2>
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
                          <StatusChip status={run.status} />
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

      <Card>
        <CardContent>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Prospect</TableHead>
                  <TableHead>Company</TableHead>
                  <TableHead>Stage</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Hook</TableHead>
                  <TableHead className="text-right">Confidence</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {detail.runs.map((run) => (
                  <TableRow
                    key={run.id}
                    className="cursor-pointer"
                    onClick={() => router.push(`/runs/${run.id}`)}
                  >
                    <TableCell>
                      <Link href={`/runs/${run.id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>
                        {run.name}
                      </Link>
                      {run.title && <p className="text-xs text-muted-foreground">{run.title}</p>}
                    </TableCell>
                    <TableCell>{run.company ?? <span className="text-muted-foreground">—</span>}</TableCell>
                    <TableCell className="max-w-80">
                      <StageCell run={run} labels={labels} />
                    </TableCell>
                    <TableCell>
                      <StatusChip status={run.status} />
                    </TableCell>
                    <TableCell>{run.hook_type ? <HookChip type={run.hook_type} /> : <span className="text-muted-foreground">—</span>}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {run.confidence != null ? run.confidence.toFixed(2) : <span className="text-muted-foreground">—</span>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {detail.not_run.length > 0 && (
        <details className="rounded-xl border bg-white px-4 py-3 text-sm">
          <summary className="cursor-pointer font-medium">
            {detail.not_run.length} row{detail.not_run.length === 1 ? " was" : "s were"} not run
          </summary>
          <ul className="mt-2 space-y-1 text-muted-foreground">
            {detail.not_run.map((row) => (
              <li key={row.row_number}>
                Row {row.row_number}
                {row.name && ` · ${row.name}`}: {row.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function StageCell({ run, labels }: { run: RunSummary; labels: Record<string, string> }) {
  if (run.status === "running") {
    if (!run.current_stage) return <span className="text-muted-foreground">Queued</span>;
    return (
      <span className="flex items-center gap-2">
        <StepIcon state={stateOf(run.current_state)} />
        {labels[run.current_stage] ?? run.current_stage}
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
