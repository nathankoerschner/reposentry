import type { FindingOccurrence, Severity } from "./api";

const SEVERITY_RANK: Record<Severity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

export function sortFindingsBySeverity(findings: FindingOccurrence[]): FindingOccurrence[] {
  return [...findings].sort((a, b) => {
    const severityDiff = SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity];
    if (severityDiff !== 0) return severityDiff;

    const pathDiff = a.file_path.localeCompare(b.file_path);
    if (pathDiff !== 0) return pathDiff;

    const lineDiff = a.line_number - b.line_number;
    if (lineDiff !== 0) return lineDiff;

    return a.id.localeCompare(b.id);
  });
}
