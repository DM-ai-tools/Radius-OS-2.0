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

function fmtNum(v: unknown): string {
  if (v == null || v === "") return "—";
  return String(v);
}

function fmtVol(v: unknown): string {
  if (v == null || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString() : String(v);
}

function fmtCpc(v: unknown): string {
  if (v == null || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : String(v);
}

function fmtKwList(raw: unknown, n = 10): string {
  if (!Array.isArray(raw) || !raw.length) return "—";
  return raw.slice(0, n).map(String).filter(Boolean).join(", ") || "—";
}

/** Status / Priority / In Scope? — same small colored-chip treatment. */
function StatusChip({ value }: { value: unknown }) {
  const v = String(value || "").trim();
  if (!v) return <span>—</span>;
  const lower = v.toLowerCase();
  const tone =
    lower.includes("create") || lower === "high"
      ? "coral"
      : lower.includes("review") || lower === "medium"
        ? "amber"
        : "teal";
  const colors: Record<string, { bg: string; fg: string }> = {
    coral: { bg: "rgba(153, 60, 29, 0.14)", fg: "#993c1d" },
    amber: { bg: "rgba(133, 79, 11, 0.14)", fg: "#854f0b" },
    teal: { bg: "rgba(15, 118, 110, 0.14)", fg: "#0f766e" },
  };
  const c = colors[tone];
  return (
    <span
      style={{
        display: "inline-block",
        fontSize: 10,
        fontWeight: 700,
        padding: "2px 7px",
        borderRadius: 4,
        whiteSpace: "nowrap",
        background: c.bg,
        color: c.fg,
      }}
    >
      {v}
    </span>
  );
}

function resolveUrlColumns(row: Record<string, unknown>): { current: string | null; proposed: string | null } {
  const cur = row.current_url ? String(row.current_url).trim() : "";
  const prop = row.proposed_url ? String(row.proposed_url).trim() : "";
  if (cur && cur !== "/") return { current: cur, proposed: null };
  if (prop && prop !== "/") return { current: null, proposed: prop };
  const action = String(row.action || row.status || "").toUpperCase();
  const sel = row.selected_url ? String(row.selected_url).trim() : "";
  const create = row.create_url ? String(row.create_url).trim() : "";
  if (action.includes("CREATE") || action === "NEW") {
    return { current: null, proposed: create || sel || null };
  }
  return { current: sel || null, proposed: null };
}

function normalizeUrlMapRow(row: Record<string, unknown>): Record<string, unknown> {
  const { current, proposed } = resolveUrlColumns(row);
  return {
    ...row,
    current_url: current,
    proposed_url: proposed,
    l3_subsubcategory: row.l3_subsubcategory ?? row.l3_sub_subcategory,
    search_volume: row.search_volume ?? row.search_volume_mo ?? row.volume,
    secondary_keywords_sheet:
      row.secondary_keywords_sheet ?? row.secondary_keywords,
  };
}

function collectUrlMapRows(payload: Record<string, unknown>): Array<Record<string, unknown>> {
  const report =
    payload.url_map_report && typeof payload.url_map_report === "object"
      ? (payload.url_map_report as Record<string, unknown>)
      : {};
  const fromReport = asRows(report.final_url_map);
  const fromTop = asRows(payload.final_url_map);
  const fromCategory = asRows(payload.category_url_mapping).filter((r) => r.row_type !== "section");
  const raw = fromReport.length ? fromReport : fromTop.length ? fromTop : fromCategory;
  return raw.map(normalizeUrlMapRow);
}

function strList(raw: unknown, n = 4): string {
  if (!Array.isArray(raw) || !raw.length) return "";
  return raw
    .slice(0, n)
    .map((x) =>
      typeof x === "string"
        ? x
        : String(
            (x as Record<string, unknown>)?.name ||
              (x as Record<string, unknown>)?.domain ||
              (x as Record<string, unknown>)?.url ||
              x,
          ),
    )
    .filter(Boolean)
    .join(", ");
}

export default function SiteArchitectureCard({ payload, canAct, onAction }: Props) {
  const current =
    payload.current_state && typeof payload.current_state === "object"
      ? (payload.current_state as Record<string, unknown>)
      : {};
  const issues = asRows(current.issues);
  const depth4 = asRows(current.pages_depth_4_plus);
  const orphanSample = asRows(current.orphan_sample);
  const phantomRaw = Array.isArray(current.phantom_sample) ? current.phantom_sample : [];
  const phantomSample = phantomRaw.map((p) =>
    typeof p === "string"
      ? { path: p }
      : p && typeof p === "object"
        ? (p as Record<string, unknown>)
        : { path: String(p) },
  );
  const evidenceNotes = Array.isArray(current.evidence_notes)
    ? (current.evidence_notes as string[])
    : [];
  const depthDist =
    current.click_depth_distribution && typeof current.click_depth_distribution === "object"
      ? (current.click_depth_distribution as Record<string, number>)
      : null;

  const urlMap = collectUrlMapRows(payload);
  const categoryMapping = asRows(payload.category_url_mapping);
  const categoryWorkbook =
    payload.workbook && typeof payload.workbook === "object"
      ? (payload.workbook as Record<string, unknown>)
      : null;
  const categoryColumns = Array.isArray(categoryWorkbook?.columns)
    ? (categoryWorkbook!.columns as string[])
    : [
        "level",
        "l1_category",
        "l2_subcategory",
        "l3_sub_subcategory",
        "l4_attribution",
        "current_url",
        "proposed_url",
        "status",
        "primary_keyword",
        "search_volume_mo",
        "cpc",
        "secondary_keywords",
        "combined_cluster_volume",
        "page_type",
        "priority",
        "notes",
        "est_products",
        "in_scope",
      ];
  const tree = asRows(payload.target_url_tree);
  const ownership = asRows(payload.cluster_ownership);
  const nav =
    payload.navigation && typeof payload.navigation === "object"
      ? (payload.navigation as Record<string, unknown>)
      : {};
  const primaryNav = asRows(nav.primary_nav);
  const remediation = asRows(nav.depth_remediation);
  const breadcrumbs =
    nav.breadcrumb_pattern && typeof nav.breadcrumb_pattern === "object"
      ? (nav.breadcrumb_pattern as Record<string, unknown>)
      : null;
  const types = asRows(payload.page_type_model);
  const redirects = asRows(payload.redirect_map);
  const handoffs = asRows(payload.handoffs);
  const competitorSites = asRows(payload.competitor_sites);
  const competitorIa = asRows(payload.competitor_ia);
  const moneyPages = Array.isArray(payload.money_pages)
    ? (payload.money_pages as string[])
    : [];
  const competitorNames = Array.isArray(payload.competitor_names)
    ? (payload.competitor_names as string[])
    : [];
  const competitorDomains = Array.isArray(payload.competitor_domains)
    ? (payload.competitor_domains as string[])
    : [];
  const urlRules =
    payload.url_convention_rules && typeof payload.url_convention_rules === "object"
      ? (payload.url_convention_rules as Record<string, unknown>)
      : null;
  const rollout =
    payload.rollout_plan && typeof payload.rollout_plan === "object"
      ? (payload.rollout_plan as Record<string, unknown>)
      : null;
  const rolloutSeq = Array.isArray(rollout?.sequence) ? (rollout!.sequence as string[]) : [];
  const riskFlags = Array.isArray(rollout?.high_risk_flags)
    ? (rollout!.high_risk_flags as string[])
    : [];
  const geo =
    payload.geographic_focus || payload.location_name
      ? [payload.geographic_focus, payload.location_name].filter(Boolean).map(String).join(" · ")
      : "";

  const depthDistBits = depthDist
    ? Object.entries(depthDist)
        .sort(([a], [b]) => Number(a) - Number(b))
        .map(([d, c]) => `d${d}: ${c}`)
        .join(" · ")
    : "";

  return (
    <div className="structured-card structured-card--report">
      <h3 className="card-title">{String(payload.title || "Site Architecture Blueprint")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        {payload.client ? `${String(payload.client)} · ` : ""}
        {payload.primary_url || payload.domain ? String(payload.primary_url || payload.domain) : ""}
        {geo ? ` · Geo: ${geo}` : ""}
        {competitorNames.length || competitorDomains.length
          ? ` · Competitors: ${(competitorNames.length ? competitorNames : competitorDomains)
              .slice(0, 5)
              .join(", ")}`
          : ""}
        {current.method ? ` · Crawl: ${String(current.method)}` : ""}
      </p>
      {payload.executive_summary ? (
        <p style={{ fontSize: 14, lineHeight: 1.5 }}>{String(payload.executive_summary)}</p>
      ) : null}
      {payload.competitor_ia_notes ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>{String(payload.competitor_ia_notes)}</p>
      ) : null}

      <WorkbookTable
        title="Category & URL Mapping (workbook)"
        columns={categoryColumns}
        rows={(categoryMapping.length ? categoryMapping : urlMap).map(normalizeUrlMapRow)}
        maxRows={25}
      />

      <div className="cs-funnel-strip">
        <span>
          URLs <strong>{fmtNum(current.urls_crawled)}</strong>
        </span>
        <span>
          200s <strong>{fmtNum(current.status_200)}</strong>
        </span>
        <span>
          Max depth <strong>{fmtNum(current.max_click_depth)}</strong>
        </span>
        <span>
          ≤3 clicks <strong>{fmtNum(current.within_3_clicks)}</strong>
        </span>
        <span>
          Depth 4+ <strong>{fmtNum(current.depth_4_plus)}</strong>
        </span>
        <span>
          Orphans <strong>{fmtNum(current.orphans)}</strong>
        </span>
        <span>
          Phantoms <strong>{fmtNum(current.phantom_dirs)}</strong>
        </span>
        {payload.gate ? <span className="cs-badge">Gate</span> : null}
      </div>
      {depthDistBits ? (
        <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
          Depth distribution: {depthDistBits}
        </p>
      ) : null}
      {payload.gate ? (
        <p style={{ fontSize: 12, fontWeight: 600, marginTop: 4 }}>{String(payload.gate)}</p>
      ) : null}

      <h4 style={{ marginBottom: 8 }}>Current-state issues</h4>
      {issues.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Issue</th>
                <th style={{ padding: "6px 8px" }}>Count</th>
                <th style={{ padding: "6px 8px" }}>Severity</th>
                <th style={{ padding: "6px 8px" }}>Note</th>
              </tr>
            </thead>
            <tbody>
              {issues.map((i, idx) => (
                <tr key={idx} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(i.issue || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(i.count)}</td>
                  <td style={{ padding: "6px 8px" }}>
                    <span className="cs-chip">{String(i.severity || "—")}</span>
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                    {String(i.note || "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>No structural issues flagged</p>
      )}

      {depth4.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Pages beyond 3 clicks</h4>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>URL</th>
                  <th style={{ padding: "6px 8px" }}>Title</th>
                  <th style={{ padding: "6px 8px" }}>Depth</th>
                </tr>
              </thead>
              <tbody>
                {depth4.slice(0, 15).map((p, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(p.url || p.path || "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(p.title || "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{fmtNum(p.depth)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {orphanSample.length || phantomSample.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Orphans & phantom directories</h4>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>Kind</th>
                  <th style={{ padding: "6px 8px" }}>URL / path</th>
                  <th style={{ padding: "6px 8px" }}>Title / note</th>
                </tr>
              </thead>
              <tbody>
                {orphanSample.slice(0, 10).map((p, i) => (
                  <tr key={`o-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px" }}>
                      <span className="cs-chip">orphan</span>
                    </td>
                    <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(p.url || "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(p.title || "—")}</td>
                  </tr>
                ))}
                {phantomSample.slice(0, 10).map((p, i) => (
                  <tr key={`ph-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px" }}>
                      <span className="cs-chip">phantom</span>
                    </td>
                    <td style={{ padding: "6px 8px", fontSize: 11 }}>
                      {String(p.path || p.url || "—")}
                    </td>
                    <td style={{ padding: "6px 8px", color: "var(--muted)" }}>
                      {p.note ? String(p.note) : "Missing hub at path"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {evidenceNotes.length ? (
        <ul className="missing-list" style={{ marginBottom: 14 }}>
          {evidenceNotes.slice(0, 6).map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      ) : null}

      <h4 style={{ marginBottom: 8 }}>Page-type model</h4>
      {types.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Type</th>
                <th style={{ padding: "6px 8px" }}>URL pattern</th>
                <th style={{ padding: "6px 8px" }}>Parent</th>
                <th style={{ padding: "6px 8px" }}>Breadcrumb</th>
                <th style={{ padding: "6px 8px" }}>Indexable</th>
                <th style={{ padding: "6px 8px" }}>Count</th>
              </tr>
            </thead>
            <tbody>
              {types.map((t, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(t.type || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(t.url_pattern || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>{String(t.parent || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(t.breadcrumb || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>
                    {t.indexable === true ? "Yes" : t.indexable === false ? "No" : "—"}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(t.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>—</p>
      )}

      {moneyPages.length ? (
        <p style={{ fontSize: 13, marginTop: 0 }}>
          <strong>Money pages:</strong> {moneyPages.slice(0, 10).join(" · ")}
        </p>
      ) : null}

      <h4 style={{ marginBottom: 8 }}>Target URL tree</h4>
      {tree.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Type</th>
                <th style={{ padding: "6px 8px" }}>Path</th>
                <th style={{ padding: "6px 8px" }}>Depth</th>
                <th style={{ padding: "6px 8px" }}>Keyword</th>
                <th style={{ padding: "6px 8px" }}>Cluster</th>
                <th style={{ padding: "6px 8px" }}>Parent</th>
                <th style={{ padding: "6px 8px" }}>Absolute URL</th>
              </tr>
            </thead>
            <tbody>
              {tree.slice(0, 40).map((n, i) => (
                <tr key={`${String(n.path)}-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px" }}>
                    <span className="cs-chip">{String(n.type || "page")}</span>
                  </td>
                  <td style={{ padding: "6px 8px", fontWeight: 600, fontSize: 11 }}>
                    {String(n.path || n.url || "—")}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(n.depth)}</td>
                  <td style={{ padding: "6px 8px" }}>
                    {String(n.primary_keyword || n.keyword || "—")}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(n.cluster || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                    {String(n.parent || "—")}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                    {String(n.absolute_url || "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {tree.length > 40 ? (
            <p style={{ fontSize: 11, color: "var(--muted)" }}>+{tree.length - 40} more URLs in payload</p>
          ) : null}
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>—</p>
      )}

      <h4 style={{ marginBottom: 8 }}>URL mapping &amp; taxonomy plan</h4>
      {urlMap.length ? (
        <>
          <h5 style={{ margin: "0 0 8px", fontSize: 12, fontWeight: 700 }}>Taxonomy &amp; status</h5>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "5px 7px" }}>Level</th>
                  <th style={{ padding: "5px 7px" }}>L1 Category</th>
                  <th style={{ padding: "5px 7px" }}>L2 Subcategory</th>
                  <th style={{ padding: "5px 7px" }}>L3 Sub-Subcategory</th>
                  <th style={{ padding: "5px 7px" }}>L4 Attribution</th>
                  <th style={{ padding: "5px 7px", minWidth: 140 }}>Current URL</th>
                  <th style={{ padding: "5px 7px", minWidth: 140 }}>Proposed URL (if new)</th>
                  <th style={{ padding: "5px 7px" }}>Status</th>
                  <th style={{ padding: "5px 7px" }}>Page Type</th>
                  <th style={{ padding: "5px 7px" }}>Priority</th>
                </tr>
              </thead>
              <tbody>
                {urlMap.slice(0, 60).map((r, i) => (
                  <tr
                    key={`tax-${String(r.cluster || r.primary_keyword)}-${i}`}
                    style={{ borderBottom: "1px solid var(--line)" }}
                  >
                    <td style={{ padding: "5px 7px" }}>{fmtNum(r.level)}</td>
                    <td style={{ padding: "5px 7px" }}>{String(r.l1_category || "—")}</td>
                    <td style={{ padding: "5px 7px" }}>{String(r.l2_subcategory || "—")}</td>
                    <td style={{ padding: "5px 7px" }}>{String(r.l3_subsubcategory || "—")}</td>
                    <td style={{ padding: "5px 7px" }}>{String(r.l4_attribution || "—")}</td>
                    <td style={{ padding: "5px 7px", fontSize: 10, color: "var(--muted)" }}>
                      {String(r.current_url || "—")}
                    </td>
                    <td style={{ padding: "5px 7px", fontSize: 10, fontWeight: 600 }}>
                      {String(r.proposed_url || "—")}
                    </td>
                    <td style={{ padding: "5px 7px" }}>
                      <StatusChip value={r.status || r.action} />
                    </td>
                    <td style={{ padding: "5px 7px" }}>{String(r.page_type || "—")}</td>
                    <td style={{ padding: "5px 7px" }}>
                      <StatusChip value={r.priority || r.score_band} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h5 style={{ margin: "0 0 8px", fontSize: 12, fontWeight: 700 }}>Keywords &amp; opportunity</h5>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "5px 7px" }}>Primary Keyword</th>
                  <th style={{ padding: "5px 7px" }}>Search Volume (mo)</th>
                  <th style={{ padding: "5px 7px" }}>CPC ($)</th>
                  <th style={{ padding: "5px 7px", minWidth: 180 }}>Secondary Keywords (5-10)</th>
                  <th style={{ padding: "5px 7px" }}>Combined Cluster Volume</th>
                  <th style={{ padding: "5px 7px" }}>Est. Products</th>
                  <th style={{ padding: "5px 7px" }}>In Scope?</th>
                  <th style={{ padding: "5px 7px", minWidth: 160 }}>Notes</th>
                </tr>
              </thead>
              <tbody>
                {urlMap.slice(0, 60).map((r, i) => (
                  <tr
                    key={`kw-${String(r.cluster || r.primary_keyword)}-${i}`}
                    style={{ borderBottom: "1px solid var(--line)" }}
                  >
                    <td style={{ padding: "5px 7px", fontWeight: 600 }}>
                      {String(r.primary_keyword || "—")}
                    </td>
                    <td style={{ padding: "5px 7px" }}>{fmtVol(r.search_volume)}</td>
                    <td style={{ padding: "5px 7px" }}>{fmtCpc(r.cpc)}</td>
                    <td style={{ padding: "5px 7px", fontSize: 10 }}>
                      {fmtKwList(r.secondary_keywords_sheet)}
                    </td>
                    <td style={{ padding: "5px 7px" }}>{fmtVol(r.combined_cluster_volume)}</td>
                    <td style={{ padding: "5px 7px" }}>{fmtNum(r.est_products)}</td>
                    <td style={{ padding: "5px 7px" }}>
                      <StatusChip value={r.in_scope} />
                    </td>
                    <td style={{ padding: "5px 7px", fontSize: 10, color: "var(--muted)" }}>
                      {String(r.notes || "—")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {urlMap.length > 60 ? (
            <p style={{ fontSize: 11, color: "var(--muted)" }}>+{urlMap.length - 60} more rows in payload</p>
          ) : null}
        </>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>
          No URL mapping rows yet — run Site Architecture after Search Demand and Content Strategy.
        </p>
      )}

      <h4 style={{ marginBottom: 8 }}>Primary navigation</h4>
      {primaryNav.length ? (
        <div style={{ overflowX: "auto", marginBottom: 10 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Label</th>
                <th style={{ padding: "6px 8px" }}>URL</th>
                <th style={{ padding: "6px 8px" }}>Depth</th>
              </tr>
            </thead>
            <tbody>
              {primaryNav.map((n, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(n.label || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(n.url || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(n.depth)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>—</p>
      )}
      {nav.rule ? (
        <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>{String(nav.rule)}</p>
      ) : null}
      {nav.notes ? (
        <p style={{ fontSize: 13, marginTop: 4 }}>{String(nav.notes)}</p>
      ) : null}
      {breadcrumbs ? (
        <p style={{ fontSize: 12, marginTop: 4 }}>
          <strong>Breadcrumbs:</strong>{" "}
          {Object.entries(breadcrumbs)
            .map(([k, v]) => `${k}: ${String(v)}`)
            .join(" · ")}
        </p>
      ) : null}

      {remediation.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Depth remediation</h4>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>URL</th>
                  <th style={{ padding: "6px 8px" }}>Current</th>
                  <th style={{ padding: "6px 8px" }}>Target</th>
                  <th style={{ padding: "6px 8px" }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {remediation.slice(0, 20).map((r, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(r.url || "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{fmtNum(r.current_depth)}</td>
                    <td style={{ padding: "6px 8px" }}>{fmtNum(r.target_depth)}</td>
                    <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(r.action || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <h4 style={{ marginBottom: 8 }}>Cluster ownership</h4>
      {ownership.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Cluster</th>
                <th style={{ padding: "6px 8px" }}>Canonical owner</th>
                <th style={{ padding: "6px 8px" }}>Disposition</th>
                <th style={{ padding: "6px 8px" }}>Competing URLs</th>
                <th style={{ padding: "6px 8px" }}>Note</th>
              </tr>
            </thead>
            <tbody>
              {ownership.slice(0, 25).map((o, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(o.cluster || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>
                    {String(o.canonical_owner_url || o.owner_url || o.url || "—")}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{String(o.disposition || "keep")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>
                    {strList(o.competing_urls, 3) || "—"}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                    {String(o.note || "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>—</p>
      )}

      <h4 style={{ marginBottom: 8 }}>Redirect map</h4>
      {redirects.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Old URL</th>
                <th style={{ padding: "6px 8px" }}>New URL</th>
                <th style={{ padding: "6px 8px" }}>Code</th>
                <th style={{ padding: "6px 8px" }}>Note</th>
              </tr>
            </thead>
            <tbody>
              {redirects.slice(0, 20).map((r, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(r.old_url || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(r.new_url || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(r.code || 301)}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                    {String(r.note || "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 14 }}>
          No URL moves proposed — prefer nav/hub fixes over restructures (Google doesn&apos;t count
          slashes).
        </p>
      )}

      {urlRules ? (
        <>
          <h4 style={{ marginBottom: 8 }}>URL convention rules</h4>
          <ul className="missing-list" style={{ marginBottom: 14 }}>
            {Object.entries(urlRules)
              .filter(([k, v]) => k !== "references" && typeof v === "string")
              .map(([k, v]) => (
                <li key={k}>
                  <strong>{k.replace(/_/g, " ")}:</strong> {String(v)}
                </li>
              ))}
          </ul>
        </>
      ) : null}

      {(competitorIa.length || competitorSites.length) ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Competitor IA / site snapshots</h4>
          {(competitorIa.length ? competitorIa : competitorSites).slice(0, 6).map((s, i) => {
            const hubs = Array.isArray(s.hub_paths) ? (s.hub_paths as string[]) : [];
            const examples = asRows(s.blog_examples);
            const blogs = examples.length ? examples : asRows(s.blogs);
            const pages = blogs.length ? blogs : asRows(s.pages);
            return (
              <div key={i} className="cs-pillar-block">
                <strong>
                  {String(s.name || s.domain || "Competitor")}
                  {s.domain ? ` · ${String(s.domain)}` : ""}
                </strong>
                <p className="cs-meta" style={{ margin: "4px 0" }}>
                  {s.page_count != null ? `Pages: ${String(s.page_count)}` : ""}
                  {s.blog_count != null ? ` · Blogs: ${String(s.blog_count)}` : ""}
                  {hubs.length ? ` · Hubs: ${hubs.slice(0, 6).join(", ")}` : ""}
                </p>
                {pages.length ? (
                  <ul className="missing-list" style={{ marginTop: 4 }}>
                    {pages.slice(0, 4).map((b, j) => (
                      <li key={j}>
                        {String(b.title || b.path || b.url || "—")}
                        {(b.path || b.url) && b.title ? (
                          <span style={{ color: "var(--muted)" }}>
                            {" "}
                            — {String(b.path || b.url)}
                          </span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            );
          })}
        </>
      ) : null}

      {rollout ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Rollout plan</h4>
          {rolloutSeq.length ? (
            <ol className="missing-list" style={{ marginBottom: 8, paddingLeft: 18 }}>
              {rolloutSeq.map((step, i) => (
                <li key={i} style={{ listStyle: "decimal", marginBottom: 4 }}>
                  {step}
                </li>
              ))}
            </ol>
          ) : null}
          <ul className="missing-list">
            {rollout.freeze_window ? (
              <li>
                <strong>Freeze:</strong> {String(rollout.freeze_window)}
              </li>
            ) : null}
            {rollout.rollback_trigger ? (
              <li>
                <strong>Rollback:</strong> {String(rollout.rollback_trigger)}
              </li>
            ) : null}
            {riskFlags.length ? (
              <li>
                <strong>Risk flags:</strong> {riskFlags.join(" · ")}
              </li>
            ) : null}
          </ul>
        </>
      ) : null}

      {handoffs.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Handoffs</h4>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>Deliverable</th>
                  <th style={{ padding: "6px 8px" }}>Receiving phase / owner</th>
                </tr>
              </thead>
              <tbody>
                {handoffs.map((h, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(h.item || "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(h.receiving || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {canAct && (
        <div className="card-actions">
          <button type="button" className="btn btn-primary" onClick={() => onAction("approve")}>
            Approve architecture
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => onAction("reject")}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
