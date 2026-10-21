import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

// The four steps of the product story, shown at the top of every screen in the flow so the
// rep always knows where they are: upload -> map columns -> pick a prospect -> read the draft.
export const FLOW = [
  { title: "Upload", hint: "your prospect CSV" },
  { title: "Map columns", hint: "check the fields" },
  { title: "Prospects", hint: "pick who to draft" },
  { title: "Draft & review", hint: "cited email, your call" },
] as const;

export function FlowSteps({ current, className, compact = false }: { current: 0 | 1 | 2 | 3; className?: string; compact?: boolean }) {
  if (compact) {
    return (
      <ol className={cn("flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]", className)} aria-label="Progress">
        {FLOW.map((step, i) => (
          <li key={step.title} aria-current={i === current ? "step" : undefined} className="flex items-center gap-2">
            {i > 0 && <span className="text-zinc-300">›</span>}
            <span className={cn(i === current ? "rounded-full bg-navy px-3 py-0.5 font-semibold text-white" : i < current ? "font-medium text-primary" : "text-muted-foreground")}>
              {i < current ? "✓\u00a0" : ""}
              {step.title}
            </span>
          </li>
        ))}
      </ol>
    );
  }
  return (
    <ol className={cn("grid grid-cols-2 gap-2 sm:grid-cols-4", className)} aria-label="Progress">
      {FLOW.map((step, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li
            key={step.title}
            aria-current={active ? "step" : undefined}
            className={cn(
              "flex items-center gap-3 rounded-xl border px-4 py-3 transition-colors",
              active ? "border-primary bg-white ring-4 ring-primary/10" : "bg-white/60",
            )}
          >
            <span
              className={cn(
                "flex size-7 shrink-0 items-center justify-center rounded-full text-[13px] font-bold",
                done ? "bg-primary text-white" : active ? "bg-navy text-white" : "bg-secondary text-muted-foreground",
              )}
            >
              {done ? <Check className="size-3.5" strokeWidth={3} /> : i + 1}
            </span>
            <span className="min-w-0">
              <span className={cn("block text-sm font-semibold", !active && !done && "text-muted-foreground")}>{step.title}</span>
              <span className="block truncate text-xs text-muted-foreground">{step.hint}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
