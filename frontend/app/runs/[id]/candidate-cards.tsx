"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { Candidate } from "@/lib/types";

// needs_input: the run stopped because identity was unclear. The rep picks; the run resumes.
export function CandidateCards({
  reason,
  candidates,
  onPick,
}: {
  reason: string;
  candidates: Candidate[];
  onPick: (candidate: Candidate) => Promise<void>;
}) {
  const [busy, setBusy] = useState<number | null>(null);
  return (
    <div className="space-y-3 rounded-xl border border-amber-300 bg-amber-50/60 p-4">
      <div>
        <p className="font-medium text-amber-900">Which person is this?</p>
        <p className="text-sm text-amber-900/80">{reason} Nothing was guessed.</p>
      </div>
      {candidates.length === 0 && <p className="text-sm text-muted-foreground">No candidates were found in public results.</p>}
      <div className="grid gap-3 md:grid-cols-2">
        {candidates.map((c, i) => (
          <div key={`${c.name}-${i}`} className="flex flex-col gap-2 rounded-lg border bg-white p-3 text-sm">
            <div>
              <p className="font-medium">{c.name}</p>
              <p className="text-muted-foreground">{[c.title, c.company, c.location].filter(Boolean).join(" · ") || "No details"}</p>
            </div>
            <p className="text-xs text-muted-foreground">“{c.evidence}”</p>
            <div className="mt-auto flex items-center justify-between gap-2">
              <a href={c.evidence_url} target="_blank" rel="noreferrer" className="truncate text-xs text-primary hover:underline">
                {c.evidence_url.replace(/^https?:\/\/(www\.)?/, "")}
              </a>
              <span className="text-xs text-muted-foreground tabular-nums">{c.confidence.toFixed(2)}</span>
            </div>
            <Button
              size="sm"
              disabled={busy !== null}
              onClick={async () => {
                setBusy(i);
                try {
                  await onPick(c);
                } finally {
                  setBusy(null);
                }
              }}
            >
              {busy === i ? "Resuming…" : "This is the one"}
            </Button>
          </div>
        ))}
      </div>
    </div>
  );
}
