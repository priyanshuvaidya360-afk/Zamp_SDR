import { ExternalLink } from "lucide-react";

import type { DraftRecord, SignalRecord } from "@/lib/types";

// The email, laid out like a mail client: To / Subject, then the body. Every factual sentence is
// underlined and carries a small number that points to its source in the list underneath.
// Review actions live in components/review-controls.tsx; the quality checks in content-guide.tsx.
export function DraftPanel({ drafts, signals, to }: { drafts: DraftRecord[]; signals: SignalRecord[]; to?: string }) {
  if (!drafts.length) return null;
  const final = latestDraft(drafts)!;
  const report = final.critic_report;
  const bySignal = new Map(signals.map((s) => [s.id, s]));
  const body = final.edited_body ?? final.body;
  const cited = final.edited_body ? { body, sources: [] as SignalRecord[], parts: [body] as React.ReactNode[] } : citeBody(final, bySignal);
  const version = [
    report.mode === "outline" ? "Outline" : "Draft",
    `v${final.version}`,
    report.revalidated_from != null ? "corrected on re-check" : final.version > 1 ? "rewritten after critic notes" : null,
    final.reviewer_action === "edit" ? "edited by you" : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="space-y-4">
      {report.banner && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">{report.banner}</p>
      )}
      <article className="overflow-hidden rounded-xl border bg-white shadow-xs">
        <dl className="divide-y text-sm">
          {to && <Row label="To" value={to} />}
          <Row label="Subject" value={final.subject} strong />
        </dl>
        <div className="px-6 py-6 text-[15px] leading-7 whitespace-pre-wrap text-zinc-800">{cited.parts}</div>
        <p className="border-t bg-zinc-50/70 px-6 py-2.5 text-xs text-muted-foreground">{version}</p>
      </article>

      {cited.sources.length > 0 && (
        <section aria-label="Sources" className="rounded-xl border bg-white px-5 py-4">
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Sources for each fact</h3>
          <ol className="mt-3 space-y-2.5 text-sm">
            {cited.sources.map((s, i) => (
              <li key={s.id} className="flex gap-3">
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[11px] font-medium text-primary">
                  {i + 1}
                </span>
                <div className="min-w-0">
                  <p className="text-zinc-800">{s.summary}</p>
                  {s.source_url.startsWith("http") ? (
                    <a href={s.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
                      {new URL(s.source_url).hostname.replace(/^www\./, "")} <ExternalLink className="size-3" />
                    </a>
                  ) : (
                    <span className="text-xs text-muted-foreground">uploaded bio</span>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}
      {final.review.reason && <p className="text-sm text-muted-foreground">Rejected: {final.review.reason}</p>}
    </div>
  );
}

function Row({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="flex gap-4 px-6 py-3">
      <dt className="w-16 shrink-0 text-muted-foreground">{label}</dt>
      <dd className={strong ? "font-medium text-zinc-900" : "text-zinc-800"}>{value}</dd>
    </div>
  );
}

/** The version the reviewer acts on: the highest one (critic rewrite or re-check correction). */
export function latestDraft(drafts: DraftRecord[]): DraftRecord | undefined {
  return drafts.reduce<DraftRecord | undefined>((best, d) => (!best || d.version > best.version ? d : best), undefined);
}

/** The body with each claimed sentence underlined and numbered; sources in order of first use. */
function citeBody(draft: DraftRecord, signals: Map<number, SignalRecord>) {
  const body = draft.body;
  const spans = draft.claims
    .map((claim) => {
      const sentence = claim.sentence.replace(/^-\s*/, "").trim();
      const start = body.indexOf(sentence);
      return { start, end: start + sentence.length, signal: signals.get(claim.signal_id) };
    })
    .filter((s) => s.start >= 0 && s.signal)
    .sort((a, b) => a.start - b.start);

  const sources: SignalRecord[] = [];
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start < cursor) continue; // overlapping claim: keep the first
    let n = sources.findIndex((s) => s.id === span.signal!.id) + 1;
    if (!n) n = sources.push(span.signal!);
    parts.push(body.slice(cursor, span.start));
    parts.push(
      <span key={span.start} className="underline decoration-primary/40 decoration-2 underline-offset-4" title={span.signal!.summary}>
        {body.slice(span.start, span.end)}
        <sup className="ml-0.5 text-[10px] font-semibold text-primary no-underline">{n}</sup>
      </span>,
    );
    cursor = span.end;
  }
  parts.push(body.slice(cursor));
  return { body, sources, parts };
}
