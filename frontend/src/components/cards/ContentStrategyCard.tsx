import WorkbookTable from "./WorkbookTable";

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
};

function asRows(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && typeof r === "object") as Array<Record<string, unknown>>;
}

function fmtVol(v: unknown): string {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString();
}

function fmtNum(v: unknown): string {
  if (v == null || v === "") return "—";
  return String(v);
}

function strList(raw: unknown, n = 4): string {
  if (!Array.isArray(raw) || !raw.length) return "";
  return raw
    .slice(0, n)
    .map((x) => (typeof x === "string" ? x : String((x as Record<string, unknown>)?.name || (x as Record<string, unknown>)?.domain || x)))
    .filter(Boolean)
    .join(", ");
}

export default function ContentStrategyCard({ payload, canAct, onAction }: Props) {
  const pillars = asRows(payload.pillars);
  const coreTopics = asRows(payload.core_topics);
  const priorities = asRows(payload.priority_pages);
  const queue = asRows(payload.priority_queue);
  const combinedQueue = asRows(payload.combined_priority_queue);
  const gapA = asRows(payload.competitor_content_gaps);
  const gaps = gapA.length ? gapA : asRows(payload.content_gaps);
  const calendar = asRows(payload.content_calendar);
  const linking = asRows(payload.internal_linking);
  const competitorSites = asRows(payload.competitor_sites);
  const competitorNames = Array.isArray(payload.competitor_names)
    ? (payload.competitor_names as string[])
    : [];
  const competitorDomains = Array.isArray(payload.competitor_domains)
    ? (payload.competitor_domains as string[])
    : [];
  const metrics =
    payload.success_metrics && typeof payload.success_metrics === "object"
      ? (payload.success_metrics as Record<string, unknown>)
      : null;
  const displayQueue = queue.length ? queue : priorities;
  const showCombined = combinedQueue.length > 0 && Boolean(payload.both_skills_present);
  const topicBlocks = coreTopics.length ? coreTopics : pillars;
  const funnelBal =
    payload.funnel_balance && typeof payload.funnel_balance === "object"
      ? (payload.funnel_balance as Record<string, unknown>)
      : null;
  const funnelCounts =
    funnelBal?.counts && typeof funnelBal.counts === "object"
      ? (funnelBal.counts as Record<string, number>)
      : null;
  const funnelWarnings = Array.isArray(funnelBal?.warnings)
    ? (funnelBal!.warnings as string[])
    : [];
  const geo =
    payload.geographic_focus || payload.location_name
      ? [payload.geographic_focus, payload.location_name].filter(Boolean).map(String).join(" · ")
      : "";
  const tofuMofuRows = asRows(payload.tofu_mofu_content_strategy);
  const strategyWorkbook =
    payload.workbook && typeof payload.workbook === "object"
      ? (payload.workbook as Record<string, unknown>)
      : null;
  const tofuMofuColumns = Array.isArray(strategyWorkbook?.columns)
    ? (strategyWorkbook!.columns as string[])
    : [
        "content_type",
        "funnel_stage",
        "parent_category",
        "blog_title",
        "primary_keyword",
        "search_volume_mo",
        "cpc",
        "secondary_keywords",
        "combined_cluster_volume",
        "internal_linking_targets",
        "priority",
        "notes",
      ];

  return (
    <div className="structured-card structured-card--report">
      <h3 className="card-title">{String(payload.title || "Content Strategy")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        {payload.business_name ? `${String(payload.business_name)} · ` : ""}
        {payload.industry ? `${String(payload.industry)} · ` : ""}
        {payload.primary_url ? String(payload.primary_url) : ""}
        {geo ? ` · Geo: ${geo}` : ""}
        {competitorNames.length || competitorDomains.length
          ? ` · Competitors: ${(competitorNames.length ? competitorNames : competitorDomains)
              .slice(0, 5)
              .join(", ")}`
          : ""}
      </p>
      {payload.target_audience ? (
        <p style={{ fontSize: 13, marginTop: 0 }}>
          <strong>Audience:</strong> {String(payload.target_audience)}
        </p>
      ) : null}
      {payload.executive_summary ? (
        <p style={{ fontSize: 14, lineHeight: 1.5 }}>{String(payload.executive_summary)}</p>
      ) : null}
      {payload.calendar_notes ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>{String(payload.calendar_notes)}</p>
      ) : null}

      {funnelCounts ? (
        <div className="cs-funnel-strip">
          <span>
            TOFU <strong>{funnelCounts.TOFU ?? 0}</strong>
          </span>
          <span>
            MOFU <strong>{funnelCounts.MOFU ?? 0}</strong>
          </span>
          <span>
            BOFU <strong>{funnelCounts.BOFU ?? 0}</strong>
          </span>
          {funnelBal?.balanced ? <span className="cs-badge">Balanced</span> : null}
        </div>
      ) : null}
      {funnelWarnings.length ? (
        <ul className="missing-list">
          {funnelWarnings.slice(0, 5).map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}

      <WorkbookTable
        title="TOFU & MOFU Content Strategy (workbook)"
        columns={tofuMofuColumns}
        rows={tofuMofuRows}
        maxRows={20}
      />

      <h4 style={{ marginBottom: 8 }}>Core topics & clusters</h4>
      {topicBlocks.length ? (
        topicBlocks.map((p, i) => {
          const clusters = asRows(p.clusters);
          const supporting = Array.isArray(p.supporting_keywords)
            ? (p.supporting_keywords as string[])
            : [];
          return (
            <div key={`${String(p.pillar || p.name)}-${i}`} className="cs-pillar-block">
              <div className="cs-pillar-head">
                <strong>{String(p.pillar || p.name || p.primary_keyword || "Pillar")}</strong>
                {p.primary_keyword ? (
                  <span className="cs-meta">Primary: {String(p.primary_keyword)}</span>
                ) : null}
                {p.intent ? <span className="cs-chip">{String(p.intent)}</span> : null}
                {p.est_words != null ? (
                  <span className="cs-meta">~{String(p.est_words)} words</span>
                ) : null}
                {p.opportunity_score != null ? (
                  <span className="cs-meta">Score {String(p.opportunity_score)}</span>
                ) : null}
              </div>
              {supporting.length ? (
                <p className="cs-supporting">Supporting: {supporting.slice(0, 8).join(" · ")}</p>
              ) : null}
              {clusters.length ? (
                <div style={{ overflowX: "auto", marginTop: 6 }}>
                  <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                    <thead>
                      <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                        <th style={{ padding: "4px 6px" }}>Cluster / page</th>
                        <th style={{ padding: "4px 6px" }}>Keyword</th>
                        <th style={{ padding: "4px 6px" }}>Intent</th>
                        <th style={{ padding: "4px 6px" }}>Words</th>
                        <th style={{ padding: "4px 6px" }}>Supporting</th>
                      </tr>
                    </thead>
                    <tbody>
                      {clusters.map((c, j) => {
                        const kids = asRows(c.supporting);
                        return (
                          <tr key={`${String(c.name)}-${j}`} style={{ borderBottom: "1px solid var(--line)" }}>
                            <td style={{ padding: "6px 8px", fontWeight: 600 }}>
                              {String(c.name || c.primary_keyword || "—")}
                            </td>
                            <td style={{ padding: "6px 8px" }}>{String(c.primary_keyword || "—")}</td>
                            <td style={{ padding: "6px 8px" }}>{String(c.intent || "—")}</td>
                            <td style={{ padding: "6px 8px" }}>{fmtNum(c.est_words)}</td>
                            <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                              {kids.length
                                ? kids
                                    .slice(0, 4)
                                    .map((k) => String(k.name || k.primary_keyword || k.keyword || ""))
                                    .filter(Boolean)
                                    .join(" · ") || "—"
                                : "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </div>
          );
        })
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>No pillars yet</p>
      )}

      {payload.audit_note ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>{String(payload.audit_note)}</p>
      ) : null}

      {showCombined ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Combined priority queue (audit + strategy)</h4>
          <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
            {String(payload.routing_rule || "")}
            {payload.note && payload.both_skills_present ? ` · ${String(payload.note)}` : ""}
          </p>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table
              className="kw-report-table"
              style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}
            >
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>#</th>
                  <th style={{ padding: "6px 8px" }}>Action</th>
                  <th style={{ padding: "6px 8px" }}>Title</th>
                  <th style={{ padding: "6px 8px" }}>Keyword</th>
                  <th style={{ padding: "6px 8px" }}>Source</th>
                  <th style={{ padding: "6px 8px" }}>Priority</th>
                </tr>
              </thead>
              <tbody>
                {combinedQueue.slice(0, 30).map((p, i) => (
                  <tr key={`cq-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px" }}>{String(p.rank ?? i + 1)}</td>
                    <td style={{ padding: "6px 8px" }}>
                      <span className="cs-chip">{String(p.action || "—")}</span>
                    </td>
                    <td style={{ padding: "6px 8px", fontWeight: 600 }}>
                      {String(p.title || "—")}
                      {p.note ? (
                        <div style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400 }}>
                          {String(p.note).slice(0, 100)}
                        </div>
                      ) : null}
                    </td>
                    <td style={{ padding: "6px 8px" }}>{String(p.keyword || "—")}</td>
                    <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(p.source || "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(p.priority || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <h4 style={{ marginBottom: 8 }}>
        {showCombined ? "Strategy-only queue (gaps / new)" : "Priority content queue"}
      </h4>
      {displayQueue.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>#</th>
                <th style={{ padding: "6px 8px" }}>Title</th>
                <th style={{ padding: "6px 8px" }}>Keyword</th>
                <th style={{ padding: "6px 8px" }}>Vol</th>
                <th style={{ padding: "6px 8px" }}>KD</th>
                <th style={{ padding: "6px 8px" }}>Score</th>
                <th style={{ padding: "6px 8px" }}>Intent</th>
                <th style={{ padding: "6px 8px" }}>Funnel</th>
                <th style={{ padding: "6px 8px" }}>Type</th>
                <th style={{ padding: "6px 8px" }}>Priority</th>
                <th style={{ padding: "6px 8px" }}>Words</th>
                <th style={{ padding: "6px 8px", minWidth: 140 }}>Beat competitors</th>
              </tr>
            </thead>
            <tbody>
              {displayQueue.slice(0, 25).map((p, i) => {
                const comps =
                  strList(p.beat_competitors, 3) || strList(p.competitor_domains, 3);
                return (
                  <tr key={`${String(p.keyword)}-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px", verticalAlign: "top" }}>{i + 1}</td>
                    <td style={{ padding: "6px 8px", verticalAlign: "top", fontWeight: 600 }}>
                      {String(p.title || p.keyword || "—")}
                      {p.rationale ? (
                        <div style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400, marginTop: 2 }}>
                          {String(p.rationale).slice(0, 120)}
                        </div>
                      ) : null}
                      {p.suggested_url ? (
                        <div style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400 }}>
                          {String(p.suggested_url)}
                        </div>
                      ) : null}
                      {Array.isArray(p.image_suggestions) && p.image_suggestions.length ? (
                        <div style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400, marginTop: 2 }}>
                          Images:{" "}
                          {(p.image_suggestions as Array<{ role?: string; prompt?: string }>)
                            .slice(0, 2)
                            .map((s) => s.role || "figure")
                            .join(" · ")}
                        </div>
                      ) : null}
                    </td>
                    <td style={{ padding: "6px 8px", verticalAlign: "top" }}>{String(p.keyword || "—")}</td>
                    <td style={{ padding: "6px 8px", verticalAlign: "top", whiteSpace: "nowrap" }}>
                      {fmtVol(p.volume)}
                    </td>
                    <td style={{ padding: "6px 8px", verticalAlign: "top" }}>{fmtNum(p.difficulty)}</td>
                    <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                      {p.opportunity_score != null ? `${p.opportunity_score}%` : "—"}
                    </td>
                    <td style={{ padding: "6px 8px", verticalAlign: "top" }}>{String(p.intent || "—")}</td>
                    <td style={{ padding: "6px 8px", verticalAlign: "top" }}>{String(p.funnel || "—")}</td>
                    <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                      {String(p.content_type || "—")}
                    </td>
                    <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                      {String(p.priority || p.priority_label || p.bucket || "—")}
                    </td>
                    <td style={{ padding: "6px 8px", verticalAlign: "top" }}>{fmtNum(p.est_words)}</td>
                    <td style={{ padding: "6px 8px", verticalAlign: "top", fontSize: 11 }}>
                      {comps || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>No prioritized pages yet</p>
      )}

      {calendar.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Content calendar (12 weeks)</h4>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>Week</th>
                  <th style={{ padding: "6px 8px" }}>Month</th>
                  <th style={{ padding: "6px 8px" }}>Title</th>
                  <th style={{ padding: "6px 8px" }}>Keyword</th>
                  <th style={{ padding: "6px 8px" }}>Type</th>
                  <th style={{ padding: "6px 8px" }}>Priority</th>
                  <th style={{ padding: "6px 8px" }}>Words</th>
                  <th style={{ padding: "6px 8px" }}>URL</th>
                </tr>
              </thead>
              <tbody>
                {calendar.flatMap((m) => {
                  const weeks = asRows(m.weeks);
                  const label = String(m.label || `Month ${m.month}`);
                  return weeks.map((w, wi) => (
                    <tr
                      key={`m${String(m.month)}-w${String(w.week)}-${wi}`}
                      style={{ borderBottom: "1px solid var(--line)" }}
                    >
                      <td style={{ padding: "6px 8px" }}>{String(w.week)}</td>
                      <td style={{ padding: "6px 8px" }}>
                        {String(m.month)} · {label}
                      </td>
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>
                        {String(w.title || w.keyword || "—")}
                      </td>
                      <td style={{ padding: "6px 8px" }}>{String(w.keyword || "—")}</td>
                      <td style={{ padding: "6px 8px" }}>{String(w.content_type || "—")}</td>
                      <td style={{ padding: "6px 8px" }}>{String(w.priority || "—")}</td>
                      <td style={{ padding: "6px 8px" }}>{fmtNum(w.est_words)}</td>
                      <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                        {String(w.suggested_url || "—")}
                      </td>
                    </tr>
                  ));
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <h4 style={{ marginBottom: 8 }}>Content gaps vs competitors</h4>
      {gaps.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Keyword</th>
                <th style={{ padding: "6px 8px" }}>Vol</th>
                <th style={{ padding: "6px 8px" }}>KD</th>
                <th style={{ padding: "6px 8px" }}>Action</th>
                <th style={{ padding: "6px 8px" }}>Competitors</th>
                <th style={{ padding: "6px 8px", minWidth: 180 }}>Competitor blog examples</th>
              </tr>
            </thead>
            <tbody>
              {gaps.slice(0, 15).map((g, i) => (
                <tr key={`${String(g.keyword)}-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(g.keyword || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>{fmtVol(g.volume)}</td>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(g.difficulty)}</td>
                  <td style={{ padding: "6px 8px" }}>{String(g.action || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>
                    {strList(g.competitors, 4) || "—"}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                    {Array.isArray(g.competitor_blog_examples)
                      ? (g.competitor_blog_examples as string[]).slice(0, 3).join(" · ") || "—"
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>—</p>
      )}

      {competitorSites.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Competitor site snapshots</h4>
          {competitorSites.slice(0, 5).map((s, i) => {
            const blogs = asRows(s.blogs);
            const pages = blogs.length ? blogs : asRows(s.pages);
            return (
              <div key={`${String(s.domain || s.name)}-${i}`} className="cs-pillar-block">
                <strong>
                  {String(s.name || s.domain || "Competitor")}
                  {s.domain ? ` · ${String(s.domain)}` : ""}
                </strong>
                {pages.length ? (
                  <ul className="missing-list" style={{ marginTop: 4 }}>
                    {pages.slice(0, 5).map((b, j) => (
                      <li key={`${String(b.title || b.url)}-${j}`}>
                        {String(b.title || b.url || "—")}
                        {b.url && b.title ? (
                          <span style={{ color: "var(--muted)" }}> — {String(b.url)}</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p style={{ fontSize: 12, color: "var(--muted)", margin: "4px 0 0" }}>No pages crawled</p>
                )}
              </div>
            );
          })}
        </>
      ) : null}

      {linking.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Internal linking</h4>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>From</th>
                  <th style={{ padding: "6px 8px" }}>To</th>
                  <th style={{ padding: "6px 8px" }}>Note</th>
                </tr>
              </thead>
              <tbody>
                {linking.slice(0, 20).map((l, i) => (
                  <tr key={`${String(l.from)}-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(l.from || "—")}</td>
                    <td style={{ padding: "6px 8px", fontSize: 11 }}>
                      {Array.isArray(l.to) ? (l.to as string[]).slice(0, 5).join(", ") : "—"}
                    </td>
                    <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                      {String(l.note || "—")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {metrics ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Success metrics</h4>
          <ul className="missing-list">
            {metrics.organic_traffic_target ? (
              <li>
                <strong>Traffic target:</strong> {String(metrics.organic_traffic_target)}
              </li>
            ) : null}
            {metrics.ranking_target ? (
              <li>
                <strong>Ranking target:</strong> {String(metrics.ranking_target)}
              </li>
            ) : null}
            {metrics.content_velocity ? (
              <li>
                <strong>Velocity:</strong> {String(metrics.content_velocity)}
              </li>
            ) : null}
            {Array.isArray(metrics.keywords_to_track) && metrics.keywords_to_track.length ? (
              <li>
                <strong>Track:</strong> {(metrics.keywords_to_track as string[]).slice(0, 12).join(", ")}
              </li>
            ) : null}
            {Array.isArray(metrics.milestones) && metrics.milestones.length
              ? (metrics.milestones as unknown[]).slice(0, 5).map((m, i) => (
                  <li key={i}>{typeof m === "string" ? m : JSON.stringify(m)}</li>
                ))
              : null}
          </ul>
        </>
      ) : null}

      {canAct && (
        <div className="card-actions">
          <button type="button" className="btn btn-primary" onClick={() => onAction("approve")}>
            Approve strategy
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => onAction("reject")}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
