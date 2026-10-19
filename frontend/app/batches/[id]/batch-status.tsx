"use client";

import { useEffect, useState } from "react";

import { StatusChip } from "@/components/status-chip";
import { api, ApiError } from "@/lib/api";
import type { BatchDetail } from "@/lib/types";

// Minimal progress view so "Run" has somewhere to land; T11 replaces it with the live
// (SSE) batch view.
export function BatchStatus({ batchId }: { batchId: number }) {
  const [detail, setDetail] = useState<BatchDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    async function poll() {
      try {
        const next = await api.getBatch(batchId);
        if (stopped) return;
        setDetail(next);
        if (next.batch.status !== "done") timer = setTimeout(poll, 2000);
      } catch (e) {
        if (!stopped) setError(e instanceof ApiError ? e.message : "Couldn't load this batch.");
      }
    }
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [batchId]);

  if (error) return <p className="text-red-700">{error}</p>;
  if (!detail) return <p className="text-muted-foreground">Loading…</p>;

  const { progress } = detail;
  return (
    <div className="space-y-4">
      <p className="text-muted-foreground">
        {detail.batch.filename} · {progress.done} of {progress.total} done
      </p>
      <ul className="divide-y rounded-xl border bg-white">
        {detail.runs.map((run) => (
          <li key={run.id} className="flex items-center justify-between gap-4 px-4 py-3">
            <div>
              <p className="font-medium">{run.name}</p>
              <p className="text-sm text-muted-foreground">
                {run.company ?? "—"} · {run.final_reason ?? run.current_stage ?? "queued"}
              </p>
            </div>
            <StatusChip status={run.status} />
          </li>
        ))}
      </ul>
    </div>
  );
}
