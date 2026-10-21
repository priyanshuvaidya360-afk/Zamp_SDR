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
          <h3 className="text-xs font-bold tracking-[0.08em] text-primary uppercase">Hook used in the email</h3>
          <HookCard signal={hook} angle={angle} />
        </section>
      )}
      {others.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-xs font-bold tracking-[0.08em] text-primary uppercase">
            {hook ? "Other signals" : "Signals"} · {others.length}
          </h3>
          <ul className="divide-y overflow-hidden rounded-[14px] border bg-white">
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
  const meta = `${signal.type.replace(/_/g, " ")} · ${signal.about === "person" ? "about them" : "about the company"} · ${signal.published_at ?? "undated"}`;
  return (
    <article className="overflow-hidden rounded-[14px] border bg-white">
      <div className="flex flex-col gap-2.5 bg-navy p-5 text-white">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-[#a9c1ee]">{meta}</span>
          {signal.scores.score != null && (
            <span className="rounded-full bg-[#3b82f6] px-2.5 py-0.5 text-[13px] font-bold whitespace-nowrap tabular-nums">
              {signal.scores.score.toFixed(1)} / 5
            </span>
          )}
        </div>
        <p className="text-lg leading-snug font-semibold tracking-tight text-pretty">{signal.summary}</p>
        <div className="flex flex-wrap gap-1.5 empty:hidden">
          <Tags signal={signal} />
        </div>
      </div>
      <div className="space-y-4 p-5">
        {angle && (
          <p className="rounded-[10px] bg-background px-4 py-3.5 text-sm leading-relaxed text-zinc-800">
            <strong className="text-navy">Angle</strong> · {angle}
          </p>
        )}
        <Evidence signal={signal} />
        <ScoreBreakdown signal={signal} />
      </div>
    </article>
  );
}

function SignalRow({ signal }: { signal: SignalRecord }) {
  const score = signal.scores.score;
  return (
    <li>
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center gap-3.5 px-4 py-3.5 hover:bg-zinc-50">
          <span className={cn("flex size-11 shrink-0 items-center justify-center rounded-[10px] text-[15px] font-bold tabular-nums", isExcluded(signal) ? "bg-zinc-100 text-zinc-300" : "bg-secondary text-primary")}>
            {score != null && !isExcluded(signal) ? score.toFixed(1) : "–"}
          </span>
          <span className="min-w-0 flex-1">
            <span className={cn("block truncate text-sm", isExcluded(signal) ? "text-zinc-400" : "text-zinc-800")}>{signal.summary}</span>
            <span className="mt-0.5 flex flex-wrap items-center gap-1.5">
              <Meta signal={signal} />
              <Tags signal={signal} />
            </span>
          </span>
          <span className="text-[13px] font-semibold text-primary group-open:hidden">Details</span>
        </summary>
        <div className="space-y-3 border-t bg-zinc-50 px-4 py-4 pl-[76px]">
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
    <div className="space-y-2">
      <blockquote className="border-l-[3px] border-[#3b82f6] pl-3.5 font-serif text-[15px] leading-relaxed text-zinc-800 italic">“{signal.evidence}”</blockquote>
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
    ...(s.adjustments ?? []).map((a) => [a.name.replace(/_/g, " "), `${a.delta > 0 ? "+" : "\u2212"}${Math.abs(a.delta)}`]),
  ].filter((p): p is [string, string] => p[1] != null);
  return (
    <div className="grid grid-cols-2 gap-2" title={s.reasoning}>
      {parts.map(([k, v]) => (
        <div key={k} className="flex flex-col gap-0.5 rounded-lg border px-3 py-2.5">
          <span className="text-xs text-muted-foreground capitalize">{k}</span>
          <span className="text-base font-bold text-navy tabular-nums">{v}</span>
        </div>
      ))}
    </div>
  );
}
