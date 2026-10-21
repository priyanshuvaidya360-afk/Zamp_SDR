import { Check, X } from "lucide-react";

import type { DraftRecord } from "@/lib/types";
import { cn } from "@/lib/utils";

// The "content guide": is this email good to send? A navy summary, then one row per check
// (length, subject, sources, personal) and the critic's checklist in plain words.
const MAX_WORDS = 120; // seller_profile.yaml tone.max_words
const SUBJECT = { min: 20, max: 60 };

export function ContentGuide({ draft }: { draft: DraftRecord }) {
  const report = draft.critic_report;
  const body = draft.edited_body ?? draft.body;
  const words = body.trim().split(/\s+/).filter(Boolean).length;
  const subjectLen = draft.subject.length;
  const sourced = new Set(draft.claims.map((c) => c.signal_id)).size;
  const warnings = (report.warnings ?? []).filter((w) => w !== report.banner);

  const rows: Row[] = [
    { title: "Length", value: `${words} words`, note: `Under ${MAX_WORDS} words`, ok: words <= MAX_WORDS, fill: words / MAX_WORDS },
    {
      title: "Subject",
      value: `${subjectLen} characters`,
      note: `${SUBJECT.min}–${SUBJECT.max} suggested`,
      ok: subjectLen >= SUBJECT.min && subjectLen <= SUBJECT.max,
      fill: subjectLen / SUBJECT.max,
    },
    {
      title: "Sourced facts",
      value: `${sourced} source${sourced === 1 ? "" : "s"}`,
      note: "Every fact links to a page",
      ok: sourced > 0 || report.mode !== "hooked",
      fill: sourced ? 1 : 0,
    },
  ];
  if (report.genericness) {
    rows.push({
      title: "Personal",
      value: `${5 - report.genericness.genericness} / 5`,
      note: report.genericness.reason,
      ok: report.genericness.genericness < 3,
      fill: (5 - report.genericness.genericness) / 5,
    });
  }
  const passed = rows.filter((r) => r.ok).length;
  const all = passed === rows.length;

  return (
    <div className="overflow-hidden rounded-[14px] border bg-white">
      <div className="flex items-center justify-between gap-3 bg-navy px-5 py-4.5 text-white">
        <div className="flex flex-col gap-1">
          <span className="text-xs font-bold tracking-[0.08em] text-[#a9c1ee]">CONTENT GUIDE</span>
          <span className="text-base font-semibold">{all ? `All ${rows.length} checks passed` : `${passed} of ${rows.length} checks passed`}</span>
        </div>
        <span className={cn("rounded-full px-3 py-1 text-[13px] font-bold", all ? "bg-[#1a9a56]" : "bg-[#e28a1d]")}>{all ? "Good" : "Review"}</span>
      </div>

      {rows.map((r) => (
        <div key={r.title} className="space-y-2.5 border-b px-5 py-4.5">
          <div className="flex items-center gap-2.5">
            <span
              className={cn(
                "flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold",
                r.ok ? "bg-[#e7f6ee] text-[#137a43]" : "bg-[#fff4e8] text-[#a14a00]",
              )}
            >
              {r.ok ? "✓" : "!"}
            </span>
            <span className="flex-1 text-[15px] font-semibold">{r.title}</span>
            <span className="text-sm font-bold text-navy">{r.value}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-secondary">
            <div className={cn("h-full rounded-full", r.ok ? "bg-primary" : "bg-[#e28a1d]")} style={{ width: `${Math.max(4, Math.min(100, r.fill * 100))}%` }} />
          </div>
          <div className="flex items-start justify-between gap-3">
            <span className="text-[13px] leading-normal text-muted-foreground">{r.note}</span>
            <span className={cn("shrink-0 rounded-md px-2 py-0.5 text-xs font-semibold", r.ok ? "bg-[#e7f6ee] text-[#137a43]" : "bg-[#fff4e8] text-[#a14a00]")}>
              {r.ok ? "Good" : "Check"}
            </span>
          </div>
        </div>
      ))}

      {report.checks && (
        <div className="space-y-2 px-5 py-4.5">
          <h3 className="text-xs font-bold tracking-[0.08em] text-primary uppercase">Critic checklist</h3>
          <ul className="space-y-1.5 text-sm">
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
        </div>
      )}

      {(warnings.length > 0 || report.confidence != null) && (
        <div className="space-y-2 border-t bg-zinc-50 px-5 py-3.5 text-xs">
          {warnings.map((w) => (
            <p key={w} className="text-[#a14a00]">{w}</p>
          ))}
          {report.confidence != null && (
            <p className="text-muted-foreground">
              Confidence <span className="font-semibold text-foreground">{report.confidence}</span>
              {report.confidence_formula && <span className="block">= {report.confidence_formula}</span>}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

type Row = { title: string; value: string; note: string; ok: boolean; fill: number };
