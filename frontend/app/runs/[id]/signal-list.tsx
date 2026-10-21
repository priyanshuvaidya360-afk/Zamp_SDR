import { ExternalLink } from "lucide-react";

import type { SignalRecord } from "@/lib/types";
import { cn } from "@/lib/utils";

// Signals tab: the hook the email is built on, up top, with the angle; every other signal below
// as one line you can open for the evidence, the score and why it wasn't used.
export function SignalList({ signals, angle, waiting }: { signals: SignalRecord[]; angle: string | null; waiting: boolean }) {
  if (!signals.length) {
    return (
      <p className="rounded-xl border border-dashed bg-white px-4 py-12 text-center text-sm text-muted-foreground">
        {waiting ? "Signals appear here as pages are read." : "No signals were found."}
      </p>
    );
  }
  const ordered = [...signals].sort(
    (a, b) =>
      Number(b.selected) - Number(a.selected) ||
      Number(isExcluded(a)) - Number(isExcluded(b)) ||
      (b.scores.score ?? -1) - (a.scores.score ?? -1),
  );
  const hook = ordered.find((s) => s.selected);
  const others = ordered.filter((s) => s !== hook);
  return (
    <div className="space-y-6">
      {hook && (
        <section className="space-y-2">
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Hook used in the email</h3>
          <HookCard signal={hook} angle={angle} />
        </section>
      )}
      {others.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {hook ? "Other signals" : "Signals"} · {others.length}
          </h3>
          <ul className="divide-y overflow-hidden rounded-xl border bg-white">
            {others.map((s) => (
              <SignalRow key={s.id} signal={s} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function isExcluded(signal: SignalRecord) {
  return signal.type === "ai_screening" || signal.sensitive;
}

function host(signal: SignalRecord) {
  return signal.source_url.startsWith("http") ? new URL(signal.source_url).hostname.replace(/^www\./, "") : "uploaded bio";
}

function Tags({ signal }: { signal: SignalRecord }) {
  const crowd = signal.scores.crowded ? signal.scores.adjustments?.find((a) => a.name === "crowded") : undefined;
  const tags: { text: string; tone: "amber" | "red" | "zinc" }[] = [];
  if (crowd) tags.push({ text: `Crowded story · ${signal.coverage} outlets`, tone: "amber" });
  else if (signal.coverage > 1) tags.push({ text: `${signal.coverage} outlets`, tone: "zinc" });
  if (signal.ghost_suspect) tags.push({ text: "Possible ghost job", tone: "amber" });
  if (signal.sensitive) tags.push({ text: "Sensitive", tone: "red" });
  if (signal.type === "ai_screening") tags.push({ text: "AI screening", tone: "amber" });
  if (!tags.length) return null;
  const tones = { zinc: "bg-zinc-100 text-zinc-700", amber: "bg-amber-100 text-amber-900", red: "bg-red-100 text-red-800" };
  return (
    <>
      {tags.map((t) => (
        <span key={t.text} className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", tones[t.tone])}>
          {t.text}
        </span>
      ))}
    </>
  );
}

function Meta({ signal }: { signal: SignalRecord }) {
  return (
    <span className="text-xs text-muted-foreground">
      {signal.type.replace(/_/g, " ")} · {signal.about === "person" ? "about them" : "about the company"} · {signal.published_at ?? "undated"}
    </span>
  );
}

function HookCard({ signal, angle }: { signal: SignalRecord; angle: string | null }) {
  return (
    <article className="rounded-xl border border-primary/40 bg-white p-5 shadow-xs ring-4 ring-primary/5">
      <div className="flex flex-wrap items-center gap-2">
        <Meta signal={signal} />
        <Tags signal={signal} />
        {signal.scores.score != null && (
          <span className="ml-auto rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary tabular-nums">
            {signal.scores.score.toFixed(1)} / 5
          </span>
        )}
      </div>
      <p className="mt-2 text-base font-medium text-zinc-900">{signal.summary}</p>
      {angle && (
        <p className="mt-3 rounded-lg bg-zinc-50 px-3 py-2 text-sm text-zinc-700">
          <span className="font-medium text-zinc-900">Angle · </span>
          {angle}
        </p>
      )}
      <Evidence signal={signal} />
      <ScoreBreakdown signal={signal} />
    </article>
  );
}

function SignalRow({ signal }: { signal: SignalRecord }) {
  const score = signal.scores.score;
  return (
    <li>
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 hover:bg-zinc-50">
          <span className={cn("w-9 shrink-0 text-sm font-medium tabular-nums", isExcluded(signal) ? "text-zinc-300" : "text-zinc-500")}>
            {score != null && !isExcluded(signal) ? score.toFixed(1) : "–"}
          </span>
          <span className="min-w-0 flex-1">
            <span className={cn("block truncate text-sm", isExcluded(signal) ? "text-zinc-400" : "text-zinc-800")}>{signal.summary}</span>
            <span className="mt-0.5 flex flex-wrap items-center gap-1.5">
              <Meta signal={signal} />
              <Tags signal={signal} />
            </span>
          </span>
          <span className="text-xs text-muted-foreground group-open:hidden">Details</span>
        </summary>
        <div className="space-y-2 border-t bg-zinc-50/60 px-4 py-3 pl-16">
          {signal.rejected_reason && (
            <p className="text-sm text-zinc-700">
              <span className="font-medium">Why not used · </span>
              {signal.rejected_reason}
            </p>
          )}
          <Evidence signal={signal} />
          <ScoreBreakdown signal={signal} />
        </div>
      </details>
    </li>
  );
}

function Evidence({ signal }: { signal: SignalRecord }) {
  return (
    <div className="mt-3 space-y-1.5">
      <blockquote className="border-l-2 border-zinc-200 pl-3 text-sm text-zinc-600">“{signal.evidence}”</blockquote>
      {signal.note && <p className="text-xs text-amber-800">{signal.note}</p>}
      {signal.source_url.startsWith("http") ? (
        <a href={signal.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
          {host(signal)} <ExternalLink className="size-3" />
        </a>
      ) : (
        <span className="text-xs text-muted-foreground">{host(signal)}</span>
      )}
    </div>
  );
}

function ScoreBreakdown({ signal }: { signal: SignalRecord }) {
  const s = signal.scores;
  if (s.score == null) return null;
  const parts = [
    ["Relevance", s.relevance?.toString()],
    ["Specificity", s.specificity?.toString()],
    ["Recency", s.recency != null ? `${s.recency.toFixed(1)} (${s.days_old} days)` : undefined],
    ["Verifiability", s.verifiability?.toString()],
  ].filter((p): p is [string, string] => p[1] != null);
  const adjustments = s.adjustments ?? [];
  return (
    <div className="mt-3 border-t pt-3" title={s.reasoning}>
      <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
        {parts.map(([k, v]) => (
          <div key={k} className="flex gap-1.5">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="font-medium text-zinc-800 tabular-nums">{v}</dd>
          </div>
        ))}
        {adjustments.map((a) => (
          <div key={a.name} className="flex gap-1.5">
            <dt className="text-muted-foreground">{a.name.replace(/_/g, " ")}</dt>
            <dd className="font-medium text-amber-800 tabular-nums">
              {a.delta > 0 ? "+" : "−"}
              {Math.abs(a.delta)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
