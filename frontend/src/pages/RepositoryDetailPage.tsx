import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getRepository, listScans, createScan, deleteScan, getApiErrorMessage } from "../api";
import type { Repository, ScanSummary } from "../api";
import { StatusBadge } from "../components/StatusBadge";
import { Play, GitCompare, Trash2 } from "lucide-react";

export function RepositoryDetailPage() {
  const { repoId } = useParams<{ repoId: string }>();
  const navigate = useNavigate();
  const [repo, setRepo] = useState<Repository | null>(null);
  const [scans, setScans] = useState<ScanSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [deletingScanId, setDeletingScanId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [baseScanId, setBaseScanId] = useState("");
  const [targetScanId, setTargetScanId] = useState("");

  async function load() {
    if (!repoId) return;
    setLoading(true);
    try {
      const [r, s] = await Promise.all([
        getRepository(repoId),
        listScans(repoId),
      ]);
      setRepo(r);
      setScans(s);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [repoId]);

  async function handleScan() {
    if (!repoId) return;
    setScanning(true);
    setError(null);
    try {
      const scan = await createScan(repoId);
      navigate(`/scans/${scan.id}`);
    } catch {
      setError("Failed to start scan");
    } finally {
      setScanning(false);
    }
  }

  function handleCompare() {
    if (!repoId || !baseScanId || !targetScanId) return;
    navigate(
      `/repositories/${repoId}/compare?base=${baseScanId}&target=${targetScanId}`
    );
  }

  async function handleDeleteScan(
    e: React.MouseEvent<HTMLButtonElement>,
    scan: ScanSummary,
  ) {
    e.stopPropagation();
    const confirmed = window.confirm(
      `Delete scan ${scan.id.slice(0, 8)}...? This will remove its findings and file results.`,
    );
    if (!confirmed) return;

    setDeletingScanId(scan.id);
    setError(null);
    try {
      await deleteScan(scan.id);
      if (baseScanId === scan.id) setBaseScanId("");
      if (targetScanId === scan.id) setTargetScanId("");
      await load();
    } catch (err: unknown) {
      setError(getApiErrorMessage(err, "Failed to delete scan"));
    } finally {
      setDeletingScanId(null);
    }
  }

  if (loading) return <div className="loading">Loading...</div>;
  if (!repo) return <div className="loading">Repository not found</div>;

  const completedScans = scans.filter((s) => s.status === "complete");

  return (
    <>
      <div className="content-header">
        <div className="content-header-left">
          <h1 className="content-header-title">
            {repo.owner}/{repo.name}
          </h1>
          <span className="content-header-subtitle">
            {repo.default_branch ?? "main"} · {repo.host}
          </span>
        </div>
        <div className="content-header-actions">
          <button
            className="btn btn-primary btn-sm"
            onClick={handleScan}
            disabled={scanning}
          >
            <Play size={16} />
            {scanning ? "Starting..." : "New Scan"}
          </button>
        </div>
      </div>

      <div className="content-body">
        <div className="page-body">
          {error && <p className="text-error">{error}</p>}

          <div>
            <div className="section-header">
              <h2 className="section-title">Scan History</h2>
            </div>

            {scans.length === 0 ? (
              <div className="empty-state">
                <p>No scans yet. Start one above.</p>
              </div>
            ) : (
              <div className="table-card">
                <div className="table-header-row">
                  <span className="table-header-cell col-repo">Scan</span>
                  <span className="table-header-cell col-status">Status</span>
                  <span className="table-header-cell col-branch">Commit</span>
                  <span className="table-header-cell col-date">Created</span>
                  <span className="table-header-cell col-actions">Actions</span>
                </div>
                {scans.map((s) => (
                  <div
                    key={s.id}
                    className="table-row"
                    onClick={() => navigate(`/scans/${s.id}`)}
                  >
                    <span className="table-cell repo col-repo">
                      {s.id.slice(0, 8)}...
                    </span>
                    <span className="table-cell col-status">
                      <StatusBadge status={s.status} />
                    </span>
                    <span className="table-cell branch col-branch">
                      {s.commit_sha?.slice(0, 7) ?? "—"}
                    </span>
                    <span className="table-cell date col-date">
                      {new Date(s.created_at).toLocaleString()}
                    </span>
                    <span className="table-cell col-actions table-actions">
                      <button
                        className="btn btn-outline btn-sm danger-button"
                        type="button"
                        onClick={(e) => handleDeleteScan(e, s)}
                        disabled={deletingScanId === s.id}
                      >
                        <Trash2 size={16} />
                        {deletingScanId === s.id ? "Deleting..." : "Delete"}
                      </button>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {completedScans.length >= 2 && (
            <div>
              <div className="section-header">
                <h2 className="section-title">Compare Scans</h2>
              </div>
              <div className="compare-picker">
                <label>
                  Base (older):
                  <select
                    value={baseScanId}
                    onChange={(e) => setBaseScanId(e.target.value)}
                  >
                    <option value="">Select...</option>
                    {completedScans.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.id.slice(0, 8)} — {s.commit_sha?.slice(0, 7) ?? "?"}{" "}
                        — {new Date(s.created_at).toLocaleDateString()}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Target (newer):
                  <select
                    value={targetScanId}
                    onChange={(e) => setTargetScanId(e.target.value)}
                  >
                    <option value="">Select...</option>
                    {completedScans.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.id.slice(0, 8)} — {s.commit_sha?.slice(0, 7) ?? "?"}{" "}
                        — {new Date(s.created_at).toLocaleDateString()}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="btn btn-outline btn-sm"
                  onClick={handleCompare}
                  disabled={
                    !baseScanId ||
                    !targetScanId ||
                    baseScanId === targetScanId
                  }
                >
                  <GitCompare size={16} />
                  Compare
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
