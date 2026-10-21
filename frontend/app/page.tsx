import { FlowSteps } from "@/components/flow-steps";

import { RecentLists } from "./recent-lists";
import { UploadHero } from "./upload-hero";

// Step 1 of the flow: upload a prospect CSV. Spec: docs/GUIDE.md "UI: three screens" > 1.
export default function Home() {
  return (
    <div className="space-y-8">
      <FlowSteps current={0} />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <UploadHero />
        <aside className="space-y-4 rounded-[14px] border bg-white p-6">
          <h2 className="text-xs font-bold tracking-[0.08em] text-primary uppercase">Why Signal</h2>
          <p className="text-[15px] leading-relaxed text-zinc-800">
            A rep with 200 prospects cannot research every person, find the news or pain point that matters, and write a
            message that sounds like it.
          </p>
          <ul className="space-y-3 text-sm">
            {[
              ["Researches", "reads the public web for each person and their company"],
              ["Picks one hook", "the most relevant, recent, verifiable signal, scored in the open"],
              ["Drafts and cites", "under 120 words, every fact linked to its source"],
              ["You decide", "approve, edit or reject. Nothing is ever sent."],
            ].map(([title, text], i) => (
              <li key={title} className="flex gap-3">
                <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-secondary text-xs font-bold text-primary">
                  {i + 1}
                </span>
                <span>
                  <strong className="text-navy">{title}</strong>
                  <span className="text-muted-foreground"> · {text}</span>
                </span>
              </li>
            ))}
          </ul>
        </aside>
      </div>
      <RecentLists />
    </div>
  );
}
