import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getScan,
  getRepository,
  getScanFiles,
  getScanFindings,
} from "../api";
import type {
  FindingOccurrence,
  Repository,
  Scan,
  ScanFile,
} from "../api";
import { FindingCard } from "../components/FindingCard";
import { StatusBadge } from "../components/StatusBadge";
import { sortFindingsBySeverity } from "../severity";
import { AlertCircle, ArrowLeft, LoaderCircle } from "lucide-react";

const POLL_INTERVAL_MS = 3000;

export function ScanDetailPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const [scan, setScan] = useState<Scan | null>(null);
  const [repo, setRepo] = useState<Repository | null>(null);
  const [files, setFiles] = useState<ScanFile[]>([]);
  const [findings, setFindings] = useState<FindingOccurrence[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timeoutId: number | undefined;

    async function load(initialLoad = false) {
      if (!scanId) return;
      if (initialLoad) {
        setLoading(true);
      }

      try {
        const currentScan = await getScan(scanId);
        if (cancelled) return;

        setScan(currentScan);
        setError(null);

        const [scanFiles, repoResult, findingResults] = await Promise.all([
          getScanFiles(scanId).catch(() => [] as ScanFile[]),
          currentScan.repository_id
            ? getRepository(currentScan.repository_id).catch(() => null)
            : Promise.resolve(null),
          currentScan.status === "complete"
            ? getScanFindings(scanId).catch(() => [] as FindingOccurrence[])
            : Promise.resolve([] as FindingOccurrence[]),
        ]);

        if (cancelled) return;

        setFiles(scanFiles);
        setRepo(repoResult);
        setFindings(sortFindingsBySeverity(findingResults));

        if (currentScan.status === "queued" || currentScan.status === "running") {
          timeoutId = window.setTimeout(() => {
            void load();
          }, POLL_INTERVAL_MS);
        }
      } catch {
        if (cancelled) return;
        setError("Failed to load scan details");
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load(true);

    return () => {
      cancelled = true;
      if (timeoutId) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [scanId]);

  const processedFiles = useMemo(
    () => files.filter((file) => file.processing_status !== null).length,
    [files],
  );
  const failedFiles = useMemo(
    () => files.filter((file) => file.processing_status === "failed").length,
    [files],
  );
  const progressPercent = useMemo(() => {
    if (!scan) return 0;
    if (scan.status === "complete") return 100;
    if (scan.status === "failed") {
      return files.length > 0 ? Math.round((processedFiles / files.length) * 100) : 100;
    }
    if (files.length === 0) return 12;
    return Math.max(12, Math.round((processedFiles / files.length) * 100));
  }, [files.length, processedFiles, scan]);

  if (loading) return <div className="loading">Loading...</div>;
  if (!scan) return <div className="loading">Scan not found</div>;

  const isActive = scan.status === "queued" || scan.status === "running";
  const statusText =
    scan.status === "complete" && scan.completed_at
      ? `Completed ${formatTimeSince(new Date(scan.completed_at))}`
      : scan.status === "running" && scan.started_at
        ? `Running for ${formatTimeSince(new Date(scan.started_at))}`
        : scan.status.charAt(0).toUpperCase() + scan.status.slice(1);

  const subtitleRepo = repo
    ? `${repo.owner}/${repo.name}`
    : scan.repository_id.slice(0, 8);

  return (
    <>
      <div className="content-header">
        <div className="content-header-left">
          <Link
            to={scan.repository_id ? `/repositories/${scan.repository_id}` : "/repositories"}
            style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, marginBottom: 4 }}
          >
            <ArrowLeft size={14} />
            {repo ? "Repository" : "Repositories"}
          </Link>
          <h1 className="content-header-title">
            {isActive ? "Scan in Progress" : "Scan Results"}
          </h1>
          <span className="content-header-subtitle">
            {subtitleRepo} · {repo?.default_branch ?? "main"} branch · {statusText}
          </span>
        </div>
      </div>

      <div className="content-body">
        <div className="page-body">
          {error && <p className="text-error">{error}</p>}

          {isActive && (
            <section className="scan-progress-card">
              <div className="scan-progress-header">
                <div>
                  <p className="scan-progress-eyebrow">Scanner status</p>
                  <div className="scan-progress-title-row">
                    <h2>Analyzing repository files</h2>
                    <StatusBadge status={scan.status} />
                  </div>
                  <p className="scan-progress-subtitle">
                    {files.length > 0
                      ? `${processedFiles} of ${files.length} files processed`
                      : "Preparing file inventory and waiting for the worker to begin."}
                  </p>
                </div>
                <div className="scan-spinner-wrap" aria-hidden="true">
                  <LoaderCircle className="scan-spinner" size={24} />
                </div>
              </div>

              <div
                className="scan-progress-bar"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={progressPercent}
              >
                <div
                  className={`scan-progress-fill ${files.length === 0 ? "indeterminate" : ""}`}
                  style={{ width: `${progressPercent}%` }}
                />
              </div>

              <div className="scan-progress-metrics">
                <div className="scan-progress-metric">
                  <span className="label">Progress</span>
                  <span className="value">{progressPercent}%</span>
                </div>
                <div className="scan-progress-metric">
                  <span className="label">Files processed</span>
                  <span className="value">{processedFiles}</span>
                </div>
                <div className="scan-progress-metric">
                  <span className="label">Files discovered</span>
                  <span className="value">{files.length || "—"}</span>
                </div>
              </div>

              <p className="poll-notice">
                This page refreshes automatically every few seconds and will show the
                findings list as soon as the scan completes.
              </p>
            </section>
          )}

          {scan.status === "failed" && (
            <section className="scan-status-panel failed">
              <div className="scan-status-panel-icon">
                <AlertCircle size={20} />
              </div>
              <div>
                <h2>Scan failed</h2>
                <p>{scan.error_message ?? "The worker could not complete this scan."}</p>
              </div>
            </section>
          )}

          {!isActive && (
            <section className="scan-summary-grid">
              <div className="scan-summary-card">
                <span className="label">Status</span>
                <span className="value">
                  {scan.status === "complete" ? "Completed" : "Failed"}
                </span>
              </div>
              <div className="scan-summary-card">
                <span className="label">Files processed</span>
                <span className="value">{processedFiles}</span>
              </div>
              <div className="scan-summary-card">
                <span className="label">Failed files</span>
                <span className="value">{failedFiles}</span>
              </div>
              <div className="scan-summary-card">
                <span className="label">Findings</span>
                <span className="value">{findings.length}</span>
              </div>
            </section>
          )}

          {!isActive && scan.status === "complete" && (
            <section>
              <div className="section-header">
                <h2 className="section-title">Findings</h2>
              </div>

              {findings.length === 0 ? (
                <div className="empty-state">
                  <p>No findings detected for this scan.</p>
                </div>
              ) : (
                <div className="findings-list">
                  {findings.map((finding) => (
                    <FindingCard key={finding.id} finding={finding} />
                  ))}
                </div>
              )}
            </section>
          )}
        </div>
      </div>
    </>
  );
}

function formatTimeSince(date: Date): string {
  const now = new Date();
  const diffMs = Math.max(now.getTime() - date.getTime(), 0);
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDays = Math.floor(diffHr / 24);
  return `${diffDays}d ago`;
}
