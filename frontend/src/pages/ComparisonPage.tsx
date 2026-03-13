import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import { compareScans } from "../api";
import type { ComparisonResponse } from "../api";
import { FindingCard } from "../components/FindingCard";
import { sortFindingsBySeverity } from "../severity";
import { ArrowLeft } from "lucide-react";

export function ComparisonPage() {
  const { repoId } = useParams<{ repoId: string }>();
  const [searchParams] = useSearchParams();
  const baseScanId = searchParams.get("base");
  const targetScanId = searchParams.get("target");

  const [data, setData] = useState<ComparisonResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!repoId || !baseScanId || !targetScanId) return;
    setLoading(true);
    compareScans(repoId, baseScanId, targetScanId)
      .then(setData)
      .catch(() => setError("Failed to load comparison"))
      .finally(() => setLoading(false));
  }, [repoId, baseScanId, targetScanId]);

  const sortedData = useMemo(() => {
    if (!data) return null;

    return {
      ...data,
      new_findings: sortFindingsBySeverity(data.new_findings),
      fixed_findings: sortFindingsBySeverity(data.fixed_findings),
      persisting_findings: sortFindingsBySeverity(data.persisting_findings),
    };
  }, [data]);

  if (!baseScanId || !targetScanId) {
    return (
      <div className="content-body">
        <p className="text-error">Missing base or target scan IDs</p>
      </div>
    );
  }

  if (loading) return <div className="loading">Loading comparison...</div>;
  if (error) return <div className="content-body"><p className="text-error">{error}</p></div>;
  if (!sortedData) return null;

  return (
    <>
      <div className="content-header">
        <div className="content-header-left">
          <Link
            to={`/repositories/${repoId}`}
            style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, marginBottom: 4 }}
          >
            <ArrowLeft size={14} />
            Repository
          </Link>
          <h1 className="content-header-title">Scan Comparison</h1>
          <span className="content-header-subtitle">
            Base: {baseScanId.slice(0, 8)}... → Target: {targetScanId.slice(0, 8)}...
          </span>
        </div>
      </div>

      <div className="content-body">
        <div className="page-body">
          <section className="comparison-section new">
            <h2>New Findings ({sortedData.new_findings.length})</h2>
            {sortedData.new_findings.length === 0 ? (
              <p className="text-muted">No new findings.</p>
            ) : (
              <div className="findings-list">
                {sortedData.new_findings.map((f) => (
                  <FindingCard key={f.id} finding={f} label="NEW" />
                ))}
              </div>
            )}
          </section>

          <section className="comparison-section fixed">
            <h2>Fixed Findings ({sortedData.fixed_findings.length})</h2>
            {sortedData.fixed_findings.length === 0 ? (
              <p className="text-muted">No findings were fixed.</p>
            ) : (
              <div className="findings-list">
                {sortedData.fixed_findings.map((f) => (
                  <FindingCard key={f.id} finding={f} label="FIXED" />
                ))}
              </div>
            )}
          </section>

          <section className="comparison-section persisting">
            <h2>Persisting Findings ({sortedData.persisting_findings.length})</h2>
            {sortedData.persisting_findings.length === 0 ? (
              <p className="text-muted">No persisting findings.</p>
            ) : (
              <div className="findings-list">
                {sortedData.persisting_findings.map((f) => (
                  <FindingCard key={f.id} finding={f} label="PERSISTING" />
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </>
  );
}
