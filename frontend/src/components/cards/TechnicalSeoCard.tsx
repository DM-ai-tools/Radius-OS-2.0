type Fix = { priority?: string; issue?: string; fix?: string; source?: string };

type Section = { score?: number; findings?: string[] };

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
};

function asRows(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && typeof r === "object") as Array<Record<string, unknown>>;
}

const SECTION_LABELS: Record<string, string> = {
  crawlability: "Crawlability",
  indexation: "Indexation",
  performance: "Performance",
  mobile: "Mobile",
  security: "Security",
  structured_data: "Structured data",
};

export default function TechnicalSeoCard({ payload, canAct, onAction }: Props) {
  const sections = (payload.sections || payload.technical_sections || {}) as Record<
    string,
    Section
  >;
  const themes = asRows(payload.findings_by_theme);
  const backlog = asRows(payload.priority_backlog);
  const fixes =
    backlog.length > 0
      ? (backlog as Fix[])
      : ((payload.priority_fixes || []) as Fix[]);
  const broken =
    payload.broken_links && typeof payload.broken_links === "object"
      ? (payload.broken_links as Record<string, unknown>)
      : null;
  const internalBroken = Array.isArray(broken?.internal) ? (broken!.internal as unknown[]) : [];
  const externalBroken = Array.isArray(broken?.external) ? (broken!.external as unknown[]) : [];
  const iaNotes = Array.isArray(payload.ia_notes)
    ? (payload.ia_notes as string[])
    : typeof payload.ia_notes === "string"
      ? [String(payload.ia_notes)]
      : [];
  const iaActions = asRows(payload.ia_actions);
  const notMeasured = asRows(payload.not_measured);
  const routing = asRows(payload.specialist_routing);
  const site = String(payload.site || payload.primary_url || payload.client_name || "—");
  const phase6Connected = Boolean(payload.phase6_connected);
  const categoryScores =
    payload.category_scores && typeof payload.category_scores === "object"
      ? (payload.category_scores as Record<string, Record<string, unknown>>)
      : null;
  const issues = asRows(payload.issues);
  const issuesByCategory =
    payload.issues_by_category && typeof payload.issues_by_category === "object"
      ? (payload.issues_by_category as Record<string, unknown[]>)
      : null;
  const auditComparison =
    payload.audit_comparison && typeof payload.audit_comparison === "object"
      ? (payload.audit_comparison as Record<string, unknown>)
      : null;
  const cwv =
    payload.core_web_vitals && typeof payload.core_web_vitals === "object"
      ? (payload.core_web_vitals as Record<string, unknown>)
      : null;
  const rendering =
    payload.rendering_audit && typeof payload.rendering_audit === "object"
      ? (payload.rendering_audit as Record<string, unknown>)
      : null;
  const hreflang =
    payload.hreflang_check && typeof payload.hreflang_check === "object"
      ? (payload.hreflang_check as Record<string, unknown>)
      : null;
  const internalLinking =
    payload.internal_linking && typeof payload.internal_linking === "object"
      ? (payload.internal_linking as Record<string, unknown>)
      : null;
  const gscIndexation =
    payload.gsc_indexation && typeof payload.gsc_indexation === "object"
      ? (payload.gsc_indexation as Record<string, unknown>)
      : null;
  const suggestionItems = asRows(payload.suggestion_items);
  const severitySummary =
    payload.severity_summary && typeof payload.severity_summary === "object"
      ? (payload.severity_summary as Record<string, number>)
      : null;
  const scoreLabel =
    (payload.score ?? payload.overall_score) != null
      ? Number(payload.score ?? payload.overall_score) >= 90
        ? "Excellent"
        : Number(payload.score ?? payload.overall_score) >= 75
          ? "Good"
          : Number(payload.score ?? payload.overall_score) >= 50
            ? "Needs improvement"
            : Number(payload.score ?? payload.overall_score) >= 25
              ? "Poor"
              : "Critical"
      : null;

  return (
    <div className="structured-card structured-card--report">
      <h3 className="card-title">{String(payload.title || "Technical SEO Audit Report")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Site: <strong>{site}</strong>
        {payload.severity ? ` · ${String(payload.severity)}` : ""}
        {phase6Connected ? " · Phase 6 IA connected" : " · Phase 6 IA not in memory"}
        {payload.js_rendering_risk ? " · JS rendering risk" : ""}
        {payload.audit_state ? ` · ${String(payload.audit_state)}` : ""}
      </p>
      <div style={{ fontSize: 28, fontWeight: 800, marginBottom: 4 }}>
        Score: {String(payload.score ?? payload.overall_score ?? "—")}
        <span style={{ fontSize: 14, fontWeight: 500, color: "var(--muted)" }}>/100</span>
      </div>
      {scoreLabel ? (
        <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>{scoreLabel}</p>
      ) : null}

      {severitySummary ? (
        <div className="cs-funnel-strip" style={{ marginBottom: 12 }}>
          {Object.entries(severitySummary).map(([k, v]) => (
            <span key={k}>
              {k} <strong>{String(v)}</strong>
            </span>
          ))}
        </div>
      ) : null}

      {categoryScores ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Category scores</h4>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
            {Object.entries(categoryScores).map(([cat, row]) => (
              <span
                key={cat}
                style={{
                  fontSize: 12,
                  padding: "4px 10px",
                  borderRadius: 6,
                  border: "1px solid var(--line)",
                }}
              >
                {cat}:{" "}
                {row.status === "NOT_AVAILABLE"
                  ? "N/A"
                  : `${String(row.score ?? "—")}/100`}
              </span>
            ))}
          </div>
        </>
      ) : null}

      {payload.executive_summary ? (
        <p style={{ fontSize: 14, lineHeight: 1.5 }}>{String(payload.executive_summary)}</p>
      ) : payload.note ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>{String(payload.note)}</p>
      ) : null}

      {auditComparison && auditComparison.delta != null ? (
        <p style={{ fontSize: 13, marginTop: 0, marginBottom: 12 }}>
          vs previous: {String(auditComparison.previous_score)} → {String(auditComparison.current_score)}{" "}
          <strong>
            ({Number(auditComparison.delta) > 0 ? "+" : ""}
            {String(auditComparison.delta)})
          </strong>
        </p>
      ) : null}

      {cwv?.field && typeof cwv.field === "object" ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Core Web Vitals</h4>
          <div className="cs-funnel-strip" style={{ marginBottom: 12 }}>
            {Object.entries(cwv.field as Record<string, unknown>).slice(0, 6).map(([k, v]) => (
              <span key={k}>
                {k} <strong>{String(v)}</strong>
              </span>
            ))}
          </div>
        </>
      ) : null}

      {rendering ? (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Rendering snapshot</h4>
          <p style={{ fontSize: 12, color: "var(--muted)" }}>
            {rendering.parity_ok ? "Desktop/mobile parity OK" : "Parity gaps detected"}
            {Array.isArray(rendering.parity_issues) && rendering.parity_issues.length > 0
              ? ` — ${(rendering.parity_issues as string[]).slice(0, 2).join("; ")}`
              : ""}
          </p>
        </>
      ) : null}

      {hreflang?.hreflang_found ? (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Hreflang surface check</h4>
          <p style={{ fontSize: 12, color: "var(--muted)" }}>
            {String(hreflang.pages_with_hreflang)} page(s) with annotations
            {hreflang.issue_count ? ` · ${String(hreflang.issue_count)} surface issue(s)` : ""}
          </p>
        </>
      ) : null}

      {internalLinking && Number(internalLinking.orphan_count || 0) > 0 ? (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Internal linking</h4>
          <p style={{ fontSize: 12, color: "var(--muted)" }}>
            {String(internalLinking.orphan_count)} orphan URL(s), {String(internalLinking.deep_count || 0)} at depth 4+
          </p>
        </>
      ) : null}

      {gscIndexation ? (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Search Console indexation</h4>
          <p style={{ fontSize: 12, color: "var(--muted)" }}>
            {String(gscIndexation.property || "GSC property")} · {String(gscIndexation.sitemap_count || 0)}{" "}
            sitemap(s)
            {gscIndexation.error_sitemaps
              ? ` · ${String(gscIndexation.error_sitemaps)} with errors`
              : ""}
          </p>
        </>
      ) : null}

      {suggestionItems.length > 0 ? (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Suggestion ledger</h4>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
            {suggestionItems.slice(0, 8).map((item, i) => (
              <li key={`suggestion-${i}`}>
                {String(item.title || item.issue || "Issue")}{" "}
                <span style={{ color: "var(--muted)" }}>
                  ({String(item.confidence || "—")} · {String(item.status || "pending")})
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {issuesByCategory && Object.keys(issuesByCategory).length > 0 ? (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Issues by category</h4>
          {Object.entries(issuesByCategory).map(([cat, rows]) => (
            <div key={cat} style={{ marginBottom: 8 }}>
              <strong style={{ fontSize: 12 }}>{cat}</strong>
              <span style={{ fontSize: 11, color: "var(--muted)", marginLeft: 6 }}>
                ({Array.isArray(rows) ? rows.length : 0})
              </span>
            </div>
          ))}
        </>
      ) : null}

      {issues.length > 0 ? (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Technical issues</h4>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table
              className="kw-report-table"
              style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}
            >
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>Priority</th>
                  <th style={{ padding: "6px 8px" }}>Severity</th>
                  <th style={{ padding: "6px 8px" }}>Issue</th>
                  <th style={{ padding: "6px 8px" }}>Category</th>
                  <th style={{ padding: "6px 8px" }}>Confidence</th>
                  <th style={{ padding: "6px 8px" }}>URLs</th>
                </tr>
              </thead>
              <tbody>
                {issues.slice(0, 25).map((row, i) => (
                  <tr key={String(row.rule_id || i)} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px" }}>{String(row.priority ?? "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(row.severity ?? "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(row.title ?? "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(row.category ?? "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(row.confidence ?? "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(row.affected_url_count ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {themes.length > 0 ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Findings by theme</h4>
          {themes.map((t, i) => (
            <div key={`${String(t.theme)}-${i}`} style={{ marginBottom: 12 }}>
              <h4 style={{ margin: "0 0 4px", fontSize: 13 }}>
                {SECTION_LABELS[String(t.theme)] || String(t.theme || "Theme")}
                {t.score != null ? `: ${String(t.score)}/100` : ""}
              </h4>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                {(Array.isArray(t.findings) ? (t.findings as unknown[]) : [])
                  .slice(0, 8)
                  .map((f, j) => (
                    <li key={j}>{typeof f === "string" ? f : String(f)}</li>
                  ))}
              </ul>
            </div>
          ))}
        </>
      ) : (
        Object.entries(sections).map(([key, sec]) => (
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
        ))
      )}

      {broken ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Broken links</h4>
          <div className="cs-funnel-strip">
            <span>
              Total <strong>{String(broken.broken_count ?? payload.broken_link_count ?? 0)}</strong>
            </span>
            <span>
              Internal <strong>{internalBroken.length}</strong>
            </span>
            <span>
              External <strong>{externalBroken.length}</strong>
            </span>
          </div>
          {(internalBroken.length > 0 || externalBroken.length > 0) && (
            <div style={{ overflowX: "auto", marginBottom: 14 }}>
              <table
                className="kw-report-table"
                style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}
              >
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                    <th style={{ padding: "6px 8px" }}>Scope</th>
                    <th style={{ padding: "6px 8px" }}>URL / detail</th>
                  </tr>
                </thead>
                <tbody>
                  {internalBroken.slice(0, 8).map((b, i) => (
                    <tr key={`int-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                      <td style={{ padding: "6px 8px" }}>Internal</td>
                      <td style={{ padding: "6px 8px", fontSize: 11 }}>
                        {typeof b === "string"
                          ? b
                          : String(
                              (b as Record<string, unknown>).url ||
                                (b as Record<string, unknown>).href ||
                                JSON.stringify(b),
                            )}
                      </td>
                    </tr>
                  ))}
                  {externalBroken.slice(0, 8).map((b, i) => (
                    <tr key={`ext-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                      <td style={{ padding: "6px 8px" }}>External</td>
                      <td style={{ padding: "6px 8px", fontSize: 11 }}>
                        {typeof b === "string"
                          ? b
                          : String(
                              (b as Record<string, unknown>).url ||
                                (b as Record<string, unknown>).href ||
                                JSON.stringify(b),
                            )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : null}

      {fixes.length > 0 && (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Priority backlog</h4>
          <ol style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {fixes.slice(0, 20).map((f, i) => (
              <li key={i} style={{ marginBottom: 6 }}>
                <strong>[{f.priority || "Medium"}]</strong> {f.issue}
                {f.fix ? ` — ${f.fix}` : ""}
                {f.source ? (
                  <span style={{ color: "var(--muted)", fontSize: 11 }}> ({f.source})</span>
                ) : null}
              </li>
            ))}
          </ol>
        </>
      )}

      {iaNotes.length > 0 || iaActions.length > 0 ? (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>
            Phase 6 IA handoffs
          </h4>
          {iaNotes.length ? (
            <ul className="missing-list">
              {iaNotes.slice(0, 10).map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          ) : null}
          {iaActions.length ? (
            <ul className="missing-list">
              {iaActions.slice(0, 10).map((a, i) => (
                <li key={i}>
                  {String(a.type || "action")}:{" "}
                  {String(a.issue || a.fix || `${a.from || ""} → ${a.to || ""}`)}
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}

      {notMeasured.length > 0 ? (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Not measured (companion skills)</h4>
          <ul className="missing-list">
            {notMeasured.map((n, i) => (
              <li key={i}>
                {String(n.item || "—")}
                {n.requires ? (
                  <span style={{ color: "var(--muted)", fontSize: 11 }}>
                    {" "}
                    → {String(n.requires)}
                  </span>
                ) : null}
                {n.note ? (
                  <div style={{ fontSize: 11, color: "var(--muted)" }}>{String(n.note)}</div>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {routing.length > 0 ? (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Specialist routing</h4>
          <ul className="missing-list">
            {routing.map((r, i) => (
              <li key={i}>
                {String(r.finding_area || "—")} → <strong>{String(r.route_to || "")}</strong>
                {r.status ? (
                  <span style={{ color: "var(--muted)", fontSize: 11 }}> ({String(r.status)})</span>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {canAct && (
        <div className="card-actions">
          <button type="button" className="btn btn-primary" onClick={() => onAction("approve")}>
            Approve
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => onAction("reject")}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
