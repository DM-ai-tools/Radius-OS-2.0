import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

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
  score_band?: string;
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

function humanizeAuditError(raw: unknown): string {
  const text = String(raw || "").trim();
  if (!text) {
    return "This audit did not measure any pages, so it cannot be approved. Re-run the website audit.";
  }
  const key = text.toLowerCase();
  if (key.includes("timeout") || key === "comprehensive_seo_audit_timeout") {
    return (
      "The SEO audit ran out of time before pages could be measured. " +
      "Re-run the website audit — slower sites (or WAF delays) need a longer pass."
    );
  }
  if (key.includes("_") && !text.includes(" ")) {
    // Snake-case machine keys → readable sentence
    return text.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()) + ". Re-run the website audit.";
  }
  return text;
}

function clusterKey(page: PageReport): string {
  return page.cluster || "other";
}

function clusterLabel(page: PageReport): string {
  return page.cluster_label || page.cluster || "Other pages";
}

function pageLabel(page: PageReport): string {
  const path = page.path || page.url || "Page";
  const title = (page.title || "").trim();
  if (!title || title === path || title === page.url || title === `/${path.replace(/^\//, "")}`) {
    return path;
  }
  return title;
}

function isMeasuredPage(page: PageReport): boolean {
  return !String(page.score_band || "").toLowerCase().startsWith("not measured");
}

function scoreTone(score: unknown): "good" | "fair" | "poor" | "none" {
  const n = Number(score);
  if (score == null || score === "" || !Number.isFinite(n)) return "none";
  if (n >= 80) return "good";
  if (n >= 50) return "fair";
  return "poor";
}

function avgScore(rows: PageReport[]): number | null {
  const scores = rows
    .filter(isMeasuredPage)
    .map((p) => Number(p.overall_score))
    .filter((n) => Number.isFinite(n));
  if (!scores.length) return null;
  return Math.round(scores.reduce((a, b) => a + b, 0) / scores.length);
}

export default function SeoAuditCard({ payload, canAct, onAction }: Props) {
  const pages = (payload.pages || []) as PageReport[];
  const unmeasured = pages.length > 0 && pages.every((page) => !isMeasuredPage(page));
  const measured =
    !unmeasured &&
    (pages.some(isMeasuredPage) ||
      Number(payload.pages_analyzed) > 0 ||
      (payload.overall_score != null && payload.overall_score !== ""));
  const blockedNote = unmeasured
    ? String(
        ((payload.warnings || []) as Issue[])[0]?.issue ||
          "Live HTML was blocked, so these pages were listed but not scored.",
      )
    : "";
  const failed = Boolean(payload.audit_failed) || Boolean(payload.error) || unmeasured || !measured;
  const payloadClusters = Array.isArray(payload.page_clusters)
    ? (payload.page_clusters as ClusterRow[])
    : [];
  const hierarchy = Array.isArray(payload.page_hierarchy)
    ? (payload.page_hierarchy as HierarchyRow[])
    : [];
  const [selected, setSelected] = useState(ALL);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerQuery, setPickerQuery] = useState("");
  const pickerRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const showActions = canAct && Array.isArray(payload.actions) && payload.actions.length > 0 && !failed;
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

  useEffect(() => {
    // The card scrolls inside a fixed height, so bring the opened menu into view.
    if (pickerOpen) menuRef.current?.scrollIntoView({ block: "nearest" });
  }, [pickerOpen]);

  function onMenuKey(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLElement>(".seo-page-search, .seo-page-group-name, .seo-page-row") || [],
    );
    if (!items.length) return;
    event.preventDefault();
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next = event.key === "ArrowDown" ? Math.min(items.length - 1, at + 1) : Math.max(0, at - 1);
    items[next]?.focus();
  }

  useEffect(() => {
    if (!pickerOpen) return;
    function onPointer(event: MouseEvent) {
      if (!pickerRef.current?.contains(event.target as Node)) setPickerOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setPickerOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [pickerOpen]);

  const pickerGroups = useMemo(() => {
    const q = pickerQuery.trim().toLowerCase();
    return grouped
      .map((group) => ({
        ...group,
        pages: q
          ? group.pages.filter((page) =>
              `${page.path || ""} ${page.url || ""} ${page.title || ""}`.toLowerCase().includes(q),
            )
          : group.pages,
      }))
      .filter((group) => group.pages.length > 0);
  }, [grouped, pickerQuery]);

  const pickerTitle = view.url ? pageLabel(pages.find((page) => page.url === selected) || { path: view.label }) : view.label;
  const pickerCount = selected === ALL
    ? pages.length
    : selected.startsWith(CLUSTER_PREFIX)
      ? view.clusterPages.length
      : null;

  function choose(next: string) {
    setSelected(next);
    setPickerOpen(false);
    setPickerQuery("");
  }

  return (
    <div className="structured-card checkpoint website">
      <h3 className="card-title">{String(payload.title || "SEO Audit Report")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Site: <strong>{String(payload.site || "—")}</strong> · Pages analyzed:{" "}
        {pages.length
          ? String(pages.length)
          : failed
            ? "0"
            : String(payload.pages_analyzed ?? "—")}
        {cddPages > 0 ? ` · CDD money pages: ${cddPages}` : ""}
      </p>
      {failed ? (
        <p style={{ fontSize: 13, color: "var(--danger, #9b2c2c)", marginTop: 0 }}>
          {humanizeAuditError(
            blockedNote ||
              payload.error_message ||
              payload.error ||
              "This audit did not measure any pages, so it cannot be approved. Re-run the website audit.",
          )}
        </p>
      ) : null}
      {focusNote ? (
        <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>{focusNote}</p>
      ) : null}

      {failed ? null : (
        <>
          <div style={{ fontSize: 28, fontWeight: 800, marginBottom: 4 }}>
            Site overall: {String(payload.overall_score ?? "—")}
            <span style={{ fontSize: 14, fontWeight: 500, color: "var(--muted)" }}>/100</span>
          </div>
          <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
            {payload.score_band ? `${String(payload.score_band)}. ` : ""}
            {payload.business_weighted_score != null
              ? `Business-weighted score ${String(payload.business_weighted_score)}.`
              : ""}
          </p>
        </>
      )}

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
                aria-pressed={active}
                onClick={() => setSelected(`${CLUSTER_PREFIX}${key}`)}
              >
                <span className="seo-cluster-name">{String(c.label || key)}</span>
                <span className="seo-cluster-meta">
                  <span>
                    <strong>{String(c.count ?? 0)}</strong> pages
                  </span>
                  {!unmeasured && c.avg_score != null ? (
                    <span>
                      <strong className={Number(c.avg_score) === 0 ? "is-zero" : undefined}>
                        {String(c.avg_score)}
                      </strong>{" "}
                      avg
                    </span>
                  ) : null}
                  {c.cdd_count ? (
                    <span>
                      <strong>{String(c.cdd_count)}</strong> CDD
                    </span>
                  ) : null}
                </span>
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
                  <td style={{ padding: "6px 8px" }}>
                    {unmeasured || String(row.score_band || "").toLowerCase().startsWith("not measured")
                      ? "—"
                      : (row.overall_score ?? "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {pages.length > 0 ? (
        <div className="field seo-page-picker" ref={pickerRef}>
          <label id="seo-audit-page-label">View report for</label>
          <button
            type="button"
            className="seo-page-trigger"
            aria-labelledby="seo-audit-page-label"
            aria-expanded={pickerOpen}
            aria-controls="seo-audit-page-menu"
            onClick={() => setPickerOpen((open) => !open)}
          >
            <span className="seo-page-trigger-name">{pickerTitle}</span>
            <span className="seo-page-trigger-end">
              {pickerCount != null ? (
                <span className="seo-page-trigger-count">
                  {pickerCount} {pickerCount === 1 ? "page" : "pages"}
                </span>
              ) : null}
              <span className="seo-page-chevron" aria-hidden />
            </span>
          </button>
          {pickerOpen ? (
            <div
              ref={menuRef}
              className="seo-page-menu"
              id="seo-audit-page-menu"
              role="listbox"
              aria-labelledby="seo-audit-page-label"
              onKeyDown={onMenuKey}
            >
              <input
                className="seo-page-search"
                type="search"
                value={pickerQuery}
                placeholder="Find a page"
                aria-label="Find a page"
                onChange={(event) => setPickerQuery(event.target.value)}
                autoFocus
              />
              {!pickerQuery.trim() ? (
                <button
                  type="button"
                  role="option"
                  aria-selected={selected === ALL}
                  className={`seo-page-row${selected === ALL ? " is-active" : ""}`}
                  onClick={() => choose(ALL)}
                >
                  <span className="seo-page-path">All pages</span>
                  <span className="seo-page-note">Combined findings, {pages.length} pages</span>
                </button>
              ) : null}
              {pickerGroups.length === 0 ? (
                <p className="seo-page-empty">No pages match.</p>
              ) : (
                pickerGroups.map((group) => (
                  <div key={group.cluster} className="seo-page-group">
                    <button
                      type="button"
                      role="option"
                      aria-selected={selected === `${CLUSTER_PREFIX}${group.cluster}`}
                      title={`Combined findings for all ${group.label.toLowerCase()}`}
                      className={`seo-page-group-name${selected === `${CLUSTER_PREFIX}${group.cluster}` ? " is-active" : ""}`}
                      onClick={() => choose(`${CLUSTER_PREFIX}${group.cluster}`)}
                    >
                      <span>{group.label}</span>
                      <span className="seo-page-note">
                        {group.pages.length} {group.pages.length === 1 ? "page" : "pages"}
                        {!unmeasured && avgScore(group.pages) != null ? `, avg ${avgScore(group.pages)}` : ""}
                      </span>
                    </button>
                    {group.pages.map((page) => {
                      const value = page.url || "";
                      const measured = !unmeasured && isMeasuredPage(page);
                      return (
                        <button
                          key={value || page.path}
                          type="button"
                          role="option"
                          aria-selected={selected === value}
                          className={`seo-page-row${selected === value ? " is-active" : ""}`}
                          onClick={() => value && choose(value)}
                        >
                          <span className="seo-page-path" title={page.url || page.path}>
                            {page.path || page.url || "Page"}
                          </span>
                          <span className="seo-page-meta">
                            {page.cdd_focus ? (
                              <span className="seo-page-tag" title="A money page named in the Client Digital Dossier">
                                CDD
                              </span>
                            ) : null}
                            <span
                              className={`seo-page-score is-${measured ? scoreTone(page.overall_score) : "none"}`}
                              aria-label={measured ? `Score ${page.overall_score ?? "not scored"} out of 100` : "Not measured"}
                            >
                              {measured && page.overall_score != null ? (
                                <>
                                  {page.overall_score}
                                  <span className="seo-page-score-of">/100</span>
                                </>
                              ) : (
                                "—"
                              )}
                            </span>
                          </span>
                        </button>
                      );
                    })}
                  </div>
                ))
              )}
            </div>
          ) : null}
        </div>
      ) : null}

      {selected !== ALL && (view.url || view.statusLabel) ? (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 20, fontWeight: 700 }}>
            {view.url ? "This page" : view.label}:{" "}
            {unmeasured || String(view.band || "").toLowerCase().startsWith("not measured")
              ? "not measured"
              : String(view.score ?? "—")}
            {unmeasured || String(view.band || "").toLowerCase().startsWith("not measured") ? null : (
              <span style={{ fontSize: 13, fontWeight: 500, color: "var(--muted)" }}>/100</span>
            )}
          </div>
          <p style={{ fontSize: 13, color: "var(--muted)", margin: "4px 0 0" }}>
            {view.band ? String(view.band) : ""}
            {view.statusLabel ? `${view.band ? " · " : ""}${view.statusLabel}` : ""}
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
                  <td style={{ padding: "6px 8px" }}>
                    {unmeasured || !isMeasuredPage(p) ? "—" : (p.overall_score ?? "—")}
                  </td>
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

      {failed ? null : (
        <>
          <IssueList title="Critical issues (must fix)" items={view.critical} tone="critical" collapsible />
          <IssueList title="Warnings (should fix)" items={view.warnings} tone="warning" collapsible />
          <IssueList title="Opportunities (nice to have)" items={view.opportunities} tone="info" collapsible />
        </>
      )}

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
  collapsible = false,
}: {
  title: string;
  items: Issue[];
  tone: string;
  collapsible?: boolean;
}) {
  const pill =
    tone === "critical" ? "fail" : tone === "warning" ? "warning" : "pass";
  const label = tone === "critical" ? "Critical" : tone === "warning" ? "Warning" : "Opp";
  const rows = items.map((it, i) => (
    <li key={i}>
      <span className={`status-pill ${pill}`}>{label}</span> {it.issue}
      {it.ref ? <div className="seo-issue-fold__ref">{it.ref}</div> : null}
    </li>
  ));

  if (collapsible) {
    return (
      <details className={`seo-issue-fold is-${tone}`}>
        <summary>
          <span>{title}</span>
          <span className={`status-pill ${pill} seo-issue-fold__count`}>{items.length}</span>
        </summary>
        {items.length ? (
          <ul className="seo-issue-fold__list">{rows}</ul>
        ) : (
          <p className="seo-issue-fold__empty">None flagged.</p>
        )}
      </details>
    );
  }

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
      <ul className="seo-issue-fold__list">{rows}</ul>
    </div>
  );
}
