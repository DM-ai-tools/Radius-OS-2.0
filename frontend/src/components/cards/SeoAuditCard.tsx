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
  cluster?: string;
  cluster_label?: string;
  page_type?: string;
  cdd_focus?: boolean;
  cdd_terms?: string[];
  critical?: Issue[];
  warnings?: Issue[];
  opportunities?: Issue[];
  passing?: string[];
};

type ClusterRow = {
  cluster?: string;
  label?: string;
  count?: number;
  cdd_count?: number;
  avg_score?: number | null;
};

type HierarchyRow = {
  level?: number;
  cluster?: string;
  cluster_label?: string;
  page_type?: string;
  path?: string;
  url?: string;
  title?: string;
  overall_score?: number | null;
  cdd_focus?: boolean;
  cdd_terms?: string[];
};

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
};

const ALL = "__all__";
const CLUSTER_PREFIX = "__cluster__:";

const HIERARCHY_ORDER = [
  "home",
  "service_hub",
  "services",
  "sub_services",
  "location",
  "guides",
  "blog",
  "other",
];

function clusterKey(page: PageReport): string {
  return page.cluster || "other";
}

function clusterLabel(page: PageReport): string {
  return page.cluster_label || page.cluster || "Other pages";
}

function avgScore(rows: PageReport[]): number | null {
  const scores = rows.map((p) => Number(p.overall_score)).filter((n) => Number.isFinite(n));
  if (!scores.length) return null;
  return Math.round(scores.reduce((a, b) => a + b, 0) / scores.length);
}

export default function SeoAuditCard({ payload, canAct, onAction }: Props) {
  const pages = (payload.pages || []) as PageReport[];
  const payloadClusters = Array.isArray(payload.page_clusters)
    ? (payload.page_clusters as ClusterRow[])
    : [];
  const hierarchy = Array.isArray(payload.page_hierarchy)
    ? (payload.page_hierarchy as HierarchyRow[])
    : [];
  const [selected, setSelected] = useState(ALL);
  const showActions = canAct && Array.isArray(payload.actions) && payload.actions.length > 0;
  const focusNote = String(payload.audit_focus_note || "");
  const cddPages = Number(payload.cdd_pages_count ?? pages.filter((p) => p.cdd_focus).length);
  const gaps = Array.isArray(payload.cdd_coverage_gaps)
    ? (payload.cdd_coverage_gaps as { term?: string; issue?: string }[])
    : [];

  const grouped = useMemo(() => {
    const map = new Map<string, PageReport[]>();
    for (const page of pages) {
      const key = clusterKey(page);
      const list = map.get(key) || [];
      list.push(page);
      map.set(key, list);
    }
    const keys = [
      ...HIERARCHY_ORDER.filter((k) => map.has(k)),
      ...[...map.keys()].filter((k) => !HIERARCHY_ORDER.includes(k)),
    ];
    return keys.map((key) => ({
      cluster: key,
      label: map.get(key)?.[0] ? clusterLabel(map.get(key)![0]) : key,
      pages: map.get(key) || [],
    }));
  }, [pages]);

  const clusters = payloadClusters.length
    ? payloadClusters
    : grouped.map((g) => ({
        cluster: g.cluster,
        label: g.label,
        count: g.pages.length,
        cdd_count: g.pages.filter((p) => p.cdd_focus).length,
        avg_score: avgScore(g.pages),
      }));

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
        clusterPages: pages,
      };
    }
    if (selected.startsWith(CLUSTER_PREFIX)) {
      const key = selected.slice(CLUSTER_PREFIX.length);
      const clusterPages = pages.filter((p) => clusterKey(p) === key);
      return {
        label: clusterPages[0] ? clusterLabel(clusterPages[0]) : key,
        score: avgScore(clusterPages),
        band: null as string | null,
        critical: clusterPages.flatMap((p) => p.critical || []),
        warnings: clusterPages.flatMap((p) => p.warnings || []),
        opportunities: clusterPages.flatMap((p) => p.opportunities || []),
        passing: [...new Set(clusterPages.flatMap((p) => p.passing || []))],
        statusLabel: `${clusterPages.length} pages`,
        url: null as string | null,
        clusterPages,
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
      clusterPages: [page],
    };
  }, [selected, pages, payload]);

  return (
    <div className="structured-card checkpoint website">
      <h3 className="card-title">{String(payload.title || "SEO Audit Report")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Site: <strong>{String(payload.site || "—")}</strong> · Pages analyzed:{" "}
        {String(payload.pages_analyzed ?? pages.length ?? "—")}
        {cddPages > 0 ? ` · CDD money pages: ${cddPages}` : ""}
      </p>
      {focusNote ? (
        <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>{focusNote}</p>
      ) : null}

      <div style={{ fontSize: 28, fontWeight: 800, marginBottom: 4 }}>
        Site overall: {String(payload.overall_score ?? "—")}
        <span style={{ fontSize: 14, fontWeight: 500, color: "var(--muted)" }}>/100</span>
      </div>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        {String(payload.score_band || "")}
        {payload.business_weighted_score != null
          ? ` · Business-weighted ${String(payload.business_weighted_score)}`
          : ""}
      </p>

      {clusters.length ? (
        <div className="cs-funnel-strip seo-cluster-strip">
          {clusters.map((c) => {
            const key = String(c.cluster || "");
            const active = selected === `${CLUSTER_PREFIX}${key}`;
            return (
              <button
                key={key || String(c.label)}
                type="button"
                className={`seo-cluster-chip${active ? " is-active" : ""}`}
                onClick={() => setSelected(`${CLUSTER_PREFIX}${key}`)}
              >
                {String(c.label || key)} <strong>{String(c.count ?? 0)}</strong>
                {c.avg_score != null ? <span> · {String(c.avg_score)}</span> : null}
                {c.cdd_count ? <span> · CDD {String(c.cdd_count)}</span> : null}
              </button>
            );
          })}
        </div>
      ) : null}

      {hierarchy.length > 0 && selected === ALL ? (
        <div style={{ overflowX: "auto", margin: "10px 0 14px" }}>
          <h4 style={{ margin: "0 0 6px", fontSize: 13 }}>Page hierarchy</h4>
          <table
            className="kw-report-table"
            style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}
          >
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: "6px 8px" }}>Tier</th>
                <th style={{ textAlign: "left", padding: "6px 8px" }}>Path</th>
                <th style={{ textAlign: "left", padding: "6px 8px" }}>Type</th>
                <th style={{ textAlign: "left", padding: "6px 8px" }}>CDD</th>
                <th style={{ textAlign: "left", padding: "6px 8px" }}>Score</th>
              </tr>
            </thead>
            <tbody>
              {hierarchy.slice(0, 40).map((row) => (
                <tr key={row.url || row.path}>
                  <td style={{ padding: "6px 8px" }}>{row.cluster_label || row.cluster}</td>
                  <td style={{ padding: "6px 8px" }}>
                    <button
                      type="button"
                      className="seo-cluster-link"
                      onClick={() => row.url && setSelected(row.url)}
                    >
                      {row.path || row.url || "page"}
                    </button>
                  </td>
                  <td style={{ padding: "6px 8px" }}>{row.page_type || "—"}</td>
                  <td style={{ padding: "6px 8px" }}>
                    {row.cdd_focus ? (row.cdd_terms || []).slice(0, 2).join(", ") || "Yes" : "—"}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{row.overall_score ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {pages.length > 0 ? (
        <div className="field seo-page-picker" style={{ margin: "12px 0 16px", maxWidth: 560 }}>
          <label htmlFor="seo-audit-page">View report for</label>
          <select
            id="seo-audit-page"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value={ALL}>All pages ({pages.length}) — combined findings</option>
            {grouped.map((g) => (
              <optgroup key={g.cluster} label={`${g.label} (${g.pages.length})`}>
                <option value={`${CLUSTER_PREFIX}${g.cluster}`}>
                  Entire {g.label.toLowerCase()} tier
                </option>
                {g.pages.map((p) => (
                  <option key={p.url || p.path} value={p.url || ""}>
                    {(p.path || p.url || "page") +
                      ` · ${p.overall_score ?? "—"}/100` +
                      (p.cdd_focus ? " · CDD" : "") +
                      (p.title ? ` — ${p.title}` : "")}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
      ) : null}

      {selected !== ALL && (view.url || view.statusLabel) ? (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 20, fontWeight: 700 }}>
            {view.url ? "This page" : view.label}: {String(view.score ?? "—")}
            <span style={{ fontSize: 13, fontWeight: 500, color: "var(--muted)" }}>/100</span>
          </div>
          <p style={{ fontSize: 13, color: "var(--muted)", margin: "4px 0 0" }}>
            {view.band ? String(view.band) : ""}
            {view.statusLabel ? ` · ${view.statusLabel}` : ""}
          </p>
          {view.url ? (
            <p style={{ fontSize: 12, color: "var(--muted)", margin: "4px 0 0", wordBreak: "break-all" }}>
              {view.url}
            </p>
          ) : null}
        </div>
      ) : null}

      {view.clusterPages.length > 1 || selected.startsWith(CLUSTER_PREFIX) ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table
            className="kw-report-table"
            style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}
          >
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: "6px 8px" }}>Page</th>
                <th style={{ textAlign: "left", padding: "6px 8px" }}>Type</th>
                <th style={{ textAlign: "left", padding: "6px 8px" }}>CDD</th>
                <th style={{ textAlign: "left", padding: "6px 8px" }}>Score</th>
              </tr>
            </thead>
            <tbody>
              {view.clusterPages.map((p) => (
                <tr key={p.url || p.path}>
                  <td style={{ padding: "6px 8px" }}>
                    <button
                      type="button"
                      className="seo-cluster-link"
                      onClick={() => p.url && setSelected(p.url)}
                    >
                      {p.path || p.url || "page"}
                    </button>
                    {p.title ? (
                      <div style={{ color: "var(--muted)", fontSize: 11 }}>{p.title}</div>
                    ) : null}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{p.page_type || "—"}</td>
                  <td style={{ padding: "6px 8px" }}>
                    {p.cdd_focus ? (p.cdd_terms || []).slice(0, 2).join(", ") || "Yes" : "—"}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{p.overall_score ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {gaps.length > 0 && selected === ALL ? (
        <div style={{ marginBottom: 12 }}>
          <h4 style={{ margin: "8px 0 4px", fontSize: 13 }}>CDD coverage gaps</h4>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {gaps.slice(0, 8).map((g, i) => (
              <li key={i}>{g.issue || `No page mapped to “${g.term}”`}</li>
            ))}
          </ul>
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
