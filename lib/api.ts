import type { DocumentItem, Job, Results, TraceEvent, ReviewInput, ReviewHistory, ExportItem, SchemaVersion, EvaluationReport } from "./types";
export class ApiError extends Error {
  constructor(message: string, public code: string, public requestId?: string) { super(message); }
}
export function createApi(base: string) {
  const root = base.replace(/\/+$/, "");
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${root}${path}`, { ...init, headers: { ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...init.headers }, signal: init.signal ?? AbortSignal.timeout(30000) });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new ApiError(body?.error?.message ?? `Request failed (${response.status}).`, body?.error?.code ?? "REQUEST_FAILED", body?.error?.request_id);
    if (!body || !("data" in body)) throw new ApiError("The API returned an unexpected response.", "INVALID_RESPONSE");
    return body.data as T;
  }
  const post = <T>(path: string, data: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(data) });
  return {
    documents: () => request<DocumentItem[]>("/documents?limit=200"),
    upload: (file: File) => { const data = new FormData(); data.append("file", file); return request<DocumentItem>("/documents", { method: "POST", body: data }); },
    schemas: () => request<SchemaVersion[]>("/schemas"),
    createJob: (id: string, schemaId: string, repair: boolean) => post<{ job_id: string; status: Job["status"] }>(`/documents/${id}/jobs`, { schema_version_id: schemaId, options: { enable_repair: repair, require_evidence: true }, idempotency_key: crypto.randomUUID() }),
    job: (id: string) => request<Job>(`/jobs/${id}`),
    results: (id: string) => request<Results>(`/jobs/${id}/results`),
    trace: (id: string) => request<TraceEvent[]>(`/jobs/${id}/trace`),
    review: (id: string, input: ReviewInput) => post<Results>(`/fields/${id}/review`, input),
    history: (id: string) => request<ReviewHistory[]>(`/fields/${id}/history`),
    cancel: (id: string) => post<Job>(`/jobs/${id}/cancel`, {}),
    retry: (id: string) => post<Job>(`/jobs/${id}/retry`, {}),
    export: (id: string, format: "csv" | "json", version: number) => post<ExportItem>(`/jobs/${id}/exports`, { format, result_version: version, include_evidence_manifest: true }),
    exportStatus: (id: string) => request<ExportItem>(`/exports/${id}`),
    download: async (id: string) => { const response = await fetch(`${root}/exports/${id}/download`, { signal: AbortSignal.timeout(30000) }); if (!response.ok) throw new Error("Download failed. Try again."); return response.blob(); },
    preview: (id: string, page: number) => `${root}/documents/${id}/pages/${page}/preview`,
    evaluate: () => post<EvaluationReport>("/evaluations", { dataset_version: "ps06-synthetic-v1", configurations: ["native", "single_pass", "evidence_first"] }),
    evaluation: (id: string) => request<EvaluationReport>(`/evaluations/${id}/report`),
  };
}
