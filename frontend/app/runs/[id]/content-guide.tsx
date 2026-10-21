import { Check, X } from "lucide-react";

import type { DraftRecord } from "@/lib/types";
import { cn } from "@/lib/utils";

// The "content guide": is this email good to send? Length, claims with sources, and the critic's
// checklist in plain words. Opened with a toggle; closed by default so the email gets the room.
const MAX_WORDS = 120; // seller_profile.yaml tone.max_words
const SUBJECT = { min: 20, max: 60 };

export function ContentGuide({ draft }: { draft: DraftRecord }) {
  const report = draft.critic_report;
  const body = draft.edited_body ?? draft.body;
  const words = body.trim().split(/\s+/).filter(Boolean).length;
  const subjectLen = draft.subject.length;
  const sourced = new Set(draft.claims.map((c) => c.signal_id)).size;
  const warnings = (report.warnings ?? []).filter((w) => w !== report.banner);

  return (
    <div className="space-y-6 text-sm">
      <section className="space-y-4">
        <Meter label="Length" value={`${words} words`} hint={`Under ${MAX_WORDS} words`} ok={words <= MAX_WORDS} fill={words / MAX_WORDS} />
        <Meter
          label="Subject"
          value={`${subjectLen} characters`}
          hint={`${SUBJECT.min}–${SUBJECT.max} suggested`}
          ok={subjectLen >= SUBJECT.min && subjectLen <= SUBJECT.max}
          fill={subjectLen / SUBJECT.max}
        />
        <Meter
          label="Sourced facts"
          value={`${sourced} source${sourced === 1 ? "" : "s"}`}
          hint="Every fact links to a page"
          ok={sourced > 0 || report.mode !== "hooked"}
          fill={sourced ? 1 : 0}
        />
        {report.genericness && (
          <Meter
            label="Personal"
            value={`${5 - report.genericness.genericness} / 5`}
            hint={report.genericness.reason}
            ok={report.genericness.genericness < 3}
            fill={(5 - report.genericness.genericness) / 5}
          />
        )}
      </section>

      {report.checks && (
        <section>
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Critic checklist</h3>
          <ul className="mt-2 space-y-1.5">
            {report.checks.map((c) => (
              <li key={c.name} className="flex items-start gap-2">
                {c.passed ? <Check className="mt-0.5 size-3.5 shrink-0 text-emerald-600" /> : <X className="mt-0.5 size-3.5 shrink-0 text-red-600" />}
                <span className="text-zinc-700">
                  {c.name.replace(/_/g, " ")}
                  {!c.passed && c.detail && <span className="block text-xs text-muted-foreground">{c.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {warnings.length > 0 && (
        <ul className="space-y-1 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      {report.confidence != null && (
        <p className="text-xs text-muted-foreground">
          Confidence <span className="font-medium text-foreground">{report.confidence}</span>
          {report.confidence_formula && <span className="block">= {report.confidence_formula}</span>}
        </p>
      )}
    </div>
  );
}

function Meter({ label, value, hint, ok, fill }: { label: string; value: string; hint: string; ok: boolean; fill: number }) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium text-zinc-900">{label}</span>
        <span className="text-xs text-zinc-700 tabular-nums">{value}</span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-zinc-100">
        <div className={cn("h-full rounded-full", ok ? "bg-emerald-500" : "bg-amber-500")} style={{ width: `${Math.max(4, Math.min(100, fill * 100))}%` }} />
      </div>
      <p className="mt-1 flex justify-between gap-2 text-xs text-muted-foreground">
        <span>{hint}</span>
        <span className={ok ? "text-emerald-700" : "text-amber-700"}>{ok ? "Good" : "Check"}</span>
      </p>
    </div>
  );
}
