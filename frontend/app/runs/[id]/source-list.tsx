import { ExternalLink } from "lucide-react";

import type { Run, SignalRecord, StageEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

// Sources tab: every website the run looked at, so the rep can check the research themselves.
// Profile links first (their LinkedIn, the company site), then the pages that were read and what
// each one gave, then everything else the searches found, folded away with the reason it wasn't
// used. LinkedIn is listed as a link for the rep to open; the app itself never fetches it.

type Found = { url: string; title: string };
type Query = { category: string; about: string; query: string; found?: Found[]; relevant_results?: Found[] };
type ReadPage = { url: string; title: string; category: string; about: string; text: string | null };
type Dropped = { source_url: string; reason: string };

interface Other {
  url: string;
  title: string;
  via: string;
  why: string;
}

export function SourceList({
  run,
  latest,
  signals,
  waiting,
}: {
  run: Run;
  latest: Map<string, StageEvent>;
  signals: SignalRecord[];
  waiting: boolean;
}) {
  const resolve = latest.get("resolve")?.payload;
  const gather = latest.get("gather")?.payload.output;
  const extract = latest.get("extract")?.payload.output;
  const pages = (gather?.sources ?? []) as ReadPage[];
  const queries = (gather?.queries ?? []) as Query[];
  const dropped = (extract?.dropped ?? []) as Dropped[];
  const identity = ((resolve?.output as { found?: Found[] } | undefined)?.found ?? resolve?.found ?? []) as Found[];

  const profiles = [
    run.linkedin_url && { url: absolute(run.linkedin_url), label: "LinkedIn profile", note: "from your CSV" },
    run.domain && { url: `https://${run.domain}`, label: run.company ?? run.domain, note: "company website" },
  ].filter(Boolean) as { url: string; label: string; note: string }[];

  // A page appears once, in the most useful group: read > found.
  const read = new Set(pages.map((p) => p.url));
  const kept = new Set(queries.flatMap((q) => (q.relevant_results ?? []).map((r) => r.url)));
  const others = new Map<string, Other>();
  for (const f of identity) {
    if (!read.has(f.url)) others.set(f.url, { ...f, via: "identity search", why: isLinkedIn(f.url) ? LINKEDIN : "used to confirm who they are" });
  }
  for (const q of queries) {
    for (const f of q.found ?? q.relevant_results ?? []) {
      if (read.has(f.url) || others.has(f.url)) continue;
      const why = isLinkedIn(f.url) ? LINKEDIN : kept.has(f.url) ? "relevant, but over the reading limit" : `not about this ${q.about === "person" ? "person" : "company"}`;
      others.set(f.url, { ...f, via: `${q.category.replace(/_/g, " ")} search`, why });
    }
  }
  const otherList = [...others.values()];

  if (!profiles.length && !pages.length && !otherList.length) {
    return <Empty>{waiting ? "Sources appear here as the research runs." : "No sources for this run."}</Empty>;
  }

  return (
    <div className="space-y-6">
      {profiles.length > 0 && (
        <Group title="Profiles">
          {profiles.map((p) => (
            <Row key={p.url} url={p.url} title={p.label} meta={p.note} />
          ))}
        </Group>
      )}

      <Group title={`Pages read · ${pages.length}`}>
        {pages.length ? (
          pages.map((p, i) => {
            const facts = signals.filter((s) => s.source_url === p.url).length;
            const lost = dropped.filter((d) => d.source_url === p.url);
            const result = !p.text
              ? "couldn't be read"
              : facts
                ? `${facts} signal${facts === 1 ? "" : "s"}`
                : lost.length
                  ? `facts dropped: ${lost[0].reason}`
                  : "nothing specific found";
            return (
              <Row
                key={`${i}-${p.url}`}
                url={p.url}
                title={p.title || hostname(p.url)}
                meta={`${p.about === "person" ? "about them" : "about the company"} · ${p.category.replace(/_/g, " ")}`}
                badge={{ text: result, good: facts > 0 }}
              />
            );
          })
        ) : (
          <li className="px-4 py-3.5 text-sm text-muted-foreground">
            {waiting ? "Pages appear here once they are read." : "No page passed the relevance check, so none was read."}
          </li>
        )}
      </Group>

      {otherList.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer list-none text-xs font-bold tracking-[0.08em] text-primary uppercase">
            Found but not used · {otherList.length}
            <span className="ml-2 font-semibold tracking-normal normal-case group-open:hidden">Show</span>
          </summary>
          <ul className="mt-2 divide-y overflow-hidden rounded-[14px] border bg-white">
            {otherList.map((o) => (
              <Row key={o.url} url={o.url} title={o.title || hostname(o.url)} meta={`${o.via} · ${o.why}`} muted />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

const LINKEDIN = "LinkedIn: open it yourself (the app never reads LinkedIn)";

function isLinkedIn(url: string) {
  return /(^|\.)linkedin\.com$/.test(hostname(url));
}

function absolute(url: string) {
  return /^https?:\/\//.test(url) ? url : `https://${url}`;
}

function hostname(url: string) {
  try {
    return new URL(absolute(url)).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-bold tracking-[0.08em] text-primary uppercase">{title}</h3>
      <ul className="divide-y overflow-hidden rounded-[14px] border bg-white">{children}</ul>
    </section>
  );
}

function Row({
  url,
  title,
  meta,
  badge,
  muted = false,
}: {
  url: string;
  title: string;
  meta: string;
  badge?: { text: string; good: boolean };
  muted?: boolean;
}) {
  return (
    <li>
      <a href={url} target="_blank" rel="noreferrer" className="flex items-start gap-3 px-4 py-3 hover:bg-zinc-50">
        <span className="min-w-0 flex-1">
          <span className={cn("block truncate text-sm font-medium", muted ? "text-zinc-600" : "text-zinc-800")}>{title}</span>
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">
            {hostname(url)} · {meta}
          </span>
          {badge && (
            <span
              className={cn(
                "mt-1.5 inline-block rounded-full px-2 py-0.5 text-[11px] font-medium",
                badge.good ? "bg-[#e7f6ee] text-[#137a43]" : "bg-zinc-100 text-zinc-700",
              )}
            >
              {badge.text}
            </span>
          )}
        </span>
        <ExternalLink className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      </a>
    </li>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded-xl border border-dashed bg-white px-4 py-12 text-center text-sm text-muted-foreground">{children}</p>;
}
