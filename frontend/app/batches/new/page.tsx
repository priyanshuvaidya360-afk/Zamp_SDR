import type { Metadata } from "next";

import { NewBatch } from "./new-batch";
import { SellerPanel } from "./seller-panel";

export const metadata: Metadata = { title: "New batch · Signal" };

export default function NewBatchPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New batch</h1>
        <p className="text-muted-foreground">
          Upload prospects. We check every row first; nothing is researched until you press Run.
        </p>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <NewBatch />
        <aside>
          <SellerPanel />
        </aside>
      </div>
    </div>
  );
}
