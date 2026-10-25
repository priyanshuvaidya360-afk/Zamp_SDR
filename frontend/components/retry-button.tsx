"use client";

import { LoaderCircle, RotateCcw } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import type { RetryResponse } from "@/lib/types";

// Retry a failed run in place (POST /runs/{id}/retry): it restarts at the stage that failed and
// reuses the research that is still fresh, so no CSV re-upload. Used by the prospect list and
// the run view.
export function RetryButton({
  runId,
  retriesLeft,
  onRetried,
}: {
  runId: number;
  retriesLeft: number;
  onRetried: (result: RetryResponse) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (retriesLeft <= 0) {
    return <span className="text-xs text-muted-foreground">No retries left: upload this prospect again</span>;
  }

  async function retry() {
    setBusy(true);
    setError(null);
    try {
      onRetried(await api.retryRun(runId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't retry this run.");
    }
    setBusy(false);
  }

  return (
    <span className="inline-flex flex-col items-end gap-1">
      <Button variant="outline" onClick={retry} disabled={busy}>
        {busy ? <LoaderCircle className="animate-spin" /> : <RotateCcw />}
        {busy ? "Retrying…" : "Retry run"}
      </Button>
      {error && <span className="max-w-64 text-right text-xs text-red-700">{error}</span>}
    </span>
  );
}
