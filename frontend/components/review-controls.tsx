"use client";

import { Check, LoaderCircle, Pencil, RotateCcw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { wordDiff } from "@/lib/diff";
import type { DraftRecord, ReviewResult } from "@/lib/types";

// Approve / edit / reject for one draft (stage 8). Spec: docs/GUIDE.md case 10 and "UI: three
// screens". Approving an old draft with time-sensitive facts first shows "Re-checking N facts";
// a changed fact comes back as a red/green diff to approve again. Nothing is ever sent.
// Callers key it by draft id, so a new draft starts with fresh state.

type Mode = "idle" | "editing" | "rejecting" | "rechecking" | "busy";

export function ReviewControls({
  runId,
  draft,
  recheckFacts,
  shortcuts = false,
  onDone,
}: {
  runId: number;
  draft: DraftRecord;
  recheckFacts: number;
  shortcuts?: boolean;
  /** Called after every answer from the server (the caller reloads, or moves to the next draft). */
  onDone: (result: ReviewResult) => void;
}) {
  const [mode, setMode] = useState<Mode>("idle");
  const [body, setBody] = useState(draft.body);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [changed, setChanged] = useState<ReviewResult | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const send = useCallback(
    async (action: "approve" | "edit" | "reject", opts: { body?: string; reason?: string; draftId?: number } = {}) => {
      setError(null);
      setMode(action === "approve" && recheckFacts > 0 && !opts.draftId ? "rechecking" : "busy");
      try {
        const result = await api.review(runId, action, { draftId: draft.id, ...opts });
        setMode("idle");
        if (result.outcome === "changed") {
          setChanged(result);
          return;
        }
        setChanged(null);
        if (result.outcome === "gone") setNotice(result.message);
        onDone(result);
      } catch (e) {
        setMode("idle");
        setError(e instanceof ApiError ? e.message : "Couldn't save the review.");
      }
    },
    [runId, draft.id, recheckFacts, onDone],
  );

  // a approve · e edit · r reject (not while typing, not while the diff is open).
  useEffect(() => {
    if (!shortcuts || changed || mode !== "idle") return;
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (target.closest("input, textarea, select, [contenteditable]") || event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === "a") send("approve");
      else if (event.key === "e") setMode("editing");
      else if (event.key === "r") setMode("rejecting");
      else return;
      event.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shortcuts, changed, mode, send]);

  const waiting = mode === "busy" || mode === "rechecking";

  return (
    <div className="space-y-3 rounded-xl border bg-white p-4">
      {mode === "editing" ? (
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            send("edit", { body });
          }}
        >
          <label htmlFor={`edit-${draft.id}`} className="text-sm font-medium">
            Edit the email
          </label>
          <textarea
            id={`edit-${draft.id}`}
            autoFocus
            value={body}
            onChange={(e) => setBody(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setMode("idle");
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send("edit", { body });
            }}
            rows={10}
            className="w-full rounded-lg border px-3 py-2 text-sm leading-relaxed outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={!body.trim() || body.trim() === draft.body.trim()}>
              Save and approve
            </Button>
            <Button type="button" variant="ghost" onClick={() => setMode("idle")}>
              Cancel
            </Button>
            <span className="self-center text-xs text-muted-foreground">⌘↩ to save · Esc to cancel</span>
          </div>
        </form>
      ) : mode === "rejecting" ? (
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            send("reject", { reason: reason.trim() || undefined });
          }}
        >
          <label htmlFor={`reject-${draft.id}`} className="text-sm font-medium">
            Why reject it? <span className="font-normal text-muted-foreground">(optional)</span>
          </label>
          <input
            id={`reject-${draft.id}`}
            autoFocus
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setMode("idle")}
            placeholder="e.g. wrong persona, hook too thin"
            className="h-8 w-full rounded-lg border px-2.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          />
          <div className="flex gap-2">
            <Button type="submit" variant="destructive">
              Reject
            </Button>
            <Button type="button" variant="ghost" onClick={() => setMode("idle")}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => send("approve")} disabled={waiting}>
            <Check /> Approve {shortcuts && <Kbd>a</Kbd>}
          </Button>
          <Button variant="outline" onClick={() => setMode("editing")} disabled={waiting}>
            <Pencil /> Edit {shortcuts && <Kbd>e</Kbd>}
          </Button>
          <Button variant="outline" onClick={() => setMode("rejecting")} disabled={waiting}>
            <X /> Reject {shortcuts && <Kbd>r</Kbd>}
          </Button>
        </div>
      )}

      {mode === "rechecking" && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
          <LoaderCircle className="size-4 animate-spin" />
          Re-checking {recheckFacts} time-sensitive fact{recheckFacts === 1 ? "" : "s"} against the live page…
        </p>
      )}
      {mode === "idle" && recheckFacts > 0 && !changed && (
        <p className="text-xs text-muted-foreground">
          This draft is older than 3 days: approving re-checks {recheckFacts} time-sensitive fact
          {recheckFacts === 1 ? "" : "s"} first.
        </p>
      )}
      {notice && <p className="text-sm text-amber-800">{notice}</p>}
      {error && (
        <p className="text-sm text-red-700" role="alert">
          {error}
        </p>
      )}

      {changed && (
        <DiffModal
          result={changed}
          busy={mode === "busy"}
          onApprove={() => send("approve", { draftId: changed.draft_id })}
          onClose={() => {
            const result = changed;
            setChanged(null);
            onDone(result); // the corrected version now waits in the queue
          }}
        />
      )}
    </div>
  );
}

function DiffModal({
  result,
  busy,
  onApprove,
  onClose,
}: {
  result: ReviewResult;
  busy: boolean;
  onApprove: () => void;
  onClose: () => void;
}) {
  const changes = result.checks.filter((c) => c.verdict === "changed");
  const parts = wordDiff(result.diff?.before ?? "", result.diff?.after ?? "");
  const approveRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    approveRef.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      if (event.key === "a" && !busy) {
        event.preventDefault();
        onApprove();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onApprove, onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="diff-title"
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="diff-title" className="text-lg font-semibold">
          A fact changed since this draft was written
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">{result.message}.</p>
        <ul className="mt-3 space-y-2 text-sm">
          {changes.map((c) => (
            <li key={c.signal_id} className="rounded-lg bg-zinc-50 p-3">
              <p>{c.reason}</p>
              <a href={c.source_url} target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">
                {c.source_url.replace(/^https?:\/\/(www\.)?/, "")}
              </a>
            </li>
          ))}
        </ul>
        <div className="mt-4 rounded-lg border p-3 text-sm leading-relaxed whitespace-pre-wrap">
          {parts.map((p, i) =>
            p.kind === "same" ? (
              <span key={i}>{p.text}</span>
            ) : p.kind === "removed" ? (
              <del key={i} className="rounded bg-red-100 px-0.5 text-red-800" aria-label={`removed: ${p.text}`}>
                {p.text}
              </del>
            ) : (
              <ins key={i} className="rounded bg-emerald-100 px-0.5 text-emerald-800 no-underline" aria-label={`added: ${p.text}`}>
                {p.text}
              </ins>
            ),
          )}
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button ref={approveRef} onClick={onApprove} disabled={busy}>
            {busy ? <LoaderCircle className="animate-spin" /> : <Check />} Approve corrected version <Kbd>a</Kbd>
          </Button>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            <RotateCcw /> Not now
          </Button>
        </div>
      </div>
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="ml-1 rounded border border-current/30 px-1 font-mono text-[10px] opacity-70">{children}</kbd>;
}
