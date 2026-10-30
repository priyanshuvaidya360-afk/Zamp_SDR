"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import type { BatchPreview, Field, Mapping } from "@/lib/types";
import { cn } from "@/lib/utils";

// Step 2 of the flow: after the upload, check which CSV column feeds which field. Opens as a
// dialog over the page; changing a column re-checks every row. Spec: docs/PROJECT_SPEC.md "Input: the
// prospect CSV" (header aliases) and T10.

const NOT_MAPPED = "__not_mapped__";

const FIELDS: { field: Field; label: string; hint: string; required?: boolean }[] = [
  { field: "first_name", label: "First name", hint: "required", required: true },
  { field: "last_name", label: "Last name", hint: "required", required: true },
  { field: "linkedin_url", label: "LinkedIn URL", hint: "an anchor: the person's profile (linkedin.com/in/…)" },
  { field: "email", label: "Email", hint: "an anchor if it's a work email (not Gmail etc.)" },
  { field: "company_domain", label: "Company domain", hint: "an anchor; filled from a work email if empty" },
  { field: "company", label: "Company", hint: "recommended" },
  { field: "title", label: "Title", hint: "shapes who the email is written for" },
  { field: "full_name", label: "Full name", hint: "used if first/last are missing" },
  { field: "notes", label: "Rep notes", hint: "your own context; trusted" },
  { field: "public_bio", label: "Public bio", hint: "treated as untrusted" },
];

export function MappingDialog({
  initial,
  confirmLabel,
  onConfirm,
  onClose,
}: {
  initial: BatchPreview;
  confirmLabel: string;
  onConfirm: (batch: BatchPreview) => void;
  onClose: () => void;
}) {
  const [batch, setBatch] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { preview } = batch;
  const { counts } = preview;
  const warned = preview.rows.filter((row) => row.status === "valid" && row.warnings.length > 0);
  const noAnchorColumn = !preview.mapping.linkedin_url && !preview.mapping.email && !preview.mapping.company_domain;

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function remap(field: Field, header: string | null) {
    const mapping: Mapping = { ...preview.mapping, [field]: header };
    setBusy(true);
    setError(null);
    try {
      setBatch(await api.updateMapping(batch.batch_id, mapping));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't update the mapping."); // the previous preview stays
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-navy/60 p-4 backdrop-blur-[2px]" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="mapping-title"
        className="flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 bg-navy px-7 py-5 text-white">
          <div>
            <p className="text-xs font-bold tracking-[0.08em] text-[#a9c1ee]">STEP 2 · MAP COLUMNS</p>
            <h2 id="mapping-title" className="mt-1 text-xl font-bold tracking-tight">
              Match your columns to what Signal needs
            </h2>
            <p className="mt-1 text-sm text-[#c9d8f5]">
              {batch.filename} · we guessed from your headers. Change any field and every row is checked again.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="rounded-lg px-2 py-1 text-xl leading-none text-[#c9d8f5] hover:bg-white/10 hover:text-white">
            ×
          </button>
        </div>

        <div className="space-y-6 overflow-y-auto px-7 py-6">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <Count label="Rows" value={counts.total} />
            <Count label="Ready to draft" value={counts.valid} tone="text-[#137a43]" />
            <Count label="Need a fix" value={counts.invalid} tone="text-red-700" />
            <Count label="Duplicates" value={counts.duplicate} tone="text-muted-foreground" />
            <Count label="Ready, with warnings" value={counts.warnings ?? warned.length} tone="text-[#a14a00]" />
          </div>

          {warned.length > 0 && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
              <p className="text-xs font-bold tracking-[0.08em] text-[#a14a00] uppercase">Check these rows (they will still run)</p>
              <ul className="mt-1.5 space-y-1 text-sm">
                {warned.map((row) => (
                  <li key={row.row_number} className="min-w-0">
                    <span className="font-semibold tabular-nums">Row {row.row_number}</span>
                    <span className="text-muted-foreground"> · {[row.data.first_name, row.data.last_name].filter(Boolean).join(" ")}: </span>
                    <span className="break-words text-[#7a3a00]">{row.warnings.join("; ")}</span>
                  </li>
                ))}
              </ul>
              {(counts.warnings ?? 0) > warned.length && (
                <p className="mt-1.5 text-xs text-muted-foreground">Showing the first {preview.rows.length} rows of the file.</p>
              )}
            </div>
          )}

          {error && <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">{error}</p>}
          {noAnchorColumn && (
            <p className="text-sm text-[#a14a00]">Map at least one of LinkedIn URL, Email or Company domain: each row needs one to identify the person.</p>
          )}

          <div className="grid gap-x-8 gap-y-3.5 sm:grid-cols-2">
            {FIELDS.map(({ field, label, hint, required }) => (
              <MappingRow
                key={field}
                label={label}
                hint={hint}
                required={required}
                value={preview.mapping[field]}
                headers={preview.headers}
                disabled={busy}
                onChange={(header) => remap(field, header)}
              />
            ))}
          </div>
          {preview.unmapped_headers.length > 0 && (
            <p className="text-sm text-muted-foreground">Not used for research (kept for the export): {preview.unmapped_headers.join(", ")}</p>
          )}

          <div>
            <h3 className="mb-2 text-xs font-bold tracking-[0.08em] text-primary uppercase">What we read from your first rows</h3>
            <div className="overflow-x-auto rounded-xl border">
              <table className="w-full text-sm">
                <thead className="bg-zinc-50 text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 font-semibold">Row</th>
                    <th className="px-3 py-2 font-semibold">Name</th>
                    <th className="px-3 py-2 font-semibold">Company</th>
                    <th className="px-3 py-2 font-semibold">LinkedIn / Email / Domain</th>
                    <th className="px-3 py-2 font-semibold">Check</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.slice(0, 5).map((row) => {
                    const d = row.data;
                    return (
                      <tr key={row.row_number} className="border-t">
                        <td className="px-3 py-2 text-muted-foreground tabular-nums">{row.row_number}</td>
                        <td className="px-3 py-2 font-medium">{d.full_name || [d.first_name, d.last_name].filter(Boolean).join(" ") || "—"}</td>
                        <td className="px-3 py-2">{d.company || "—"}</td>
                        <td className="max-w-48 truncate px-3 py-2 text-muted-foreground">{d.linkedin_url || d.email || d.company_domain || "—"}</td>
                        <td className="px-3 py-2">
                          {row.status === "valid" ? (
                            <span className={cn("font-semibold", row.warnings.length ? "text-[#a14a00]" : "text-[#137a43]")}>
                              {row.warnings.length ? `✓ ready · ${row.warnings.length} warning${row.warnings.length === 1 ? "" : "s"}` : "✓ ready"}
                            </span>
                          ) : (
                            <span className={row.status === "invalid" ? "text-red-700" : "text-muted-foreground"}>{row.reason}</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t bg-zinc-50 px-7 py-4">
          <p className="text-sm text-muted-foreground">
            {counts.valid === 0 ? "No valid rows yet. Fix the mapping above." : `${counts.valid} prospect${counts.valid === 1 ? "" : "s"} ready. Nothing is researched until you click Draft email.`}
          </p>
          <div className="flex gap-2">
            <Button variant="outline" size="lg" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button size="lg" onClick={() => onConfirm(batch)} disabled={busy || counts.valid === 0}>
              {confirmLabel}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Count({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-xl border px-4 py-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn("text-2xl font-bold tabular-nums", tone)}>{value}</p>
    </div>
  );
}

function MappingRow({
  label,
  hint,
  required,
  value,
  headers,
  disabled,
  onChange,
}: {
  label: string;
  hint: string;
  required?: boolean;
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
        <p className="flex items-center gap-1.5 text-sm font-semibold">
          {label}
          {value && <span className="text-[#1a9a56]">✓</span>}
        </p>
        <p className={cn("text-xs", required && !value ? "text-[#a14a00]" : "text-muted-foreground")}>{hint}</p>
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
