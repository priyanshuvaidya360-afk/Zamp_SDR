import { Check, X } from "lucide-react";

import type { DraftRecord, SignalRecord } from "@/lib/types";

// Right column: the final draft with every factual sentence underlined and linked to its source,
// plus the critic's checklist. Review actions (approve / edit / reject) arrive with T15.
export function DraftPanel({ drafts, signals }: { drafts: DraftRecord[]; signals: SignalRecord[] }) {
  if (!drafts.length) return null;
  const final = drafts.find((d) => d.critic_report.final) ?? drafts[drafts.length - 1];
  const report = final.critic_report;
  const bySignal = new Map(signals.map((s) => [s.id, s]));
  const warnings = (report.warnings ?? []).filter((w) => w !== report.banner);

  return (
    <div className="space-y-3">
      {report.banner && (
        <p className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">{report.banner}</p>
      )}
      <article className="rounded-xl border bg-white p-4">
        <p className="text-xs text-muted-foreground">
          {report.mode === "outline" ? "Outline" : "Draft"} · version {final.version}
          {final.version > 1 && " (rewritten after the critic's notes)"}
        </p>
        <p className="mt-2 text-sm">
          <span className="text-muted-foreground">Subject: </span>
          <span className="font-medium">{final.subject}</span>
        </p>
        <div className="mt-3 text-sm leading-relaxed whitespace-pre-wrap">
          <CitedBody body={final.body} claims={final.claims} signals={bySignal} />
        </div>
      </article>

      {report.checks && (
        <div className="rounded-xl border bg-white p-4 text-sm">
          <p className="font-medium">Critic</p>
          <ul className="mt-2 space-y-1">
            {report.checks.map((c) => (
              <li key={c.name} className="flex items-start gap-2">
                {c.passed ? <Check className="mt-0.5 size-3.5 text-emerald-600" /> : <X className="mt-0.5 size-3.5 text-red-600" />}
                <span>
                  {c.name.replace(/_/g, " ")}
                  {c.detail && <span className="text-xs text-muted-foreground"> · {c.detail}</span>}
                </span>
              </li>
            ))}
            {report.genericness && (
              <li className="flex items-start gap-2">
                {report.genericness.genericness < 3 ? (
                  <Check className="mt-0.5 size-3.5 text-emerald-600" />
                ) : (
                  <X className="mt-0.5 size-3.5 text-red-600" />
                )}
                <span>
                  specific to them ({report.genericness.genericness}/5 generic)
                  <span className="block text-xs text-muted-foreground">{report.genericness.reason}</span>
                </span>
              </li>
            )}
          </ul>
          {warnings.length > 0 && (
            <ul className="mt-3 space-y-1 text-xs text-amber-800">
              {warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          {report.confidence_formula && (
            <p className="mt-3 text-xs text-muted-foreground">
              Confidence {report.confidence} = {report.confidence_formula}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** The body with each claimed sentence underlined and linked to the signal it relies on. */
function CitedBody({
  body,
  claims,
  signals,
}: {
  body: string;
  claims: DraftRecord["claims"];
  signals: Map<number, SignalRecord>;
}) {
  const spans = claims
    .map((claim) => {
      const sentence = claim.sentence.replace(/^-\s*/, "").trim();
      const start = body.indexOf(sentence);
      return { start, end: start + sentence.length, signal: signals.get(claim.signal_id) };
    })
    .filter((s) => s.start >= 0 && s.signal)
    .sort((a, b) => a.start - b.start);

  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start < cursor) continue; // overlapping claim: keep the first
    parts.push(body.slice(cursor, span.start));
    parts.push(
      <a
        key={span.start}
        href={span.signal!.source_url.startsWith("http") ? span.signal!.source_url : undefined}
        target="_blank"
        rel="noreferrer"
        title={`Source: ${span.signal!.summary}`}
        className="underline decoration-primary/50 decoration-2 underline-offset-4 hover:decoration-primary"
      >
        {body.slice(span.start, span.end)}
      </a>,
    );
    cursor = span.end;
  }
  parts.push(body.slice(cursor));
  return <>{parts}</>;
}
