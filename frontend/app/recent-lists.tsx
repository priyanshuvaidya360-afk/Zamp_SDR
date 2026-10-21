"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { BatchListItem } from "@/lib/types";

// Earlier uploads, so a rep can pick up where they left off.
export function RecentLists() {
  const [items, setItems] = useState<BatchListItem[] | null>(null);

  useEffect(() => {
    api.listBatches(5, 0).then((page) => setItems(page.items), () => setItems([]));
  }, []);

  if (!items?.length) return null;
  return (
    <section className="space-y-3">
      <h2 className="text-xs font-bold tracking-[0.08em] text-primary uppercase">Your recent lists</h2>
      <ul className="divide-y overflow-hidden rounded-[14px] border bg-white">
        {items.map((b) => (
          <li key={b.id}>
            <Link href={`/batches/${b.id}`} className="flex items-center justify-between gap-4 px-5 py-3.5 hover:bg-zinc-50">
              <span className="min-w-0">
                <span className="block truncate font-semibold">{b.filename}</span>
                <span className="text-xs text-muted-foreground">
                  {new Date(b.uploaded_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })} · {b.valid} prospects
                </span>
              </span>
              <span className="shrink-0 text-sm text-muted-foreground">
                <span className="font-semibold text-navy tabular-nums">{b.runs}</span> of {b.valid} drafted
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
