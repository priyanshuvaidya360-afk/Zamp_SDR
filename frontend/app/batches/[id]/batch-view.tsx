"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { StatusChip } from "@/components/status-chip";
import { StepIcon } from "@/components/step-icon";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError, streams } from "@/lib/api";
import { stateOf } from "@/lib/stages";
import { RUN_STATUS } from "@/lib/status";
import type { BatchDetail, LiveEvent, RunStatus, RunSummary } from "@/lib/types";
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{detail.batch.filename}</h1>
          <p className="text-muted-foreground">
            Batch #{detail.batch.id} · {progress.total} prospect{progress.total === 1 ? "" : "s"}
            {detail.not_run.length > 0 && ` · ${detail.not_run.length} rows not run`}
          </p>
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
            {accounts.map(([key, runs]) => (
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
            ))}
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
