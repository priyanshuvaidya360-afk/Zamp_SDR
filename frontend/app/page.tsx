import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";

// The dashboard (metrics, batches, runs) is built in T16; for now, the way in.
export default function Home() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Signal</h1>
        <p className="max-w-2xl text-muted-foreground">
          Upload your prospect list. Signal researches each person, picks one real, cited reason to reach out,
          and drafts a short email for you to review. Nothing is ever sent.
        </p>
      </div>
      <Link href="/batches/new" className={buttonVariants()}>
        New batch
      </Link>
    </div>
  );
}
