import { useMemo, useState } from "react";

type Issue = { issue?: string; ref?: string };

type PageReport = {
  url?: string;
  path?: string;
  title?: string;
  status?: number | string;
  status_label?: string;
  overall_score?: number;
  score_band?: string;
  critical?: Issue[];
  warnings?: Issue[];
  opportunities?: Issue[];
  passing?: string[];
};

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
};

const ALL = "__all__";

export default function SeoAuditCard({ payload, canAct, onAction }: Props) {
  const pages = (payload.pages || []) as PageReport[];
  const [selected, setSelected] = useState(ALL);
  const showActions = canAct && Array.isArray(payload.actions) && payload.actions.length > 0;

  const view = useMemo(() => {
    if (selected === ALL || !pages.length) {
      return {
        label: "All pages",
        score: payload.overall_score,
        band: payload.score_band,
        critical: (payload.critical || []) as Issue[],
        warnings: (payload.warnings || []) as Issue[],
        opportunities: (payload.opportunities || []) as Issue[],
        passing: (payload.passing || []) as string[],
        statusLabel: null as string | null,
        url: null as string | null,
      };
    }
    const page = pages.find((p) => p.url === selected) || pages[0];
    return {
      label: page.path || page.url || "Page",
      score: page.overall_score,
      band: page.score_band,
      critical: page.critical || [],
      warnings: page.warnings || [],
      opportunities: page.opportunities || [],
      passing: page.passing || [],
      statusLabel: page.status_label || null,
      url: page.url || null,
    };
  }, [selected, pages, payload]);

  return (
    <div className="structured-card checkpoint website">
      <h3 className="card-title">{String(payload.title || "SEO Audit Report")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Site: <strong>{String(payload.site || "—")}</strong> · Pages analyzed:{" "}
        {String(payload.pages_analyzed ?? pages.length ?? "—")}
      </p>

      <div style={{ fontSize: 28, fontWeight: 800, marginBottom: 4 }}>
        Site overall: {String(payload.overall_score ?? "—")}
        <span style={{ fontSize: 14, fontWeight: 500, color: "var(--muted)" }}>/100</span>
      </div>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        {String(payload.score_band || "")}
      </p>

      {pages.length > 0 ? (
        <div className="field seo-page-picker" style={{ margin: "12px 0 16px", maxWidth: 520 }}>
          <label htmlFor="seo-audit-page">View report for</label>
          <select
            id="seo-audit-page"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value={ALL}>
              All pages ({pages.length}) — combined findings
            </option>
            {pages.map((p) => (
              <option key={p.url || p.path} value={p.url || ""}>
                {(p.path || p.url || "page") +
                  ` · ${p.overall_score ?? "—"}/100` +
                  (p.title ? ` — ${p.title}` : "")}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {selected !== ALL && view.url ? (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 20, fontWeight: 700 }}>
            This page: {String(view.score ?? "—")}
            <span style={{ fontSize: 13, fontWeight: 500, color: "var(--muted)" }}>/100</span>
          </div>
          <p style={{ fontSize: 13, color: "var(--muted)", margin: "4px 0 0" }}>
            {view.band ? String(view.band) : ""}
            {view.statusLabel ? ` · ${view.statusLabel}` : ""}
          </p>
          <p style={{ fontSize: 12, color: "var(--muted)", margin: "4px 0 0", wordBreak: "break-all" }}>
            {view.url}
          </p>
        </div>
      ) : null}

      <IssueList title="Critical issues (must fix)" items={view.critical} tone="critical" />
      <IssueList title="Warnings (should fix)" items={view.warnings} tone="warning" />
      <IssueList title="Opportunities (nice to have)" items={view.opportunities} tone="info" />

      {view.passing.length > 0 && (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Passing</h4>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {view.passing.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
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

function IssueList({
  title,
  items,
  tone,
}: {
  title: string;
  items: Issue[];
  tone: string;
}) {
  if (!items.length) {
    return (
      <div style={{ marginBottom: 10 }}>
        <h4 style={{ margin: "8px 0 4px", fontSize: 13 }}>{title}</h4>
        <p style={{ fontSize: 13, color: "var(--muted)", margin: 0 }}>None flagged.</p>
      </div>
    );
  }
  return (
    <div style={{ marginBottom: 10 }}>
      <h4 style={{ margin: "8px 0 4px", fontSize: 13 }}>{title}</h4>
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
        {items.map((it, i) => (
          <li key={i} style={{ marginBottom: 4 }}>
            <span
              className={`status-pill ${
                tone === "critical" ? "fail" : tone === "warning" ? "warning" : "pass"
              }`}
            >
              {tone === "critical" ? "Critical" : tone === "warning" ? "Warning" : "Opp"}
            </span>{" "}
            {it.issue}
            {it.ref ? (
              <div style={{ fontSize: 12, color: "var(--muted)" }}>{it.ref}</div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
