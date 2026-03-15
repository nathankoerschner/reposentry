import axios from "axios";

export function getApiErrorMessage(error: unknown, fallback: string): string {
  return (
    (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
    fallback
  );
}

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000",
});

/**
 * Attach Clerk session token to every request.
 * Called once from main.tsx after Clerk loads.
 */
export function setTokenGetter(fn: () => Promise<string | null>) {
  api.interceptors.request.use(async (config) => {
    const token = await fn();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });
}

/* ── Repositories ─────────────────────────────────────────── */

export interface Repository {
  id: string;
  url: string;
  host: string;
  owner: string;
  name: string;
  default_branch: string | null;
  created_at: string;
}

export const listRepositories = () =>
  api.get<Repository[]>("/api/repositories").then((r) => r.data);

export const getRepository = (id: string) =>
  api.get<Repository>(`/api/repositories/${id}`).then((r) => r.data);

export const createRepository = (url: string) =>
  api.post<Repository>("/api/repositories", { url }).then((r) => r.data);

export const deleteRepository = (id: string) =>
  api.delete(`/api/repositories/${id}`).then((r) => r.data);

/* ── Scans ────────────────────────────────────────────────── */

export type ScanStatus = "queued" | "running" | "complete" | "failed";
export type ProcessingStatus = "queued" | "running" | "complete" | "failed" | "skipped";

export interface ScanSummary {
  id: string;
  repository_id: string;
  status: ScanStatus;
  commit_sha: string | null;
  created_at: string;
}

export interface Scan {
  id: string;
  repository_id: string;
  status: ScanStatus;
  commit_sha: string | null;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  created_at: string;
}

export interface ScanFile {
  id: string;
  scan_id: string;
  file_path: string;
  stage1_result: "suspicious" | "not_suspicious" | "failed" | null;
  stage2_attempted: boolean;
  processing_status: ProcessingStatus | null;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
}

export interface ActiveScanFile {
  id: string;
  file_path: string;
  stage1_result: "suspicious" | "not_suspicious" | "failed" | null;
  processing_status: ProcessingStatus;
  started_at: string | null;
}

export interface ScanProgress {
  status: ScanStatus;
  files_total: number;
  files_queued: number;
  files_running: number;
  files_complete: number;
  files_failed: number;
  files_skipped: number;
  findings_so_far: number;
  active_files: ActiveScanFile[];
}

export const listScans = (repoId: string) =>
  api.get<ScanSummary[]>(`/api/repositories/${repoId}/scans`).then((r) => r.data);

export const createScan = (repoId: string) =>
  api.post<Scan>(`/api/repositories/${repoId}/scans`).then((r) => r.data);

export const getScan = (scanId: string) =>
  api.get<Scan>(`/api/scans/${scanId}`).then((r) => r.data);

export const getScanFiles = (scanId: string) =>
  api.get<ScanFile[]>(`/api/scans/${scanId}/files`).then((r) => r.data);

export const getScanProgress = (scanId: string) =>
  api.get<ScanProgress>(`/api/scans/${scanId}/progress`).then((r) => r.data);

export const deleteScan = (scanId: string) =>
  api.delete(`/api/scans/${scanId}`).then((r) => r.data);

/* ── Findings ─────────────────────────────────────────────── */

export type Severity = "low" | "medium" | "high" | "critical";
export type TriageStatus = "open" | "false_positive" | "resolved";

export interface Triage {
  id: string;
  finding_occurrence_id: string;
  status: TriageStatus;
  note: string | null;
  updated_at: string;
}

export interface FindingOccurrence {
  id: string;
  scan_id: string;
  finding_identity_id: string;
  file_path: string;
  line_number: number;
  severity: Severity;
  vulnerability_type: string;
  description: string;
  explanation: string;
  code_snippet: string | null;
  created_at: string;
  triage: Triage | null;
  github_deeplink: string | null;
}

export const getScanFindings = (scanId: string) =>
  api.get<FindingOccurrence[]>(`/api/scans/${scanId}/findings`).then((r) => r.data);

export const updateTriage = (occurrenceId: string, status: TriageStatus, note?: string | null) =>
  api
    .patch<Triage>(`/api/finding-occurrences/${occurrenceId}/triage`, { status, note: note ?? null })
    .then((r) => r.data);

/* ── Comparison ───────────────────────────────────────────── */

export interface ComparisonResponse {
  base_scan_id: string;
  target_scan_id: string;
  new_findings: FindingOccurrence[];
  fixed_findings: FindingOccurrence[];
  persisting_findings: FindingOccurrence[];
}

export const compareScans = (repoId: string, baseScanId: string, targetScanId: string) =>
  api
    .get<ComparisonResponse>(`/api/repositories/${repoId}/compare`, {
      params: { base_scan_id: baseScanId, target_scan_id: targetScanId },
    })
    .then((r) => r.data);

export default api;
