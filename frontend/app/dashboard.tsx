"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { StatusChip } from "@/components/status-chip";
import { Button, buttonVariants } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import { RUN_STATUS } from "@/lib/status";
import type { BatchListItem, Page, RunStatus, RunSummary, Stats } from "@/lib/types";
import { cn } from "@/lib/utils";

// Spec: docs/GUIDE.md "UI: three screens" > 3 Dashboard, and T16. Every number comes from GET
// /stats; definitions are in backend/app/routes/stats.py. Each panel loads, fails and empties on
// its own, so one slow query never blanks the page.

const PAGE = 10;
const RUN_FILTERS: RunStatus[] = [
  "ready_for_review", "approved", "rejected", "needs_input", "low_signal", "held", "blocked", "suppressed", "failed", "running",
];

type Load<T> = { data: T | null; error: string | null; loading: boolean };

function useLoad<T>(fetcher: () => Promise<T>, deps: unknown[]): Load<T> & { reload: () => void } {
  const [state, setState] = useState<Load<T>>({ data: null, error: null, loading: true });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    fetcher().then(
      (data) => live && setState({ data, error: null, loading: false }),
      (e) => live && setState((s) => ({ ...s, error: e instanceof ApiError ? e.message : "Couldn't load this.", loading: false })),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}

export function Dashboard() {
  const stats = useLoad(() => api.getStats(), []);
  const [tab, setTab] = useState<"batches" | "runs">("batches");

  // While anything is running, keep the numbers fresh.
  const running = stats.data?.running ?? 0;
  const { reload } = stats;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  }, [running, reload]);

  return (
    <div className="space-y-6">
      <Metrics state={stats} />
      <div className="space-y-3">
        <div role="tablist" aria-label="Lists" className="flex gap-1 border-b">
          {(["batches", "runs"] as const).map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={cn(
                "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
                tab === t ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {t === "batches" ? "Batches" : "All runs"}
            </button>
          ))}
        </div>
        <div role="tabpanel">{tab === "batches" ? <BatchesPanel /> : <RunsPanel />}</div>
      </div>
    </div>
  );
}

// --- Metrics ------------------------------------------------------------------------------------

function Metrics({ state }: { state: Load<Stats> & { reload: () => void } }) {
  if (state.error && !state.data) return <PanelError message={state.error} onRetry={state.reload} />;
  if (!state.data) return <Skeleton className="h-28" />;
  const s = state.data;
  if (s.runs_total === 0 && s.batches_this_week === 0)
    return (
      <Empty>
        No runs yet. <Link href="/batches/new" className="text-primary hover:underline">Upload a prospect CSV</Link> to start.
      </Empty>
    );

  const cards = [
    { label: "Batches this week", value: String(s.batches_this_week), note: "last 7 days" },
    { label: "Prospects this week", value: String(s.prospects_this_week), note: "runs started, last 7 days" },
    {
      label: "Ready for review",
      value: pct(s.ready_for_review_rate),
      note: `${s.reached_review} of ${s.runs_finished} finished runs reached review`,
    },
    { label: "Approval rate", value: pct(s.approval_rate), note: `${s.approved} approved of ${s.approved + s.rejected} reviewed` },
    { label: "Median run time", value: duration(s.median_run_seconds), note: "start to finish" },
    {
      label: "Avg credits / prospect",
      value: s.avg_credits_per_prospect == null ? "–" : s.avg_credits_per_prospect.toFixed(2),
      note: "search credits, finished runs",
    },
  ];
  const notReady = Object.entries(s.not_ready) as [RunStatus, number][];
  const notReadyTotal = notReady.reduce((n, [, c]) => n + c, 0);

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <section aria-label="Metrics" className="grid grid-cols-2 gap-3 md:grid-cols-3">
        {cards.map((c) => (
          <div key={c.label} className="rounded-xl border bg-white p-4">
            <p className="text-xs text-muted-foreground">{c.label}</p>
            <p className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{c.value}</p>
            <p className="mt-1 text-[11px] text-muted-foreground">{c.note}</p>
          </div>
        ))}
      </section>
      <section aria-label="Why not ready" className="rounded-xl border bg-white p-4">
        <p className="text-sm font-medium">Why not ready</p>
        <p className="text-xs text-muted-foreground">
          {notReadyTotal} of {s.runs_finished} finished runs did not reach the review queue
          {s.running > 0 && ` · ${s.running} still running`}
        </p>
        <ul className="mt-3 space-y-2">
          {notReady.map(([status, n]) => (
            <li key={status} className="grid grid-cols-[7.5rem_minmax(0,1fr)_2rem] items-center gap-2 text-sm">
              <span className="justify-self-start">
                <StatusChip status={status} />
              </span>
              <span className="h-1.5 overflow-hidden rounded-full bg-zinc-100">
                <span
                  className="block h-full rounded-full bg-zinc-400"
                  style={{ width: `${notReadyTotal ? (n / notReadyTotal) * 100 : 0}%` }}
                />
              </span>
              <span className="text-right tabular-nums text-muted-foreground">{n}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

// --- Batches ------------------------------------------------------------------------------------

function BatchesPanel() {
  const [offset, setOffset] = useState(0);
  const page = useLoad(() => api.listBatches(PAGE, offset), [offset]);
  if (page.error && !page.data) return <PanelError message={page.error} onRetry={page.reload} />;
  if (!page.data) return <Skeleton className="h-48" />;
  if (page.data.total === 0)
    return (
      <Empty>
        No batches yet. <Link href="/batches/new" className="text-primary hover:underline">Upload one</Link>.
      </Empty>
    );
  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-xl border bg-white">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>File</TableHead>
              <TableHead>Uploaded</TableHead>
              <TableHead className="text-right">Rows</TableHead>
              <TableHead>Progress</TableHead>
              <TableHead>Outcomes</TableHead>
              <TableHead className="text-right">Diversity</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.data.items.map((b) => (
              <BatchRow key={b.id} batch={b} />
            ))}
          </TableBody>
        </Table>
      </div>
      <Pager page={page.data} offset={offset} onChange={setOffset} noun="batches" />
    </div>
  );
}

function BatchRow({ batch: b }: { batch: BatchListItem }) {
  const percent = b.runs ? Math.round((b.done / b.runs) * 100) : 0;
  const outcomes = Object.entries(b.counts).filter(([s, n]) => n && s !== "running") as [RunStatus, number][];
  return (
    <TableRow>
      <TableCell className="max-w-56">
        <Link href={`/batches/${b.id}`} className="block truncate font-medium hover:underline">
          {b.filename}
        </Link>
      </TableCell>
      <TableCell className="whitespace-nowrap text-muted-foreground">{when(b.uploaded_at)}</TableCell>
      <TableCell className="text-right tabular-nums" title={`${b.valid} valid · ${b.invalid} invalid · ${b.duplicates} duplicates`}>
        {b.rows}
        {b.rows !== b.valid && <span className="text-muted-foreground"> ({b.valid} valid)</span>}
      </TableCell>
      <TableCell className="min-w-36">
        {b.status === "validating" ? (
          <span className="text-muted-foreground">Not started</span>
        ) : (
          <div className="flex items-center gap-2">
            <div className="h-1.5 w-20 overflow-hidden rounded-full bg-zinc-100">
              <div className="h-full rounded-full bg-primary" style={{ width: `${percent}%` }} />
            </div>
            <span className="text-xs tabular-nums text-muted-foreground">
              {b.done}/{b.runs}
            </span>
          </div>
        )}
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap gap-1.5">
          {outcomes.length ? (
            outcomes.map(([status, n]) => (
              <span key={status} className="inline-flex items-center gap-1">
                <StatusChip status={status} />
                <span className="text-xs tabular-nums text-muted-foreground">{n}</span>
              </span>
            ))
          ) : (
            <span className="text-muted-foreground">–</span>
          )}
        </div>
      </TableCell>
      <TableCell className="text-right tabular-nums">{b.diversity_score == null ? "–" : b.diversity_score.toFixed(2)}</TableCell>
    </TableRow>
  );
}

// --- All runs -----------------------------------------------------------------------------------

function RunsPanel() {
  const [status, setStatus] = useState<RunStatus | "">("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(query);
      setOffset(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  const page = useLoad(() => api.listRuns({ status, q: search, limit: PAGE, offset }), [status, search, offset]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="run-search">
          Search by name or company
        </label>
        <input
          id="run-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search name or company"
          className="h-8 w-64 max-w-full rounded-lg border bg-white px-2.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        />
        <label className="sr-only" htmlFor="run-status">
          Status
        </label>
        <select
          id="run-status"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as RunStatus | "");
            setOffset(0);
          }}
          className="h-8 rounded-lg border bg-white px-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <option value="">All statuses</option>
          {RUN_FILTERS.map((s) => (
            <option key={s} value={s}>
              {RUN_STATUS[s].label}
            </option>
          ))}
        </select>
      </div>
      {page.error && !page.data ? (
        <PanelError message={page.error} onRetry={page.reload} />
      ) : !page.data ? (
        <Skeleton className="h-48" />
      ) : page.data.total === 0 ? (
        <Empty>{status || search ? "No runs match these filters." : "No runs yet."}</Empty>
      ) : (
        <>
          <div className="overflow-x-auto rounded-xl border bg-white">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Prospect</TableHead>
                  <TableHead>Company</TableHead>
                  <TableHead>Batch</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Hook</TableHead>
                  <TableHead className="text-right">Confidence</TableHead>
                  <TableHead className="text-right">Credits</TableHead>
                  <TableHead className="text-right">Run time</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {page.data.items.map((r) => (
                  <RunRow key={r.id} run={r} />
                ))}
              </TableBody>
            </Table>
          </div>
          <Pager page={page.data} offset={offset} onChange={setOffset} noun="runs" />
        </>
      )}
    </div>
  );
}

function RunRow({ run: r }: { run: RunSummary }) {
  const seconds = r.finished_at ? (parseUtc(r.finished_at) - parseUtc(r.started_at)) / 1000 : null;
  return (
    <TableRow>
      <TableCell>
        <Link href={`/runs/${r.id}`} className="font-medium hover:underline">
          {r.name}
        </Link>
        {r.title && <span className="block text-xs text-muted-foreground">{r.title}</span>}
      </TableCell>
      <TableCell>{r.company ?? "–"}</TableCell>
      <TableCell>
        <Link href={`/batches/${r.batch_id}`} className="text-muted-foreground hover:underline">
          #{r.batch_id}
        </Link>
      </TableCell>
      <TableCell title={r.final_reason ?? undefined}>
        <StatusChip status={r.status} />
      </TableCell>
      <TableCell className="text-muted-foreground">{r.hook_type?.replace(/_/g, " ") ?? "–"}</TableCell>
      <TableCell className="text-right tabular-nums">{r.confidence == null ? "–" : r.confidence.toFixed(2)}</TableCell>
      <TableCell className="text-right tabular-nums">{r.cost_credits}</TableCell>
      <TableCell className="text-right tabular-nums text-muted-foreground">{duration(seconds)}</TableCell>
    </TableRow>
  );
}

// --- Shared bits --------------------------------------------------------------------------------

function Pager<T>({ page, offset, onChange, noun }: { page: Page<T>; offset: number; onChange: (o: number) => void; noun: string }) {
  if (page.total <= PAGE) return null;
  return (
    <div className="flex items-center justify-between text-sm text-muted-foreground">
      <span>
        {offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total} {noun}
      </span>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE))}>
          Previous
        </Button>
        <Button variant="outline" size="sm" disabled={offset + PAGE >= page.total} onClick={() => onChange(offset + PAGE)}>
          Next
        </Button>
      </div>
    </div>
  );
}

function PanelError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
      <span>{message}</span>
      <button onClick={onRetry} className={buttonVariants({ variant: "outline", size: "sm" })}>
        Try again
      </button>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded-xl border border-dashed bg-white px-4 py-10 text-center text-sm text-muted-foreground">{children}</p>;
}

function Skeleton({ className }: { className: string }) {
  return <div className={cn("rounded-xl bg-zinc-100", className)} role="status" aria-label="Loading" />;
}

function parseUtc(value: string): number {
  // The API sends UTC; SQLite drops the offset, so treat a bare timestamp as UTC.
  return Date.parse(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
}

function when(value: string): string {
  return new Date(parseUtc(value)).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function pct(value: number | null): string {
  return value == null ? "–" : `${Math.round(value * 100)}%`;
}

function duration(seconds: number | null): string {
  if (seconds == null) return "–";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  return `${Math.floor(seconds / 60)} m ${Math.round(seconds % 60)} s`;
}
