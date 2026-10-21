import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";

import { Dashboard } from "./dashboard";

// Spec: docs/GUIDE.md "UI: three screens" > 3 Dashboard, and T16.
export default function Home() {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
          <p className="max-w-2xl text-muted-foreground">
            Every prospect researched, every hook cited, every draft waiting for a person. Nothing is ever sent.
          </p>
        </div>
        <Link href="/batches/new" className={buttonVariants()}>
          New batch
        </Link>
      </div>
      <Dashboard />
    </div>
  );
}
