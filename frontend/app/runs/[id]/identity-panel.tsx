"use client";

import { Check, Minus, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { Candidate, IdentityCheck, IdentityOutcome, ResumeInput, Run } from "@/lib/types";
import { cn } from "@/lib/utils";

// needs_input: the run stopped because identity failed. The backend says why (resolve.diagnose),
// so the card tells a broken search from bad data from a real person who just isn't online, shows
// the checks behind that verdict, and offers only the actions that make sense for it. Whatever
// the rep picks, the run resumes from resolve.

const LOOK: Record<"amber" | "red" | "blue", { box: string; title: string }> = {
  amber: { box: "border-amber-300 bg-amber-50/60", title: "text-amber-900" },
  red: { box: "border-red-200 bg-red-50/60", title: "text-red-800" },
  blue: { box: "border-[#c9d8f5] bg-[#eaf1fd]/60", title: "text-navy" },
};

export function IdentityPanel({
  reason,
  outcome,
  checks,
  candidates,
  prospect,
  onResume,
}: {
  reason: string;
  outcome: IdentityOutcome | null;
  checks: IdentityCheck[];
  candidates: Candidate[];
  prospect: Run;
  onResume: (input: ResumeInput) => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState(false);
  const pickPerson = outcome === "ambiguous" || outcome === "unconfirmed" || outcome === null;
  const company = prospect.company ?? prospect.domain;

  async function act(key: string, input: ResumeInput) {
    setBusy(key);
    setError(null);
    try {
      await onResume(input);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not resume this run.");
      setBusy(null);
    }
  }

  const heading =
    outcome === "search_failed"
      ? { title: "Search wasn't available", tone: "blue" as const }
      : outcome === "bad_data"
        ? { title: "This row looks like bad data", tone: "red" as const }
        : outcome === "not_public"
          ? { title: company ? "Real company, but this person isn't online" : "Not found online", tone: "amber" as const }
          : { title: "Which person is this?", tone: "amber" as const };
  const look = LOOK[heading.tone];

  return (
    <div className={cn("space-y-3 rounded-xl border p-4", look.box)}>
      <div>
        <p className={cn("font-medium", look.title)}>{heading.title}</p>
        <p className="text-sm text-foreground/80">{reason} Nothing was guessed.</p>
      </div>

      {checks.length > 0 && <CheckList checks={checks} />}

      {pickPerson && candidates.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2">
          {candidates.map((c, i) => (
            <div key={`${c.name}-${i}`} className="flex flex-col gap-2 rounded-lg border bg-white p-3 text-sm">
              <div>
                <p className="font-medium">{c.name}</p>
                <p className="text-muted-foreground">{[c.title, c.company, c.location].filter(Boolean).join(" · ") || "No details"}</p>
              </div>
              <p className="text-xs text-muted-foreground">“{c.evidence}”</p>
              <div className="mt-auto flex items-center justify-between gap-2">
                <a href={c.evidence_url} target="_blank" rel="noreferrer" className="truncate text-xs text-primary hover:underline">
                  {c.evidence_url.replace(/^https?:\/\/(www\.)?/, "")}
                </a>
                <span className="text-xs text-muted-foreground tabular-nums">{c.confidence.toFixed(2)}</span>
              </div>
              <Button size="sm" disabled={busy !== null} onClick={() => act(`pick-${i}`, { candidate: c })}>
                {busy === `pick-${i}` ? "Resuming…" : "This is the one"}
              </Button>
            </div>
          ))}
        </div>
      )}

      {manual ? (
        <ManualIdentity
          prospect={prospect}
          disabled={busy !== null}
          fixing={outcome === "bad_data"}
          onSubmit={onResume}
        />
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {outcome === "search_failed" && (
            <Button size="sm" disabled={busy !== null} onClick={() => act("search", {})}>
              {busy === "search" ? "Searching…" : "Search again"}
            </Button>
          )}
          {outcome === "bad_data" && (
            <Button size="sm" disabled={busy !== null} onClick={() => setManual(true)}>
              Fix the row
            </Button>
          )}
          {outcome === "not_public" && (
            <>
              <Button size="sm" disabled={busy !== null} onClick={() => setManual(true)}>
                I know them: enter details
              </Button>
              {company && (
                <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => act("company", { unverified: true })}>
                  {busy === "company" ? "Resuming…" : "Write a company-level draft"}
                </Button>
              )}
            </>
          )}
          {pickPerson && (
            <Button size="sm" variant={candidates.length ? "outline" : "default"} disabled={busy !== null} onClick={() => setManual(true)}>
              {candidates.length ? "None of these: enter their details" : "Enter their details"}
            </Button>
          )}
          <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => act("dismiss", { dismiss: true })}>
            {busy === "dismiss" ? "Dismissing…" : "Dismiss"}
          </Button>
        </div>
      )}

      {outcome === "not_public" && company && !manual && (
        <p className="text-xs text-muted-foreground">
          A company-level draft uses research on {company} only and says nothing personal about {prospect.first_name}.
          You&rsquo;ll confirm they exist before approving it.
        </p>
      )}
      {error && <p className="text-sm text-red-700">{error}</p>}
    </div>
  );
}

function CheckList({ checks }: { checks: IdentityCheck[] }) {
  return (
    <ul className="grid gap-1 rounded-lg border bg-white px-3 py-2.5 text-sm sm:grid-cols-2" aria-label="What was checked">
      {checks.map((c) => (
        <li key={c.label} className="flex items-start gap-2">
          {c.ok === true ? (
            <Check className="mt-0.5 size-4 shrink-0 text-[#1a9a56]" aria-label="passed" />
          ) : c.ok === false ? (
            <X className="mt-0.5 size-4 shrink-0 text-red-600" aria-label="failed" />
          ) : (
            <Minus className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-label="not checked" />
          )}
          <span>{c.label}</span>
        </li>
      ))}
    </ul>
  );
}

const inputClass =
  "h-8 w-full rounded-lg border bg-white px-2.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50";

// The rep vouches for the identity: the run continues with these details (confidence 1.0),
// trusted over the CSV. Research still has to find signals; with none, the draft is role-based.
// Fixing a bad-data row is different: the corrected details are searched again, not trusted.
function ManualIdentity({
  prospect,
  disabled,
  fixing,
  onSubmit,
}: {
  prospect: Run;
  disabled: boolean;
  /** bad data: the rep corrects the row, and identity is searched again. */
  fixing: boolean;
  onSubmit: (input: ResumeInput) => Promise<void>;
}) {
  const [title, setTitle] = useState(prospect.title ?? "");
  const [company, setCompany] = useState(prospect.company ?? "");
  const [domain, setDomain] = useState(prospect.domain ?? "");
  const [linkedin, setLinkedin] = useState(prospect.linkedin_url ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const name = prospect.full_name || `${prospect.first_name} ${prospect.last_name}`;
  const ready = company.trim() !== "" || domain.trim() !== "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    const site = domain.trim();
    const details = { title: title.trim() || null, company: company.trim() || null, company_domain: site || null };
    try {
      await onSubmit(
        fixing
          ? { fix: details }
          : {
              candidate: {
                name,
                ...details,
                location: null,
                country: null,
                evidence_url: linkedin.trim() || (site ? `https://${site.replace(/^https?:\/\//, "")}` : ""),
                evidence: "Confirmed by the rep",
                confidence: 1,
              },
            },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not resume this run.");
      setBusy(false);
    }
  }

  const field = (id: string, label: string, value: string, set: (v: string) => void, placeholder: string) => (
    <label htmlFor={id} className="space-y-1 text-xs font-medium text-muted-foreground">
      <span>{label}</span>
      <input id={id} value={value} onChange={(e) => set(e.target.value)} placeholder={placeholder} className={inputClass} />
    </label>
  );

  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border bg-white p-3">
      <p className="text-sm font-medium">{fixing ? `${name}: correct their details` : `${name}: confirm who they are`}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {field("id-title", "Job title", title, setTitle, "e.g. Founder")}
        {field("id-company", "Company", company, setCompany, "e.g. CreditsIn")}
        {field("id-domain", "Company website", domain, setDomain, "e.g. creditsin.com")}
        {!fixing && field("id-linkedin", "LinkedIn URL (optional)", linkedin, setLinkedin, "https://linkedin.com/in/…")}
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" size="sm" disabled={disabled || busy || !ready}>
          {busy ? "Resuming…" : fixing ? "Search again with these details" : "Confirm and continue research"}
        </Button>
        <p className="text-xs text-muted-foreground">
          {!ready
            ? "Give a company or its website."
            : fixing
              ? "Identity is checked again with the corrected details."
              : "You vouch for this match; nothing is searched for identity again."}
        </p>
      </div>
    </form>
  );
}
