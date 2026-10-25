"use client";

import { FileSpreadsheet } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { MappingDialog } from "@/components/mapping-dialog";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import type { BatchPreview } from "@/lib/types";
import { cn } from "@/lib/utils";

// Upload, then the column-mapping dialog, then on to the prospect list.
export function UploadHero() {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [batch, setBatch] = useState<BatchPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    try {
      setBatch(await api.uploadBatch(file));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  // The demo files live in public/ (copies of seed/demo.csv and seed/demo_messy.csv).
  async function loadDemo(name: "demo.csv" | "demo_messy.csv") {
    const response = await fetch(`/${name}`);
    await upload(new File([await response.blob()], name, { type: "text/csv" }));
  }

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-[28px] leading-tight font-bold tracking-tight">Turn a prospect list into researched drafts</h1>
        <p className="mt-1.5 max-w-2xl text-muted-foreground">
          Upload your CSV. Each row needs a first name, a last name and the person&rsquo;s LinkedIn URL. Up to 50 rows.
        </p>
      </div>

      <div
        role="button"
        tabIndex={0}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files[0];
          if (file) upload(file);
        }}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-3 rounded-[14px] border-2 border-dashed bg-white px-6 py-16 text-center transition-colors",
          dragging ? "border-primary bg-secondary" : "border-[#c4d3ea] hover:border-primary hover:bg-zinc-50",
        )}
      >
        <span className="flex size-14 items-center justify-center rounded-2xl bg-secondary text-primary">
          <FileSpreadsheet className="size-7" />
        </span>
        <p className="text-lg font-semibold">{busy ? "Checking your file…" : "Drop your prospect CSV here"}</p>
        <p className="text-sm text-muted-foreground">or click to choose a file</p>
        <input
          ref={input}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload(file);
            e.target.value = "";
          }}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" size="lg" onClick={() => loadDemo("demo.csv")} disabled={busy}>
          Use the demo CSV
        </Button>
        <Button
          variant="ghost"
          size="lg"
          onClick={() => loadDemo("demo_messy.csv")}
          disabled={busy}
          title="Odd column names, ALL-CAPS names, a LinkedIn-only row, missing data and a duplicate"
        >
          Try a messy CSV
        </Button>
        <a href="/template.csv" download className="text-sm font-semibold text-primary hover:underline">
          Download the CSV template
        </a>
      </div>
      {error && <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">{error}</p>}

      {batch && (
        <MappingDialog
          initial={batch}
          confirmLabel="Looks right · see prospects"
          onClose={() => setBatch(null)}
          onConfirm={(b) => router.push(`/batches/${b.batch_id}`)}
        />
      )}
    </section>
  );
}
