// The only way the frontend talks to the backend. NEXT_PUBLIC_API_URL is baked in at build
// time; it defaults to the local FastAPI server (make dev-api).

import type {
  BatchDetail,
  BatchListItem,
  BatchPreview,
  Candidate,
  DraftResponse,
  Mapping,
  ReviewAction,
  ReviewResult,
  Page,
  RunDetail,
  RunStatus,
  RunSummary,
  SellerProfile,
  StartResponse,
  Stats,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new ApiError(0, `Can't reach the API at ${API_URL}. Is the backend running (make dev-api)?`);
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // not JSON: keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  uploadBatch(file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<BatchPreview>("/batches", { method: "POST", body: form });
  },
  getPreview: (batchId: number) => request<BatchPreview>(`/batches/${batchId}/preview`),
  updateMapping: (batchId: number, mapping: Mapping) =>
    request<BatchPreview>(`/batches/${batchId}/mapping`, json("PATCH", { mapping })),
  startBatch: (batchId: number) => request<StartResponse>(`/batches/${batchId}/start`, { method: "POST" }),
  /** Start the pipeline for these rows (all remaining valid rows when none are given). */
  draftRows: (batchId: number, rowNumbers?: number[]) =>
    request<DraftResponse>(`/batches/${batchId}/draft`, json("POST", { row_numbers: rowNumbers ?? null })),
  getBatch: (batchId: number) => request<BatchDetail>(`/batches/${batchId}`),
  getSeller: () => request<SellerProfile>("/seller"),
  getRun: (runId: number) => request<RunDetail>(`/runs/${runId}`),
  getStats: () => request<Stats>("/stats"),
  listBatches: (limit: number, offset: number) => request<Page<BatchListItem>>(`/batches?limit=${limit}&offset=${offset}`),
  listRuns: (opts: { status?: RunStatus | ""; q?: string; limit: number; offset: number }) => {
    const params = new URLSearchParams({ limit: String(opts.limit), offset: String(opts.offset) });
    if (opts.status) params.set("status", opts.status);
    if (opts.q?.trim()) params.set("q", opts.q.trim());
    return request<Page<RunSummary>>(`/runs?${params}`);
  },
  resumeRun: (runId: number, candidate: Candidate) =>
    request<{ run_id: number; status: string }>(`/runs/${runId}/resume`, json("POST", { stage: "resolve", input: { candidate } })),
  review: (runId: number, action: ReviewAction, opts: { body?: string; reason?: string; draftId?: number } = {}) =>
    request<ReviewResult>(
      `/runs/${runId}/review`,
      json("POST", { action, body: opts.body, reason: opts.reason, draft_id: opts.draftId }),
    ),
  /** A plain link: the browser downloads the file. Approved rows only unless `all`. */
  exportUrl: (batchId: number, all = false) => `${API_URL}/batches/${batchId}/export.csv${all ? "?all=true" : ""}`,
};

/** SSE endpoints (opened with EventSource by useEventStream). */
export const streams = {
  batch: (batchId: number) => `${API_URL}/batches/${batchId}/events`,
  run: (runId: number, after = 0) => `${API_URL}/runs/${runId}/events?after=${after}`,
};
