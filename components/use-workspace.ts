"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { createApi } from "@/lib/api";
import { downloadFile } from "@/lib/domain";
import type { DemoState, DocumentItem, FieldName, ReviewInput, SchemaVersion, EvaluationReport, Job, ExportItem } from "@/lib/types";

const emptyState = (): DemoState => ({ documents: [], records: [], traces: [], history: [], exports: [], snapshots: {} });
const CONFIG_KEY = "beeproof-connection-v1";
const DEFAULT_API_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000/api/v1";

export function useWorkspace() {
  const [state, setState] = useState<DemoState>(emptyState);
  const [ready, setReady] = useState(false);
  const [baseUrl, setBaseUrl] = useState(DEFAULT_API_URL);
  const [mode, setMode] = useState<"live">("live");
  const [schemas, setSchemas] = useState<SchemaVersion[]>([]);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [evaluation, setEvaluation] = useState<EvaluationReport | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const api = useMemo(() => createApi(baseUrl), [baseUrl]);

  useEffect(() => {
    try {
      const config = JSON.parse(localStorage.getItem(CONFIG_KEY) ?? "null");
      const url = config?.baseUrl || DEFAULT_API_URL;
      setBaseUrl(url);
      setMode("live");
    } catch {
      setBaseUrl(DEFAULT_API_URL);
      setMode("live");
    }
    setReady(true);
  }, []);

  const refresh = useCallback(async () => {
    const [documents, availableSchemas] = await Promise.all([api.documents(), api.schemas()]);
    setSchemas(availableSchemas);
    const eligible = documents.filter(d => ["completed", "review_required"].includes(d.status));
    const resultSets = await Promise.all(
      eligible.map(async d => ({
        id: d.job_id,
        data: await api.results(d.job_id),
        trace: await api.trace(d.job_id),
      }))
    );
    setState(prev => ({
      ...prev,
      documents: documents.map(d => ({ ...d, is_demo: false })),
      records: resultSets.flatMap(r => r.data.records),
      traces: resultSets.flatMap(r => r.trace),
    }));
  }, [api]);

  useEffect(() => {
    if (!ready) return;
    let active = true;
    setBusy(true);
    refresh()
      .catch(e => {
        if (active) setError(e instanceof Error ? e.message : "Failed to connect to API.");
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [ready, refresh]);

  useEffect(() => {
    const active = state.documents.filter(
      d => !["completed", "review_required", "failed", "cancelled"].includes(d.status) && d.job_id
    );
    if (!active.length) return;
    let stopped = false, timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const results = await Promise.all(active.map(d => api.job(d.job_id)));
        if (stopped) return;
        setJobs(prev => ({ ...prev, ...Object.fromEntries(results.map(j => [j.id, j])) }));
        setState(prev => ({
          ...prev,
          documents: prev.documents.map(d => {
            const j = results.find(j => j.id === d.job_id);
            return j ? { ...d, status: j.status, result_version: j.result_version ?? d.result_version } : d;
          }),
        }));
        if (results.some(j => ["completed", "review_required", "failed", "cancelled"].includes(j.status))) {
          await refresh();
        }
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e.message : "Job polling failed.");
      }
      if (!stopped) timer = setTimeout(poll, 2000);
    };

    timer = setTimeout(poll, 1000);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [state.documents, api, refresh]);

  useEffect(() => {
    if (!evaluation || !["queued", "running"].includes(evaluation.status)) return;
    let stopped = false;
    const timer = setTimeout(async () => {
      try {
        const report = await api.evaluation(evaluation.id);
        if (!stopped) setEvaluation(report);
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e.message : "Evaluation status unavailable.");
      }
    }, 2500);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [api, evaluation]);

  async function act<T>(fn: () => Promise<T>, message?: string): Promise<T | undefined> {
    setBusy(true);
    setError("");
    try {
      const result = await fn();
      if (message) setNotice(message);
      return result;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong. Try again.");
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File, schemaId: string, repair: boolean) {
    return act(async () => {
      const doc = await api.upload(file);
      setState(prev => ({ ...prev, documents: [{ ...doc, is_demo: false }, ...prev.documents] }));
      try {
        const targetSchema = schemaId || schemas[0]?.id || "rate_sheet:1";
        const job = await api.createJob(doc.id, targetSchema, repair);
        const updated = { ...doc, job_id: job.job_id, status: job.status, is_demo: false };
        setState(prev => ({
          ...prev,
          documents: prev.documents.map(d => (d.id === doc.id ? updated : d)),
        }));
        return updated;
      } catch (e) {
        throw new Error(
          `File was uploaded, but processing could not start: ${
            e instanceof Error ? e.message : "Unknown error"
          }. Open its document and retry.`
        );
      }
    }, "Document uploaded and extraction started.");
  }

  async function review(recordId: string, field: FieldName, input: ReviewInput) {
    return act(async () => {
      const record = state.records.find(r => r.id === recordId);
      if (!record || !record.fields[field]) throw new Error("Record or field not found.");
      await api.review(record.fields[field].id, input);
      const history = await api.history(record.fields[field].id);
      await refresh();
      setState(prev => ({
        ...prev,
        history: [...prev.history.filter(h => h.field_id !== record.fields[field].id), ...history],
      }));
      return true;
    }, "Review saved and revalidated.");
  }

  async function loadHistory(id: string) {
    try {
      const history = await api.history(id);
      setState(prev => ({
        ...prev,
        history: [...prev.history.filter(h => h.field_id !== id), ...history],
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load field history.");
    }
  }

  async function exportResults(doc: DocumentItem, format: "csv" | "json", version: number) {
    return act(async () => {
      const item = await api.export(doc.job_id, format, version);
      item.document_name = doc.original_filename;
      if (item.status === "ready") {
        downloadFile(await api.download(item.id), `beeproof-${doc.id}-v${version}.${format}`);
      }
      setState(prev => ({ ...prev, exports: [item, ...prev.exports] }));
      return true;
    }, "Export created for the selected result version.");
  }

  async function downloadExport(item: ExportItem) {
    return act(async () => {
      const updated = await api.exportStatus(item.id);
      setState(prev => ({
        ...prev,
        exports: prev.exports.map(e => (e.id === item.id ? { ...e, ...updated } : e)),
      }));
      if (updated.status !== "ready") {
        throw new Error(
          updated.status === "failed"
            ? `Export failed: ${updated.error_code ?? "unknown error"}`
            : "The export is still being generated. Check again shortly."
        );
      }
      downloadFile(await api.download(item.id), `beeproof-v${item.result_version}.${item.format}`);
    });
  }

  async function connect(url: string) {
    return act(async () => {
      let parsed: URL;
      try {
        parsed = new URL(url);
      } catch {
        throw new Error("Enter an absolute API URL, such as http://localhost:8000/api/v1.");
      }
      if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
        throw new Error("Use an HTTP(S) URL without embedded credentials.");
      }
      await createApi(url).documents();
      localStorage.setItem(CONFIG_KEY, JSON.stringify({ mode: "live", baseUrl: url }));
      setBaseUrl(url);
      setJobs({});
      setEvaluation(null);
      await refresh();
      return true;
    }, "Backend connected.");
  }

  async function jobAction(doc: DocumentItem, action: "cancel" | "retry", schemaId?: string) {
    return act(async () => {
      if (!doc.job_id && action === "retry") {
        const job = await api.createJob(doc.id, schemaId ?? schemas[0]?.id, true);
        setState(prev => ({
          ...prev,
          documents: prev.documents.map(d => (d.id === doc.id ? { ...d, job_id: job.job_id, status: job.status } : d)),
        }));
      } else {
        const job = await api[action](doc.job_id);
        setState(prev => ({
          ...prev,
          documents: prev.documents.map(d => (d.id === doc.id ? { ...d, job_id: job.id, status: job.status } : d)),
        }));
      }
      return true;
    });
  }

  return {
    state,
    ready,
    mode,
    baseUrl,
    schemas,
    jobs,
    evaluation,
    error,
    notice,
    busy,
    setError,
    setNotice,
    upload,
    review,
    loadHistory,
    exportResults,
    downloadExport,
    connect,
    jobAction,
    resetDemo: () => {},
    refresh: () => act(refresh),
    runEvaluation: () =>
      act(async () => {
        setEvaluation(await api.evaluate());
        return true;
      }),
  };
}
