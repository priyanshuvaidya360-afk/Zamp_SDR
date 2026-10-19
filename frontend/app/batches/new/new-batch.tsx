"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { StatusChip } from "@/components/status-chip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import type { BatchPreview, Field, Mapping, PreviewRow } from "@/lib/types";
import { cn } from "@/lib/utils";

// Spec: docs/GUIDE.md "UI: three screens" > 1 Upload and preview, and T10.

const NOT_MAPPED = "__not_mapped__";

const FIELDS: { field: Field; label: string; hint?: string }[] = [
  { field: "first_name", label: "First name", hint: "required" },
  { field: "last_name", label: "Last name", hint: "required" },
  { field: "full_name", label: "Full name", hint: "used if first/last are missing" },
  { field: "company_domain", label: "Company domain", hint: "this or LinkedIn URL" },
  { field: "linkedin_url", label: "LinkedIn URL", hint: "this or company domain" },
  { field: "company", label: "Company", hint: "recommended" },
  { field: "title", label: "Title" },
  { field: "email", label: "Email" },
  { field: "notes", label: "Rep notes" },
  { field: "public_bio", label: "Public bio", hint: "treated as untrusted" },
];

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Try again.";
}

export function NewBatch() {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [batch, setBatch] = useState<BatchPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"upload" | "mapping" | "start" | null>(null);
  const [dragging, setDragging] = useState(false);

  async function upload(file: File) {
    setBusy("upload");
    setError(null);
    try {
      setBatch(await api.uploadBatch(file));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function useDemo() {
    const response = await fetch("/demo.csv");
    await upload(new File([await response.blob()], "demo.csv", { type: "text/csv" }));
  }

  async function remap(field: Field, header: string | null) {
    if (!batch) return;
    const mapping: Mapping = { ...batch.preview.mapping, [field]: header };
    setBusy("mapping");
    setError(null);
    try {
      setBatch(await api.updateMapping(batch.batch_id, mapping));
    } catch (e) {
      setError(errorMessage(e)); // the previous preview stays on screen
    } finally {
      setBusy(null);
    }
  }

  async function start() {
    if (!batch) return;
    setBusy("start");
    setError(null);
    try {
      await api.startBatch(batch.batch_id);
      router.push(`/batches/${batch.batch_id}`);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(null);
    }
  }

  if (!batch) {
    return (
      <div className="space-y-4">
        <div
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
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed bg-white px-6 py-16 text-center transition-colors",
            dragging ? "border-primary bg-primary/5" : "border-zinc-300 hover:border-zinc-400",
          )}
        >
          <p className="font-medium">{busy === "upload" ? "Checking your file…" : "Drop a prospect CSV here"}</p>
          <p className="text-sm text-muted-foreground">
            or click to choose a file. Each row needs a first name, last name, and a company domain or LinkedIn URL.
            Up to 50 rows.
          </p>
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
          <Button variant="outline" onClick={useDemo} disabled={busy !== null}>
            Use demo CSV
          </Button>
          <a href="/template.csv" download className="text-sm text-primary underline-offset-4 hover:underline">
            Download the CSV template
          </a>
        </div>
        {error && <ErrorAlert message={error} />}
      </div>
    );
  }

  const { preview } = batch;
  const valid = preview.counts.valid;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-muted-foreground">File</p>
          <p className="font-medium">{batch.filename}</p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => setBatch(null)} disabled={busy !== null}>
          Upload a different file
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Count label="Rows" value={preview.counts.total} />
        <Count label="Valid" value={preview.counts.valid} tone="text-emerald-700" />
        <Count label="Invalid" value={preview.counts.invalid} tone="text-red-700" />
        <Count label="Duplicates" value={preview.counts.duplicate} tone="text-zinc-500" />
      </div>

      {error && <ErrorAlert message={error} />}

      <Card>
        <CardHeader>
          <CardTitle>Column mapping</CardTitle>
          <CardDescription>
            Detected from your headers. Change a column and every row is checked again.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            {FIELDS.map(({ field, label, hint }) => (
              <MappingRow
                key={field}
                label={label}
                hint={hint}
                value={preview.mapping[field]}
                headers={preview.headers}
                disabled={busy !== null}
                onChange={(header) => remap(field, header)}
              />
            ))}
          </div>
          {preview.unmapped_headers.length > 0 && (
            <p className="mt-4 text-sm text-muted-foreground">
              Not used for research (kept for the export): {preview.unmapped_headers.join(", ")}
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Preview</CardTitle>
          <CardDescription>
            {preview.rows.length < preview.counts.total
              ? `First ${preview.rows.length} of ${preview.counts.total} rows.`
              : `All ${preview.counts.total} rows.`}{" "}
            Row numbers match your spreadsheet.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <PreviewTable rows={preview.rows} />
        </CardContent>
      </Card>

      <div className="flex items-center justify-end gap-4">
        {valid === 0 && <p className="text-sm text-muted-foreground">No valid rows to run.</p>}
        <Button size="lg" onClick={start} disabled={valid === 0 || busy !== null}>
          {busy === "start" ? "Starting…" : `Run ${valid} prospect${valid === 1 ? "" : "s"}`}
        </Button>
      </div>
    </div>
  );
}

function Count({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-xl border bg-white px-4 py-3">
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className={cn("text-2xl font-semibold tabular-nums", tone)}>{value}</p>
    </div>
  );
}

function MappingRow({
  label,
  hint,
  value,
  headers,
  disabled,
  onChange,
}: {
  label: string;
  hint?: string;
  value: string | null;
  headers: string[];
  disabled: boolean;
  onChange: (header: string | null) => void;
}) {
  const items: Record<string, string> = { [NOT_MAPPED]: "Not mapped" };
  for (const header of headers) items[header] = header;
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      <Select
        items={items}
        value={value ?? NOT_MAPPED}
        onValueChange={(next) => onChange(!next || next === NOT_MAPPED ? null : String(next))}
        disabled={disabled}
      >
        <SelectTrigger className="w-52">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Object.entries(items).map(([key, text]) => (
            <SelectItem key={key} value={key}>
              {text}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function PreviewTable({ rows }: { rows: PreviewRow[] }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-14">Row</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Name</TableHead>
            <TableHead>Company</TableHead>
            <TableHead>Domain / LinkedIn</TableHead>
            <TableHead className="min-w-52">Notes</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => {
            const d = row.data;
            const name = d.full_name || [d.first_name, d.last_name].filter(Boolean).join(" ") || "—";
            return (
              <TableRow key={row.row_number} className={row.status !== "valid" ? "bg-zinc-50/80" : undefined}>
                <TableCell className="text-muted-foreground tabular-nums">{row.row_number}</TableCell>
                <TableCell>
                  <StatusChip status={row.status} kind="row" />
                </TableCell>
                <TableCell>
                  <p className="font-medium">{name}</p>
                  {d.title && <p className="text-xs text-muted-foreground">{d.title}</p>}
                </TableCell>
                <TableCell>{d.company || <span className="text-muted-foreground">—</span>}</TableCell>
                <TableCell className="max-w-44 truncate text-muted-foreground">
                  {d.company_domain || d.linkedin_url || "—"}
                </TableCell>
                <TableCell className="min-w-52 text-xs whitespace-normal">
                  {row.reason && (
                    <span className={row.status === "invalid" ? "text-red-700" : "text-muted-foreground"}>
                      {row.reason}
                    </span>
                  )}
                  {row.warnings.map((w) => (
                    <span key={w} className="block text-amber-700">
                      {w}
                    </span>
                  ))}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function ErrorAlert({ message }: { message: string }) {
  return (
    <Alert variant="destructive">
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  );
}
