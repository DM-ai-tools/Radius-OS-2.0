type Fix = { priority?: string; issue?: string; fix?: string };

type Section = { score?: number; findings?: string[] };

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
};

const SECTION_LABELS: Record<string, string> = {
  crawlability: "Crawlability",
  indexation: "Indexation",
  performance: "Performance",
  mobile: "Mobile",
  security: "Security",
  structured_data: "Structured data",
};

export default function TechnicalSeoCard({ payload, canAct, onAction }: Props) {
  const sections = (payload.sections || {}) as Record<string, Section>;
  const fixes = (payload.priority_fixes || []) as Fix[];
  const showActions = canAct && Array.isArray(payload.actions) && payload.actions.length > 0;

  return (
    <div className="structured-card checkpoint website">
      <h3 className="card-title">{String(payload.title || "Technical SEO Audit Report")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Site: <strong>{String(payload.site || "—")}</strong>
      </p>
      <div style={{ fontSize: 28, fontWeight: 800, marginBottom: 12 }}>
        Score: {String(payload.score ?? "—")}
        <span style={{ fontSize: 14, fontWeight: 500, color: "var(--muted)" }}>/100</span>
      </div>

      {Object.entries(sections).map(([key, sec]) => (
        <div key={key} style={{ marginBottom: 12 }}>
          <h4 style={{ margin: "0 0 4px", fontSize: 13 }}>
            {SECTION_LABELS[key] || key}: {sec.score ?? "—"}/100
          </h4>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {(sec.findings || []).map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </div>
      ))}

      {fixes.length > 0 && (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Priority fixes</h4>
          <ol style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {fixes.map((f, i) => (
              <li key={i} style={{ marginBottom: 6 }}>
                <strong>[{f.priority || "Medium"}]</strong> {f.issue} — {f.fix}
              </li>
            ))}
          </ol>
        </>
      )}

      {showActions && (
        <div className="card-actions">
          <button className="btn btn-primary" onClick={() => onAction("approve")}>
            Approve
          </button>
          <button className="btn btn-amber" onClick={() => onAction("edit")}>
            Edit
          </button>
          <button className="btn btn-danger" onClick={() => onAction("reject")}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
