import { ExternalLink } from "lucide-react";

import type { DraftRecord, SignalRecord } from "@/lib/types";

// The email, laid out like a mail client: To / Subject, then the body. Every factual sentence is
// underlined and carries a small number that points to its source in the list underneath.
// Review actions live in components/review-controls.tsx; the quality checks in content-guide.tsx.
export function DraftPanel({
  drafts,
  signals,
  to,
  from,
}: {
  drafts: DraftRecord[];
  signals: SignalRecord[];
  to?: string;
  from?: string;
}) {
  if (!drafts.length) return null;
  const final = latestDraft(drafts)!;
  const report = final.critic_report;
  const bySignal = new Map(signals.map((s) => [s.id, s]));
  const cited = final.edited_body ? { sources: [] as SignalRecord[], parts: [final.edited_body] as React.ReactNode[] } : citeBody(final, bySignal);
  const version = [
    report.mode === "outline" ? "Outline" : "Draft",
    `v${final.version}`,
    report.revalidated_from != null ? "corrected on re-check" : final.version > 1 ? "rewritten after critic notes" : null,
    final.reviewer_action === "edit" ? "edited by you" : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const sender = from || "Halyard";

  return (
    <div className="space-y-4">
      {report.banner && (
        <p className="rounded-xl border border-[#f7d6b2] bg-[#fff4e8] px-4 py-3 text-sm text-[#a14a00]">{report.banner}</p>
      )}
      <article className="overflow-hidden rounded-[14px] border bg-white">
        <div className="flex items-center gap-1.5 border-b bg-zinc-50 px-5 py-2.5 text-[13px] text-muted-foreground">
          <span className="font-semibold text-navy">Outbox</span>
          <span>›</span>
          <span>Draft</span>
        </div>
        <div className="flex flex-wrap items-center gap-3 px-8 pt-6 pb-2">
          <h2 className="text-2xl font-medium tracking-tight">{final.subject}</h2>
          <span className="rounded-[5px] bg-secondary px-2 py-0.5 text-xs font-semibold text-primary">{version}</span>
        </div>
        <div className="flex items-start gap-3.5 px-8 py-4">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-full bg-[#3b82f6] text-base font-semibold text-white">
            {sender.charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-bold">{sender}</p>
            {to && <p className="text-[13px] text-muted-foreground">to {to} ▾</p>}
          </div>
        </div>
        <div className="max-w-[860px] px-8 pb-8 font-serif text-[17px] leading-[1.75] whitespace-pre-wrap text-zinc-800 sm:pl-[86px]">
          {cited.parts}
        </div>
        {cited.sources.length > 0 && (
          <section aria-label="Sources" className="space-y-3 border-t px-8 pt-4.5 pb-5.5 sm:pl-[86px]">
            <h3 className="text-xs font-bold tracking-[0.08em] text-primary uppercase">Sources for each fact</h3>
            {cited.sources.map((s, i) => (
              <div key={s.id} className="flex max-w-[560px] items-center gap-3.5 rounded-[10px] border bg-zinc-50 px-4 py-3">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-[7px] bg-primary text-[13px] font-bold text-white">
                  {i + 1}
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-medium">{s.summary}</p>
                  {s.source_url.startsWith("http") ? (
                    <a href={s.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[13px] font-semibold text-primary hover:underline">
                      {new URL(s.source_url).hostname.replace(/^www\./, "")} <ExternalLink className="size-3" />
                    </a>
                  ) : (
                    <span className="text-[13px] text-muted-foreground">uploaded bio</span>
                  )}
                </div>
              </div>
            ))}
          </section>
        )}
        <p className="border-t bg-zinc-50 px-8 py-2.5 text-xs text-muted-foreground">Nothing is sent from Signal. Approve, edit or reject below.</p>
      </article>
      {final.review.reason && <p className="text-sm text-muted-foreground">Rejected: {final.review.reason}</p>}
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
      <span key={span.start} className="border-b-2 border-[#3b82f6] bg-secondary px-0.5" title={span.signal!.summary}>
        {body.slice(span.start, span.end)}
        <sup className="ml-0.5 font-sans text-[11px] font-bold text-primary">{n}</sup>
      </span>,
    );
    cursor = span.end;
  }
  parts.push(body.slice(cursor));
  return { body, sources, parts };
}
