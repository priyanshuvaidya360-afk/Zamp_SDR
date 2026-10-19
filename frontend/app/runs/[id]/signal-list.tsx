import type { SignalRecord } from "@/lib/types";
import { cn } from "@/lib/utils";

// Middle column: every signal, the chosen hook first with its angle, the rest with "why not".
export function SignalList({ signals, angle, waiting }: { signals: SignalRecord[]; angle: string | null; waiting: boolean }) {
  if (!signals.length) {
    return (
      <p className="rounded-xl border border-dashed bg-white px-4 py-10 text-center text-sm text-muted-foreground">
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
  return (
    <div className="space-y-3">
      {ordered.map((signal) => (
        <SignalCard key={signal.id} signal={signal} angle={signal.selected ? angle : null} />
      ))}
    </div>
  );
}

function isExcluded(signal: SignalRecord) {
  return signal.type === "ai_screening" || signal.sensitive;
}

function SignalCard({ signal, angle }: { signal: SignalRecord; angle: string | null }) {
  const score = signal.scores.score;
  const domain = signal.source_url.startsWith("http") ? new URL(signal.source_url).hostname.replace(/^www\./, "") : "uploaded bio";
  return (
    <article
      className={cn(
        "rounded-xl border bg-white p-4",
        signal.selected && "border-primary ring-2 ring-primary/20",
        isExcluded(signal) && "bg-zinc-50",
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        {signal.selected && <span className="rounded-md bg-primary px-1.5 py-0.5 font-medium text-primary-foreground">Hook</span>}
        <span className="rounded-md bg-zinc-100 px-1.5 py-0.5 font-medium text-zinc-700">{signal.type.replace(/_/g, " ")}</span>
        <span className="text-muted-foreground">{signal.about === "person" ? "about them" : "about the company"}</span>
        <span className="text-muted-foreground">· {signal.published_at ?? "undated"}</span>
        {signal.coverage > 1 && <Badge tone="zinc">reported by {signal.coverage} outlets</Badge>}
        {signal.ghost_suspect && <Badge tone="amber">possible ghost job</Badge>}
        {signal.sensitive && <Badge tone="red">sensitive</Badge>}
        {signal.type === "ai_screening" && <Badge tone="amber">AI screening</Badge>}
        {(signal.scores.adjustments ?? []).map((a) => (
          <Badge key={a.name} tone="amber">
            {a.name} {a.delta > 0 ? "+" : ""}
            {a.delta}
          </Badge>
        ))}
      </div>
      <p className="mt-2 text-sm font-medium">{signal.summary}</p>
      <blockquote className="mt-1 border-l-2 border-zinc-200 pl-2 text-xs text-muted-foreground">“{signal.evidence}”</blockquote>
      {signal.note && <p className="mt-2 text-xs text-amber-800">{signal.note}</p>}
      {angle && <p className="mt-2 text-sm"><span className="font-medium">Angle:</span> {angle}</p>}
      {score != null && <ScoreBar signal={signal} />}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs">
        {signal.source_url.startsWith("http") ? (
          <a href={signal.source_url} target="_blank" rel="noreferrer" className="text-primary hover:underline">
            {domain}
          </a>
        ) : (
          <span className="text-muted-foreground">{domain}</span>
        )}
        {!signal.selected && signal.rejected_reason && (
          <span className="text-muted-foreground">Why not: {signal.rejected_reason}</span>
        )}
      </div>
    </article>
  );
}

function ScoreBar({ signal }: { signal: SignalRecord }) {
  const s = signal.scores;
  const width = Math.max(0, Math.min(100, ((s.score ?? 0) / 5) * 100));
  return (
    <div className="mt-3" title={s.reasoning}>
      <div className="flex items-center gap-2">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-100">
          <div className={cn("h-full rounded-full", signal.selected ? "bg-primary" : "bg-zinc-400")} style={{ width: `${width}%` }} />
        </div>
        <span className="text-xs font-medium tabular-nums">{s.score?.toFixed(2)}</span>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        relevance {s.relevance} · specificity {s.specificity} · recency {s.recency?.toFixed(1)} ({s.days_old} days) ·
        verifiability {s.verifiability}
      </p>
    </div>
  );
}

function Badge({ tone, children }: { tone: "zinc" | "amber" | "red"; children: React.ReactNode }) {
  const tones = { zinc: "bg-zinc-100 text-zinc-700", amber: "bg-amber-100 text-amber-900", red: "bg-red-100 text-red-800" };
  return <span className={cn("rounded-md px-1.5 py-0.5", tones[tone])}>{children}</span>;
}
