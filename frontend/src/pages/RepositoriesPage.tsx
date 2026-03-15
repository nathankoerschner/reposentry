import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listRepositories, createRepository, deleteRepository, getApiErrorMessage } from "../api";
import type { Repository } from "../api";
import { GitBranch, Plus, Trash2 } from "lucide-react";

export function RepositoriesPage() {
  const navigate = useNavigate();
  const [repos, setRepos] = useState<Repository[]>([]);
  const [loading, setLoading] = useState(true);
  const [url, setUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const [deletingRepoId, setDeletingRepoId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      setRepos(await listRepositories());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setAdding(true);
    setError(null);
    try {
      await createRepository(url.trim());
      setUrl("");
      await load();
    } catch (err: unknown) {
      setError(getApiErrorMessage(err, "Failed to add repository"));
    } finally {
      setAdding(false);
    }
  }

  async function handleDeleteRepo(
    e: React.MouseEvent<HTMLButtonElement>,
    repo: Repository,
  ) {
    e.stopPropagation();
    const confirmed = window.confirm(
      `Delete ${repo.owner}/${repo.name}? This will also remove its scans and findings.`,
    );
    if (!confirmed) return;

    setDeletingRepoId(repo.id);
    setError(null);
    try {
      await deleteRepository(repo.id);
      await load();
    } catch (err: unknown) {
      setError(getApiErrorMessage(err, "Failed to delete repository"));
    } finally {
      setDeletingRepoId(null);
    }
  }

  return (
    <>
      <div className="content-header">
        <h1 className="content-header-title">Repositories</h1>
      </div>

      <div className="content-body">
        <div className="page-body">
          <form className="add-repo-form" onSubmit={handleAdd}>
            <input
              type="url"
              placeholder="https://github.com/owner/repo"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
              disabled={adding}
            />
            <button className="btn btn-primary btn-sm" type="submit" disabled={adding}>
              <Plus size={16} />
              {adding ? "Adding..." : "Add Repository"}
            </button>
          </form>

          {error && <p className="text-error">{error}</p>}

          {loading ? (
            <div className="loading">Loading...</div>
          ) : repos.length === 0 ? (
            <div className="empty-state">
              <GitBranch size={32} />
              <p>No repositories yet. Add one above.</p>
            </div>
          ) : (
            <div className="table-card">
              <div className="table-header-row">
                <span className="table-header-cell col-repo">Repository</span>
                <span className="table-header-cell col-branch">Branch</span>
                <span className="table-header-cell col-date">Added</span>
                <span className="table-header-cell col-actions">Actions</span>
              </div>
              {repos.map((r) => (
                <div
                  key={r.id}
                  className="table-row"
                  onClick={() => navigate(`/repositories/${r.id}`)}
                >
                  <span className="table-cell repo col-repo">
                    {r.owner}/{r.name}
                  </span>
                  <span className="table-cell branch col-branch">
                    {r.default_branch ?? "—"}
                  </span>
                  <span className="table-cell date col-date">
                    {new Date(r.created_at).toLocaleDateString("en-US", {
                      month: "short",
                      day: "numeric",
                      year: "numeric",
                    })}
                  </span>
                  <span className="table-cell col-actions table-actions">
                    <button
                      className="btn btn-outline btn-sm danger-button"
                      type="button"
                      onClick={(e) => handleDeleteRepo(e, r)}
                      disabled={deletingRepoId === r.id}
                    >
                      <Trash2 size={16} />
                      {deletingRepoId === r.id ? "Deleting..." : "Delete"}
                    </button>
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
